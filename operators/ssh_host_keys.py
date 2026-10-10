"""One bounded SSH host identity reconciliation pass. No daemon or API scheduler."""
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from models import json_object, json_string
from server_operation import reservation

from common import (API, OperatorError, alias, address, capture_inventory, fingerprint,
                    inventory_hosts, public_key, request_json, ssh_args, ssh_probe, write_private,
                    run_ansible, management_address)


def current(api, snapshot):
    """Reread identity, ownership and trust before delivering keys or reporting status."""
    server = api.get("servers", snapshot["metadata"]["name"])
    metadata = server["metadata"]
    expected_metadata = snapshot["metadata"]
    status = server.get("status", {})
    expected_status = snapshot.get("status", {})
    if metadata.get("deletionTimestamp"):
        raise RuntimeError("Server is terminating; retry next pass")
    if status.get("provisioning", {}).get("maintenance"):
        raise RuntimeError("Provisioning owns the Server; retry next pass")
    for field in ("uid", "generation"):
        if metadata[field] != expected_metadata[field]:
            raise RuntimeError("Server " + field + " changed; retry next pass")
    for field in ("operation", "machineRef", "desiredSSHTrustBundleDigest"):
        if status.get(field) != expected_status.get(field):
            raise RuntimeError("Server " + field + " changed; retry next pass")
    if status.get("networking", {}).get("management") != expected_status.get("networking",
                                                                             {}).get("management"):
        raise RuntimeError("Management binding changed; retry next pass")
    observed_host = status.get("hostSSH", {})
    expected_host = expected_status.get("hostSSH", {})
    for field in ("keyPairRef", "publicKey", "fingerprint", "keyReady", "bootstrapPublicKey",
                  "installedKeyPairRef", "installedFingerprint"):
        if observed_host.get(field) != expected_host.get(field):
            raise RuntimeError("Host SSH " + field + " changed; retry next pass")
    return server


def observation(api, snapshot, fields, expected_pin=None, additional_status=None):
    """CAS-merge verified observations while preserving unrelated status writers."""
    for _ in range(5):
        latest = current(api, snapshot)
        if expected_pin is not None and latest.get("status", {}).get(
                "hostSSH", {}).get("bootstrapPublicKey") != expected_pin:
            raise RuntimeError("Bootstrap enrollment changed; refusing stale transition")
        try:
            return api.patch_status(latest, {"hostSSH": fields, **(additional_status or {})})
        except HTTPError as exc:
            if exc.code != 409:
                raise
    raise RuntimeError("Concurrent Server status updates; retry next pass")


def pin_first_contact(api, server, host, directory):
    """Persist a Server-bound TOFU pin before delivering private host material."""
    known = directory / "first-contact-known-hosts"
    write_private(known, "")
    # No private host material has been read yet. A successful SSH login pins
    # the key on the same connection, rather than trusting a discarded scan.
    ssh_probe(server, host, known, "accept-new")
    entries = []
    for line in known.read_text().splitlines():
        if line and not line.startswith("#"):
            fields = line.split()
            if len(fields) != 3 or fields[0] != alias(server):
                raise RuntimeError("Unexpected bootstrap known_hosts entry")
            entries.append(public_key(" ".join(fields[1:])))
    if len(entries) != 1:
        raise RuntimeError("Expected exactly one first-contact host key")
    latest = current(api, server)
    trust = latest.get("status", {}).get("hostSSH", {})
    if trust.get("bootstrapPublicKey") or trust.get("installedKeyPairRef"):
        raise RuntimeError("Another build enrolled this Server; retry using its persisted identity")
    # A conflicting pin write MUST stop, not overwrite the winner or retry TOFU.
    return api.patch_status(
        latest, {
            "hostSSH": {
                "bootstrapPublicKey": entries[0],
                "phase": "BootstrapPinned",
                "reason": "FirstContactRecorded",
                "message": "First-contact host key recorded using TOFU"
            }
        })


def host_private_key(api, server):
    """Validate ownership before reading one host key with a short-lived Bao token."""
    host = server["status"]["hostSSH"]
    reference = host["keyPairRef"]
    key = api.get("ssh-key-pairs", reference["name"])
    uid = server["metadata"]["uid"]
    if (key["metadata"]["uid"] != reference["uid"] or key["metadata"].get("deletionTimestamp")
            or key["metadata"].get("annotations", {}).get("homelab.io/server-uid") != uid
            or key["spec"]["path"] != "ssh/hosts/" + uid):
        raise RuntimeError("Host key ownership does not match the Server")
    if (key.get("status", {}).get("phase") != "Ready"
            or key["status"].get("observedGeneration") != key["metadata"]["generation"]
            or public_key(key["status"]["publicKey"]) != public_key(host["publicKey"])):
        raise RuntimeError("Owned host key is not current and Ready")
    if key["spec"]["secretStoreRef"]["name"] != os.environ.get("HOST_KEY_SECRET_STORE", "openbao"):
        raise RuntimeError("Host key belongs to a different SecretStore")
    base = os.environ["BAO_ADDR"].rstrip("/")
    login = request_json(
        base + "/v1/auth/approle/login", "", "POST", {
            "role_id": os.environ["HOST_KEY_BAO_ROLE_ID"],
            "secret_id": os.environ["HOST_KEY_BAO_SECRET_ID"]
        })
    auth = json_object(login.get('auth'), 'OpenBao auth')
    token = json_string(auth.get('client_token'), 'OpenBao client_token')
    try:
        response = request_json(base + "/v1/kv2/data/secrets/ssh/hosts/" + uid, token,
                                token_header="X-Vault-Token")
        version = json_object(response.get('data'), 'OpenBao version')
        data = json_object(version.get('data'), 'OpenBao key data')
        if data.get("sshKeyPairUID") != reference["uid"] or public_key(
                json_string(data.get('publicKey'), 'OpenBao publicKey')) != public_key(
                    host["publicKey"]):
            raise RuntimeError("OpenBao host identity does not match the owned SSHKeyPair")
        return json_string(data.get('privateKey'), 'OpenBao privateKey')
    finally:
        request_json(base + "/v1/auth/token/revoke-self", token, "POST", {},
                     token_header="X-Vault-Token")


def run_play(server, host, directory, known, play, variables=None):
    """Execute the reviewed host-key/trust play with a private scoped inventory."""
    inventory = {
        "all": {
            "hosts": {
                server["metadata"]["name"]: {
                    "ansible_host": address(host),
                    "ansible_user": "ansible",
                    "ansible_become": True,
                    "host_key_alias": alias(server),
                    "ansible_ssh_args": shlex.join(ssh_args(server, known)),
                    "ansible_ssh_common_args": "",
                    "ansible_ssh_extra_args": ""
                }
            }
        }
    }
    write_private(directory / "inventory.json", json.dumps(inventory))
    write_private(directory / "variables.json", json.dumps(variables or {}))
    diagnostic = Path('/tmp/ssh-host-operator-diagnostics') / (server['metadata']['uid'] + '-' +
                                                               play + '.log')
    result = run_ansible([
        "ansible-playbook", "-i",
        str(directory / "inventory.json"),
        str(Path(os.environ.get("ANSIBLE_PLAYS_PATH", "/homelab/plays")) / play), "-e",
        "@" + str(directory / "variables.json")
    ], diagnostic, timeout=300)
    if result.returncode:
        tasks = re.findall(r'TASK \[([^\]\r\n]+)\]', result.stdout)
        last = tasks[-1] if tasks else 'initialization'
        raise OperatorError(
            'SSHReconciliationFailed', 'Ansible reconciliation failed at ' + last +
            ' (secret-bearing output kept in private diagnostics)')


def reconcile(api, server, host, directory):
    """Acquire short-operation ownership; defer busy Servers to the next pass."""
    try:
        with reservation(api, server):
            reconcile_owned(api, server, host, directory)
    except OperatorError as exc:
        if exc.reason != 'OperationBusy':
            raise
        print(server['metadata']['name'] + ': mutation ownership busy; retry next pass')


def reconcile_owned(api, server, host, directory):
    """Inspect, install and verify managed trust while this pass holds ownership."""
    provisioning = server.get("status", {}).get("provisioning", {})
    if provisioning.get("maintenance") or provisioning.get("phase") in (
            "PreparingBoot", "AwaitingLive", "Installing", "AwaitingInstalled", "Verifying"):
        print(server["metadata"]["name"] + ": provisioning owns the SSH transition")
        return
    trust = server.get("status", {}).get("hostSSH", {})
    if not trust.get("keyReady") or not trust.get("keyPairRef"):
        print(server["metadata"]["name"] + ": managed key pending")
        return
    if not server.get("status", {}).get("desiredSSHTrustBundleDigest") or not server.get(
            "status", {}).get("sshTrust", {}).get("publicBundle"):
        print(server["metadata"]["name"] + ": management user-CA trust pending")
        return
    if fingerprint(trust["publicKey"]) != trust.get("fingerprint"):
        raise RuntimeError("Desired public host identity is inconsistent")
    if trust.get("installedKeyPairRef") and (trust["installedKeyPairRef"] != trust["keyPairRef"]
                                             or trust.get("installedFingerprint")
                                             != trust["fingerprint"]):
        raise RuntimeError("Unapproved managed host identity replacement")
    if not trust.get("bootstrapPublicKey") and not trust.get("installedKeyPairRef"):
        # Preserve the accepted pin in the caller's snapshot too, so a later
        # failure report cannot overwrite another enrollment/reset.
        server.update(pin_first_contact(api, server, host, directory))
        trust = server["status"]["hostSSH"]
    pin = trust.get("bootstrapPublicKey")
    known = directory / "known_hosts"
    # Established Servers never fall back to TOFU/bootstrap keys, even on drift.
    keys = [public_key(trust["publicKey"])]
    if not trust.get("installedKeyPairRef") and pin:
        keys.append(public_key(pin))
    write_private(known, "".join(alias(server) + " " + key + "\n" for key in dict.fromkeys(keys)))
    ssh_probe(server, host, known)
    server = current(api, server)
    private = host_private_key(api, server)
    write_private(directory / "host_key", private)
    derived = subprocess.run(
        ["ssh-keygen", "-y", "-f", str(directory / "host_key")], capture_output=True, text=True,
        check=True, timeout=15).stdout
    if public_key(derived) != public_key(trust["publicKey"]):
        raise RuntimeError("Host private key does not match desired public identity")
    # Reread immediately before privileged mutation, after secret retrieval.
    current(api, server)
    run_play(
        server, host, directory, known, "ssh_host_keys.yml", {
            "managed_host_private_key_file": str(directory / "host_key"),
            "managed_host_public_key": public_key(trust["publicKey"])
        })
    write_private(known, alias(server) + " " + public_key(trust["publicKey"]) + "\n")
    ssh_probe(server, host, known)
    # This also reconciles the user-CA trust bundle, replacing the scheduled
    # ssh-trust CommandsPipeline without losing that existing behavior.
    run_play(
        server, host, directory, known, "ssh_trust.yml", {
            "ssh_ca_bundle": server["status"]["sshTrust"]["publicBundle"],
            "ssh_ca_bundle_digest": server["status"]["desiredSSHTrustBundleDigest"]
        })
    ssh_probe(server, host, known)
    observation(
        api, server, {
            "bootstrapPublicKey": None,
            "installedKeyPairRef": trust["keyPairRef"],
            "installedFingerprint": trust["fingerprint"],
            "observedGeneration": server["metadata"]["generation"],
            "phase": "Ready",
            "reason": "ManagedHostKeyVerified",
            "message": "Fresh SSH connection verified managed host identity"
        }, pin, {"installedSSHTrustBundleDigest": server["status"]["desiredSSHTrustBundleDigest"]})


def main():
    """Run a bounded pass; isolate target failures without exposing credentials."""
    api = API()
    group, inventory = capture_inventory(api, os.environ["INVENTORY_CAPTURE_GROUP"])
    kinds = group["spec"]["selector"].get("matchKinds", [])
    if kinds != [{"apiVersion": "homelab.io/v1alpha1", "kind": "Server"}]:
        raise RuntimeError("Host-key operator requires a Server-only capture group")
    failed = False
    for omitted in group.get("status", {}).get("omittedResources", []):
        print(omitted["name"] + ": pending (" + omitted["reason"] + ")")
    for name, variables in inventory_hosts(inventory).items():
        server = None
        try:
            server = api.get("servers", name)
            if server["metadata"].get("deletionTimestamp"):
                continue
            host = address(variables["ansible_host"])
            actual = management_address(server)
            if actual != host:
                raise RuntimeError("Capture-group management address is stale")
            with tempfile.TemporaryDirectory(prefix="ssh-host-operator-") as tmp:
                reconcile(api, server, host, Path(tmp))
            print(name + ": reconciliation pass complete")
        except Exception as exc:
            failed = True
            # Do not print exceptions that may include HTTP bodies/key material.
            reason = exc.reason if isinstance(exc, OperatorError) else "ReconciliationFailed"
            if isinstance(exc, OperatorError):
                message = str(exc)
            else:
                message = ("Operator could not verify convergence; "
                           "inspect connectivity, binding, credentials and host identity")
            print(name + ": " + reason + ": " + message, file=sys.stderr)
            if server:
                try:
                    observation(api, server, {
                        "phase": "Failed",
                        "reason": reason,
                        "message": message
                    })
                except Exception:
                    pass
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("Host-key operator initialization failed", file=sys.stderr)
        sys.exit(1)
