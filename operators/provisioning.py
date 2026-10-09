"""One bounded, checkpointed provisioning pass using the existing Ansible roles."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from common import (API, NoRedirect, OperatorError, address, alias, capture_inventory, fingerprint,
                    resolved_inventory_hosts, public_key, ssh_args, ssh_probe, write_private,
                    run_ansible)
from ssh_host_keys import host_private_key

ACTIVE = {"PreparingBoot", "AwaitingLive", "Installing", "AwaitingInstalled", "Verifying"}
SPEC_INPUTS = ("machineSelector", "boot", "operatingSystem", "sshCertificateAuthorityRef", "users",
               "groups", "packages", "sysctls", "featureFlags", "networking", "hostName",
               "domainName")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def eligible(server):
    return bool(server.get("status", {}).get("provisioning", {}).get("activeRunRef"))


def bind_run(api, server):
    """Local execution view. Stored Server status contains only a reservation."""
    ref = server['status']['provisioning']['activeRunRef']
    run = api.get('provisioning-runs', ref['name'])
    expected = {'name': server['metadata']['name'], 'uid': server['metadata']['uid']}
    if (run['metadata']['uid'] != ref['uid'] or run['metadata'].get('deletionTimestamp')
            or run['spec']['serverRef'] != expected
            or run['spec']['machineRef'] != server['status'].get('machineRef')):
        raise OperatorError('ProvisioningBlocked', 'Run or machine binding changed')
    value = copy.deepcopy(server)
    value['_run'] = run
    value['status']['provisioning'] = copy.deepcopy(run['status'])
    return value


def release_run(api, server):
    """Recover a lost completion/release boundary without rerunning installation."""
    run = api.get('provisioning-runs', server['_run']['metadata']['name'])
    if (run['metadata']['uid'] != server['_run']['metadata']['uid']
            or run['status'].get('maintenance') or run['status'].get('netbootArmed')
            or run['status']['phase'] not in ('Succeeded', 'Blocked')):
        return
    for _ in range(5):
        latest = api.get('servers', server['metadata']['name'])
        ref = {'name': run['metadata']['name'], 'uid': run['metadata']['uid']}
        if (latest['metadata']['uid'] != server['metadata']['uid']
                or latest['status'].get('provisioning', {}).get('activeRunRef') != ref):
            raise OperatorError('ProvisioningBlocked', 'Run lost its Server reservation')
        fields = {'activeRunRef': None, 'maintenance': False}
        if run['status']['phase'] == 'Succeeded':
            fields.update(provisioned=True, lastSuccessfulRunRef=ref)
        try:
            api.patch_status(latest, {'provisioning': fields})
            return
        except HTTPError as exc:
            if exc.code != 409:
                raise
    raise OperatorError('ProvisioningBlocked', 'Concurrent reservation release')


def identity(server):
    status, spec = server["status"], server["spec"]
    host = status.get("hostSSH", {})
    if (not status.get("machineRef") or not host.get("keyReady") or host.get("phase") != "Ready"
            or host.get("installedKeyPairRef") != host.get("keyPairRef")
            or fingerprint(host["publicKey"]) != host.get("installedFingerprint")):
        raise OperatorError(
            "ProvisioningBlocked",
            "Managed host identity and binding must be verified before provisioning")
    os_spec = spec.get("operatingSystem", {})
    if (os_spec.get("architecture") != "amd64" or os_spec.get("bootMode") != "uefi"
            or (os_spec.get("distribution"), os_spec.get("version")) not in (("arch", "rolling"),
                                                                             ("debian", "trixie"))):
        raise OperatorError("ProvisioningBlocked",
                            "Supported targets are Arch rolling and Debian trixie on amd64 UEFI")


def current(api, snapshot, allow_paused=False):
    value = bind_run(api, api.get("servers", snapshot["metadata"]["name"]))
    p = snapshot["status"]["provisioning"]
    actual = value.get("status", {}).get("provisioning", {})
    if (value["metadata"]["uid"] != snapshot["metadata"]["uid"]
            or value["metadata"].get("deletionTimestamp")
            or actual.get("attemptID") != p["attemptID"] or actual.get("snapshot") != p["snapshot"]
            or actual.get("phase") != p.get("phase")
            or any(value["spec"].get(field) != p["snapshot"]["inputs"]["serverSpec"].get(field)
                   for field in SPEC_INPUTS)
            or value['_run']['metadata']['uid'] != snapshot['_run']['metadata']['uid']
            or value['_run']['spec'] != snapshot['_run']['spec']
            or value["status"].get("machineRef") != p["snapshot"]["machineRef"]
            or value["status"].get("networking", {}).get("management", {}).get("interface", {}).get(
                "mac", '').lower() != p["snapshot"]["bootMAC"]
            or value["status"].get("hostSSH", {}).get("keyPairRef") != p["snapshot"]["keyPairRef"]):
        raise OperatorError("ProvisioningBlocked",
                            "Attempt, desired inputs or machine/key binding changed")
    if not allow_paused and (not value["spec"]["provisioning"]["enabled"]
                             or value["spec"].get("reconciliation", {}).get("paused")):
        raise OperatorError("ProvisioningBlocked", "Provisioning was disabled or paused")
    return value


def checkpoint(api, server, phase, **fields):
    for _ in range(5):
        latest = current(api, server, allow_paused=True)
        try:
            result = api.patch_status(latest['_run'], {
                "phase": phase,
                "currentStage": phase,
                **fields
            })
            latest['_run'] = result
            latest['status']['provisioning'] = copy.deepcopy(result['status'])
            server.clear()
            server.update(latest)
            print(server['metadata']['name'] + ': checkpoint ' + phase, flush=True)
            return server
        except HTTPError as exc:
            if exc.code != 409:
                raise
    raise OperatorError("ProvisioningBlocked", "Concurrent provisioning status update")


def known_file(server, directory, key=None):
    path = directory / "known_hosts"
    write_private(
        path,
        alias(server) + " " + public_key(key or server["status"]["hostSSH"]["publicKey"]) + "\n")
    return path


def host_address(server):
    return address(server["status"]["networking"]["management"]["address"]["address"])


def inspect(server, known):
    script = (Path(__file__).resolve().parents[1] / "roles/provision/files/probe.py").read_text()
    result = subprocess.run([
        "ssh", *ssh_args(server, known), "ansible@" + host_address(server), "sudo -n python3 - " +
        shlex.quote(json.dumps(server['status']['provisioning']['selectedDisk']))
    ], input=script, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise OperatorError("ProvisioningBlocked", "Read-only SSH provisioning probe failed")
    return json.loads(result.stdout)


def validate_disk(facts, live_required=False):
    if not facts["uefi"] or facts["secureBoot"] is not False:
        raise OperatorError(
            "ProvisioningBlocked",
            "Unsigned v1 GRUB/iPXE path requires UEFI with Secure Boot off; do not change firmware automatically"
        )
    if live_required and (not facts["live"] or facts["mounted"] or facts['rootOnTarget']):
        raise OperatorError(
            "ProvisioningBlocked",
            "Installation requires live execution independent of the unmounted target disk")
    if facts.get('firmwareBootNext') or facts.get('competingBootServices'):
        raise OperatorError(
            "ProvisioningBlocked",
            'Clear/reconcile competing firmware BootNext services before provisioning')


def plan(api, server, variables, facts, revision):
    identity(server)
    target = facts['targetDisk']
    if facts['disk'] != server['status']['provisioning']['selectedDisk']:
        raise OperatorError('ProvisioningBlocked', 'Selected disk identity changed since request')
    desired, status = server["spec"], server["status"]
    image = api.get("isos", desired["boot"]["isoRef"]["name"])
    image_status = image.get("status", {})
    if (image["metadata"].get("deletionTimestamp") or image_status.get("phase") != "Ready"
            or image_status.get("observedGeneration") != image["metadata"]["generation"]
            or status.get("bootISORef") != {
                "name": image["metadata"]["name"],
                "uid": image["metadata"]["uid"]
            }):
        raise OperatorError("ProvisioningBlocked", "Selected ISO identity is not current and Ready")
    authority = api.get("ssh-certificate-authorities",
                        desired["sshCertificateAuthorityRef"]["name"])
    ca = authority.get("status", {})
    if (authority["metadata"].get("deletionTimestamp") or ca.get("phase") != "Ready"
            or ca.get("observedGeneration") != authority["metadata"]["generation"]
            or status.get("sshTrust", {}).get("authorityRef") != image_status.get("authorityRef")
            or image_status.get("authorityRef", {}).get("uid") != authority["metadata"]["uid"]
            or ca.get("trustBundleDigest") != image_status.get("completedBuild",
                                                               {}).get("trustBundleDigest")
            or status.get("desiredSSHTrustBundleDigest") != ca.get("trustBundleDigest")):
        raise OperatorError("ProvisioningBlocked", "Live-image and Server CA trust must agree")
    machine = api.get("machines", status["machineRef"]["name"])
    if machine["metadata"]["uid"] != status["machineRef"]["uid"] or machine.get(
            "status", {}).get("serverRef") != {
                "name": server["metadata"]["name"],
                "uid": server["metadata"]["uid"]
            }:
        raise OperatorError("ProvisioningBlocked", "Machine binding is stale")
    build_id = image_status["completedBuild"]["id"]
    boot_arguments = image_status.get('bootArguments')
    if not boot_arguments or any(not isinstance(arg, str) or any(c.isspace()
                                                                 for c in arg) or '$' in arg
                                 for arg in boot_arguments):
        raise OperatorError('ProvisioningBlocked', 'Selected ISO has no valid shared boot recipe')
    if desired["operatingSystem"]["distribution"] == "arch" and image["spec"][
            "distribution"] != "arch":
        raise OperatorError("ProvisioningBlocked",
                            "Arch targets currently require the Arch live installation tools")
    storage = {
        "partitions": [{
            field: part[field]
            for field in ('fs_type', 'alloc_type', 'size', 'flags') if field in part
        } for part in variables.get("storage", {}).get("partitions", [])]
    }
    if [part.get("fs_type") for part in storage.get("partitions", [])] != ["efi", "swap", "ext4"]:
        raise OperatorError(
            "ProvisioningBlocked",
            "v1 requires an explicit EFI/swap/ext4-root layout in capture-group variables")
    for index, part in enumerate(storage["partitions"]):
        if (part.get("alloc_type") != ("percentage" if index == 2 else "size")
                or int(part.get("size", 0)) <= 0 or index == 2 and int(part["size"]) != 100):
            raise OperatorError("ProvisioningBlocked", "Invalid v1 partition sizing")
    if int(storage['partitions'][0]['size']) < 260 or (sum(
            int(part['size'])
            for part in storage['partitions'][:2]) + 1024) * 1024 * 1024 > facts['disk']['size']:
        raise OperatorError("ProvisioningBlocked", 'EFI/root sizing does not fit the approved disk')
    os_spec = desired['operatingSystem']
    if os_spec.get('installationSource') or os_spec.get('locale', 'en_US.UTF-8') != 'en_US.UTF-8':
        raise OperatorError(
            "ProvisioningBlocked",
            'Custom installation sources/locales are not supported by the current recipes')
    if not re.fullmatch(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*', os_spec.get('timezone', 'UTC')):
        raise OperatorError("ProvisioningBlocked", 'Invalid timezone')
    if any(not re.fullmatch(r'[A-Za-z0-9_.,=:/+-]+', arg)
           for arg in os_spec.get('kernelArguments', [])):
        raise OperatorError("ProvisioningBlocked", 'Invalid kernel arguments')
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', desired[field])
           for field in ('hostName', 'domainName') if field in desired):
        raise OperatorError("ProvisioningBlocked", 'Invalid installed hostname/domain')
    if any(not re.fullmatch(r'[A-Za-z0-9_.+-]+', package)
           for package in desired.get('packages', [])):
        raise OperatorError("ProvisioningBlocked", 'Invalid package name')
    if any(not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', group)
           for group in desired.get('groups', [])):
        raise OperatorError("ProvisioningBlocked", 'Invalid group name')
    if any(not re.fullmatch(r'[A-Za-z0-9_.]+', key) or '\n' in value or '\r' in value
           for key, value in desired.get('sysctls', {}).items()):
        raise OperatorError("ProvisioningBlocked", 'Invalid sysctl configuration')
    for feature, settings in desired.get('featureFlags', {}).items():
        if feature == 'daemon' and not settings.get(
                'enabled') or feature != 'daemon' and settings.get('enabled'):
            raise OperatorError(
                "ProvisioningBlocked",
                'Requested feature requires an installation recipe not yet supported here')
    users = copy.deepcopy(desired.get("users", []))
    for user in users:
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}",
                            user["name"]) or user["name"] in ("root", "ansible", "homelabd"):
            raise OperatorError("ProvisioningBlocked", "Invalid/reserved ordinary user")
        if any(not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', group)
               for group in user.get('groups', [])) or not re.fullmatch(
                   r'/[A-Za-z0-9_/-]+', user.get('shell', '/bin/bash')):
            raise OperatorError("ProvisioningBlocked", 'Invalid user groups/shell')
        keys = []
        for ref in user.get("ssh", {}).get("authorizedKeyRefs", []):
            key = api.get("ssh-key-pairs", ref["name"])
            if key.get("status", {}).get("phase") != "Ready" or key["status"].get(
                    "observedGeneration") != key["metadata"]["generation"]:
                raise OperatorError("ProvisioningBlocked", "User public key is not Ready")
            keys.append(public_key(key["status"]["publicKey"]))
        user["publicKeys"] = keys
    result = {
        "serverUID": server["metadata"]["uid"],
        "machineRef": status["machineRef"],
        "keyPairRef": status["hostSSH"]["keyPairRef"],
        "isoRef": status["bootISORef"],
        "authorityRef": image_status["authorityRef"],
        "trustBundleDigest": ca["trustBundleDigest"],
        "isoBuildID": build_id,
        "distribution": image["spec"]["distribution"],
        "bootArguments": boot_arguments,
        "artifacts": image_status["artifacts"],
        "targetDisk": target,
        "diskIdentity": facts["disk"],
        "bootMAC": status['networking']['management']['interface']['mac'].lower(),
        "codeRevision": revision,
        "inputs": {
            "serverSpec": {
                field: desired[field]
                for field in SPEC_INPUTS if field in desired
            },
            "storage": storage,
            "users": users,
            "disableEEE": bool(variables.get("provision_disable_eee", False))
        }
    }
    result["planDigest"] = digest(result)
    return result


def verify_dependencies(api, server):
    p = server["status"]["provisioning"]["snapshot"]
    for collection, field in (("machines", "machineRef"), ("isos", "isoRef"),
                              ("ssh-key-pairs", "keyPairRef"), ("ssh-certificate-authorities",
                                                                "authorityRef")):
        dependency = api.get(collection, p[field]["name"])
        if dependency["metadata"].get(
                "deletionTimestamp") or dependency["metadata"]["uid"] != p[field]["uid"]:
            raise OperatorError("ProvisioningBlocked", "Pinned dependency was deleted/replaced")
    if server["status"].get("desiredSSHTrustBundleDigest") != p["trustBundleDigest"]:
        raise OperatorError("ProvisioningBlocked",
                            "CA trust changed during this attempt; do not install a mixed plan")


def drain_commands(api, timeout=300):
    deadline = time.monotonic() + timeout
    while True:
        busy = [
            item for item in api.list("commands")
            if item.get("status", {}).get("phase") in ("Dispatching", "Running")
        ]
        if not busy:
            return
        if time.monotonic() >= deadline:
            raise OperatorError("ProvisioningBlocked",
                                "In-flight administrative builds did not drain")
        time.sleep(5)


def artifact_preflight(api, server):
    p = server["status"]["provisioning"]["snapshot"]
    for artifact in p["artifacts"]:
        if not artifact["url"].startswith("https://"):
            raise OperatorError("ProvisioningBlocked", "Live artifacts require HTTPS")
        with build_opener(NoRedirect()).open(Request(artifact["url"], method="HEAD"),
                                             timeout=30) as response:
            if response.status != 200:
                raise OperatorError("ProvisioningBlocked", "Pinned artifact is not reachable")
    mac = server["status"]["networking"]["management"]["interface"]["mac"].lower()
    url = os.environ["STIGMERGY_API_URL"].rstrip("/") + "/ipxe/" + mac
    with build_opener(NoRedirect()).open(url, timeout=30) as response:
        script = response.read(65536).decode()
    if "exit 1" in script or any(artifact["url"] not in script for artifact in p["artifacts"]
                                 if artifact["type"] in ("kernel", "initrd")):
        raise OperatorError("ProvisioningBlocked",
                            "Boot endpoint did not select the pinned live image")


def failed_ansible_task(output):
    """Keep the failure label even when an always cleanup task follows."""
    blocks = re.split(r'TASK \[([^\]\r\n]+)\][^\r\n]*', output)
    failures = [
        blocks[index] for index in range(1, len(blocks), 2)
        if re.search(r'(?m)^fatal: .*?(FAILED!|UNREACHABLE!)', blocks[index + 1])
    ]
    return failures[-1] if failures else 'initialization (see playbook output)'


def run_stage(server, directory, known, stage):
    p = server["status"]["provisioning"]
    snapshot = p["snapshot"]
    inventory = {
        "all": {
            "hosts": {
                server["metadata"]["name"]: {
                    "ansible_host": host_address(server),
                    "ansible_user": "ansible",
                    "ansible_become": True,
                    "ansible_ssh_args": shlex.join(ssh_args(server, known)),
                    "ansible_ssh_common_args": "",
                    "ansible_ssh_extra_args": ""
                }
            }
        }
    }
    variables = {
        "provision_stage": stage,
        "provision_attempt_id": p["attemptID"],
        "provision_server_uid": server["metadata"]["uid"],
        "provision_target_disk": snapshot["targetDisk"],
        "provision_run_uid": server['_run']['metadata']['uid'],
        "provision_plan_digest": snapshot["planDigest"],
        "provision_operating_system": snapshot["inputs"]["serverSpec"]["operatingSystem"],
        "provision_server_spec": snapshot["inputs"]["serverSpec"],
        "storage": snapshot["inputs"]["storage"],
        "provision_users": snapshot["inputs"]["users"],
        "provision_disable_eee": snapshot["inputs"]["disableEEE"],
        "provision_boot_mac": snapshot['bootMAC'],
        "provision_disk_identity": snapshot["diskIdentity"],
        "provision_live_build_id": snapshot["isoBuildID"],
        "provision_live_boot_id": p.get("liveBootID", ""),
        "provision_destructive_authorized": stage == "install",
        "ssh_ca_bundle": server["status"]["sshTrust"]["publicBundle"],
        "ssh_ca_bundle_digest": snapshot["trustBundleDigest"]
    }
    variables['provision_live_artifacts'] = snapshot['artifacts']
    variables['provision_live_boot_arguments'] = [
        arg.replace('@BOOT_MAC@', snapshot['bootMAC'].replace(':', '-'))
        for arg in snapshot.get('bootArguments', [])
    ]
    variables['provision_source_boot_id'] = p['sourceBootID']
    write_private(directory / "inventory.json", json.dumps(inventory))
    write_private(directory / "variables.json", json.dumps(variables))
    plays = Path(os.environ.get("ANSIBLE_PLAYS_PATH", "/homelab/plays"))
    print(server['metadata']['name'] + ': starting Ansible stage ' + stage, flush=True)
    diagnostic = Path('/tmp/provision-operator-diagnostics') / (p['attemptID'] + '-' + stage +
                                                                '.log')
    result = run_ansible([
        "ansible-playbook", "-i",
        str(directory / "inventory.json"),
        str(plays / "provision_stage.yml"), "-e", "@" + str(directory / "variables.json")
    ], diagnostic, timeout=7200 if stage in ("install", "repair") else 600)
    if result.returncode:
        # Private diagnostics remain in the failed task container, not build logs.
        last = failed_ansible_task(result.stdout)
        raise OperatorError(
            "ProvisioningBlocked", "Ansible stage failed at " + last +
            "; see playbook output (credential tasks remain redacted)")
    print(server['metadata']['name'] + ': completed Ansible stage ' + stage, flush=True)


def reboot(server, known):
    run_stage(server, known.parent, known, 'reboot')
    # An async reboot acknowledgement is not success; fresh-session verification decides.


def await_session(api, server, directory, live, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        server.update(current(api, server, allow_paused=not live))
        p = server["status"]["provisioning"]
        pin = p.get("bootstrapPublicKey") if live else None
        known = known_file(server, directory, pin)
        try:
            facts = inspect(server, known)
        except Exception:
            if live and not pin:
                # Scoped first contact, before reading any host private key.
                known = directory / "live-first-contact"
                write_private(known, "")
                try:
                    ssh_probe(server, host_address(server), known, "accept-new")
                    lines = known.read_text().splitlines()
                    if len(lines) != 1 or lines[0].split()[0] != alias(server):
                        raise OperatorError("ProvisioningBlocked",
                                            "Invalid attempt-scoped TOFU pin")
                    pin = public_key(" ".join(lines[0].split()[1:]))
                    facts = inspect(server, known)
                    if not facts["live"] or facts["liveBuildID"] != p["snapshot"][
                            "isoBuildID"] or facts["bootID"] == p.get("sourceBootID"):
                        raise OperatorError("ProvisioningBlocked",
                                            "Fresh contact is not the intended live session")
                    checkpoint(api, server, "AwaitingLive", bootstrapPublicKey=pin)
                except Exception:
                    time.sleep(5)
                    continue
            else:
                time.sleep(5)
                continue
        source = p.get("sourceBootID") if live else p.get("liveBootID")
        if facts["live"] == live and facts["bootID"] != source:
            if live and facts["liveBuildID"] != p["snapshot"]["isoBuildID"]:
                raise OperatorError("ProvisioningBlocked", "Booted the wrong immutable live image")
            return facts, known
        time.sleep(5)
    raise OperatorError("ProvisioningBlocked",
                        "Timed out waiting for the intended changed boot session")


def enroll_live(api, server, facts, known, directory):
    # Identity/build checks precede host-private-key delivery. TOFU is explicitly
    # scoped to this attempt and carries the documented v1 impersonation risk.
    from ssh_host_keys import run_play
    snapshot = server["status"]["provisioning"]["snapshot"]
    validate_disk(facts, live_required=True)
    if facts["disk"] != snapshot["diskIdentity"]:
        raise OperatorError("ProvisioningBlocked",
                            "Live disk identity differs from the approved plan")
    current(api, server)
    private = host_private_key(api, server)
    write_private(directory / "host_key", private)
    derived = subprocess.check_output(
        ["ssh-keygen", "-y", "-f", str(directory / "host_key")], text=True, timeout=15)
    if public_key(derived) != public_key(server["status"]["hostSSH"]["publicKey"]):
        raise OperatorError("ProvisioningBlocked", "Managed host private/public identity mismatch")
    current(api, server)
    # The play reloads sshd before its cleanup tasks. A reconnect must accept
    # either the recorded live-bootstrap key or the verified managed key during
    # this one handoff; it must never fall back to unpinned first contact.
    managed_entry = alias(server) + " " + public_key(derived) + "\n"
    if managed_entry not in known.read_text().splitlines(keepends=True):
        write_private(known, known.read_text() + managed_entry)
    run_play(
        server, host_address(server), directory, known, "ssh_host_keys.yml", {
            "managed_host_private_key_file": str(directory / "host_key"),
            "managed_host_public_key": public_key(derived)
        })
    known = known_file(server, directory)
    ssh_probe(server, host_address(server), known)
    checkpoint(api, server, "AwaitingLive", liveBootID=facts["bootID"], bootstrapPublicKey=None,
               netbootArmed=False)
    return known


def verify_installed(server, facts):
    p, marker = server["status"]["provisioning"], facts.get("marker") or {}
    if (facts["live"] or facts["rootType"] != "ext4" or facts["bootID"] == p.get("liveBootID")
            or facts['disk'] != p['snapshot']['diskIdentity']):
        raise OperatorError("ProvisioningBlocked", "Not a fresh installed boot")
    expected = {
        "serverUID": server["metadata"]["uid"],
        "attemptID": p["attemptID"],
        "runUID": server['_run']['metadata']['uid'],
        "planDigest": p["snapshot"]["planDigest"]
    }
    if any(marker.get(k) != v for k, v in expected.items()) or not facts["rootUUID"] or marker.get(
            "rootUUID") != facts["rootUUID"] or not facts.get("rootOnTarget"):
        raise OperatorError("ProvisioningBlocked",
                            "Installed marker/root/disk does not match this attempt")
    target = p["snapshot"]["inputs"]["serverSpec"]["operatingSystem"]
    if facts["os"].get("ID", "").strip('"') != target["distribution"] or (
            target["distribution"] == "debian"
            and facts["os"].get("VERSION_CODENAME", "").strip('"') != target["version"]):
        raise OperatorError("ProvisioningBlocked", "Installed OS differs from requested target")
    if not facts["grubReady"] or "next_entry=" in facts["grubEnvironment"]:
        raise OperatorError("ProvisioningBlocked", "Installed GRUB contract is not restored")


def reconcile(api, server, variables, directory, revision, preflight=False):
    p = server.get("status", {}).get("provisioning", {})
    if p.get('phase') in ('Succeeded', 'Blocked'):
        if p.get('phase') == 'Blocked' and p.get('maintenance'):
            raise OperatorError(
                'ProvisioningBlocked',
                'Blocked run retains maintenance; inspect its failed build before recovery')
        if not preflight:
            release_run(api, server)
        return
    if preflight and p.get("phase") in ACTIVE:
        print(server["metadata"]["name"] +
              ": active attempt; preflight does not resume or mutate it")
        return
    if p.get("phase") == "Installing":
        checkpoint(
            api, server, "Blocked", message=
            "Interrupted Installing: inspect the disk/checkpoint manually; never automatically rewipe",
            maintenance=True)
        return
    if not p.get("attemptID") or p.get("phase") not in ACTIVE:
        if (not server['spec'].get('provisioning', {}).get('enabled')
                or server['spec'].get('reconciliation', {}).get('paused')):
            print(server['metadata']['name'] + ': pending run paused', flush=True)
            return
        identity(server)
        known = known_file(server, directory)
        facts = inspect(server, known)
        # Existing staging mounts/swap are irrelevant until the new live boot.
        validate_disk(facts)
        if facts['live'] and facts['rootOnTarget']:
            raise OperatorError('ProvisioningBlocked', 'Live root depends on the approved disk')
        if not facts["live"] and not facts["grubReady"]:
            raise OperatorError(
                "ProvisioningBlocked",
                "Installed host has no validated GRUB/iPXE path; manual setup required")
        snapshot = plan(api, server, variables, facts, revision)
        if preflight:
            print(server["metadata"]["name"] + ": preflight passed (no claim, mutation or reboot)")
            return
        for other in api.list('servers'):
            if other['metadata']['uid'] == server['metadata']['uid'] or not eligible(other):
                continue
            other_run = bind_run(api, other)['status']['provisioning']
            if other_run.get('phase') != 'Pending' and other_run.get('maintenance'):
                raise OperatorError('ProvisioningBlocked',
                                    'Another run retains lifecycle maintenance; inspect it first')
        # Creation already reserved this Server atomically; claim the immutable run.
        result = api.patch_status(
            server['_run'], {
                "phase": "PreparingBoot",
                "currentStage": "PreparingBoot",
                "maintenance": True,
                "attemptID": server['_run']['metadata']['uid'],
                "snapshot": snapshot,
                "startedAt": now(),
                "completedAt": None,
                "backendRunID": os.environ.get("BUILD_ID", "manual"),
                "observedServerGeneration": server["metadata"]["generation"],
                "sourceBootID": facts["bootID"],
                "liveBootID": None,
                "bootstrapPublicKey": None,
                "netbootArmed": False,
                "bootTarget": "live",
                "message": "Attempt claimed"
            })
        server['_run'] = result
        server['status']['provisioning'] = copy.deepcopy(result['status'])
    elif preflight:
        print(server["metadata"]["name"] +
              ": active attempt; preflight does not resume or mutate it")
        return
    if server["status"]["provisioning"]["snapshot"]["codeRevision"] != revision:
        raise OperatorError("ProvisioningBlocked",
                            "Resume requires the pinned operator-code revision")
    server.update(
        current(api, server,
                allow_paused=server['status']['provisioning']['phase'] == 'AwaitingInstalled'))
    verify_dependencies(api, server)
    drain_commands(api)
    p = server["status"]["provisioning"]
    known = known_file(server, directory)
    if p["phase"] == "PreparingBoot":
        facts = inspect(server, known)
        if facts["disk"] != p["snapshot"]["diskIdentity"] or facts["bootID"] != p["sourceBootID"]:
            raise OperatorError("ProvisioningBlocked",
                                "Unexpected session/disk before boot preparation")
        artifact_preflight(api, server)
        if facts["live"]:
            run_stage(server, directory, known, 'prime')
            checkpoint(api, server, 'PreparingBoot', netbootArmed=True)
            run_stage(server, directory, known, 'refresh-prepare')
            current(api, server)
            checkpoint(api, server, 'AwaitingLive', liveBootID=None, netbootArmed=True)
            current(api, server)
            run_stage(server, directory, known, 'refresh-boot')
        else:
            if "next_entry=" in facts["grubEnvironment"]:
                raise OperatorError("ProvisioningBlocked", "Unaccounted pending GRUB entry")
            run_stage(server, directory, known, "prime")
            checkpoint(api, server, "AwaitingLive", netbootArmed=True)
            current(api, server)
            run_stage(server, directory, known, "arm")
            current(api, server)
            reboot(server, known)
    p = server["status"]["provisioning"]
    if p["phase"] == "AwaitingLive":
        if p.get("liveBootID"):
            facts = inspect(server, known)
            if facts['bootID'] == p['sourceBootID'] or facts["bootID"] != p[
                    "liveBootID"] or not facts["live"] or facts["liveBuildID"] != p["snapshot"][
                        "isoBuildID"]:
                raise OperatorError("ProvisioningBlocked", "Recorded live session changed")
        else:
            facts, known = await_session(api, server, directory, live=True)
        known = enroll_live(api, server, facts, known, directory)
        verify_dependencies(api, server)
        current(api, server)
        checkpoint(api, server, "Installing",
                   message="Destructive stage entered; interrupted runs require manual inspection")
        run_stage(server, directory, known, "install")
        checkpoint(api, server, "AwaitingInstalled", bootTarget="installed", netbootArmed=False)
    p = server["status"]["provisioning"]
    if p["phase"] == "AwaitingInstalled":
        # Recover a lost status/reboot boundary without rerunning installation.
        # A final reboot is safe only from the exact completed live session.
        try:
            before = inspect(server, known)
        except Exception:
            before = None  # The reboot may already be in progress.
        if before and before['live']:
            expected = {
                "serverUID": server["metadata"]["uid"],
                "attemptID": p["attemptID"],
                "runUID": server['_run']['metadata']['uid'],
                "planDigest": p["snapshot"]["planDigest"]
            }
            if (before['bootID'] != p.get('liveBootID') or not before['stagedBootReady'] or any(
                (before.get('stagedMarker') or {}).get(k) != v for k, v in expected.items())):
                raise OperatorError("ProvisioningBlocked",
                                    'Cannot verify a completed staged installation for final boot')
            current(api, server, allow_paused=True)
            reboot(server, known)
        facts, known = await_session(api, server, directory, live=False)
        verify_installed(server, facts)
        checkpoint(api, server, "Verifying")
    if server["status"]["provisioning"]["phase"] == "Verifying":
        facts = inspect(server, known)
        verify_installed(server, facts)
        current(api, server)
        run_stage(server, directory, known, "post")
        # Only attested installed state and healthy services may complete a run.
        result = subprocess.run([
            "ssh", *ssh_args(server, known), "ansible@" + host_address(server),
            "sudo -n systemctl is-active homelabd ansible-account.timer lldpd NetworkManager " +
            ("ssh" if facts["os"]["ID"].strip('"') == "debian" else "sshd")
        ], capture_output=True, timeout=45)
        if result.returncode:
            raise OperatorError("ProvisioningBlocked",
                                "Installed management services are not healthy")
        verify_installed(server, inspect(server, known))
        checkpoint(api, server, "Succeeded", maintenance=False, completedAt=now(),
                   message="Fresh installed boot, marker and management services verified")
        release_run(api, server)


def handle_failure(api, server, directory, failure_message=None):
    p = server.get("status", {}).get("provisioning", {})
    if p.get("phase") == 'Pending':
        result = api.patch_status(
            server['_run'], {
                'message':
                'Preflight pending: inspect dependencies, disk identity and management connectivity'
            })
        server['_run'] = result
        return
    if p.get("phase") not in ACTIVE:
        return
    if p["phase"] in ("AwaitingInstalled", "Verifying"):
        checkpoint(api, server, p["phase"],
                   message="Verification pending; later passes may verify, never rewipe")
        return
    keep = p["phase"] == "Installing"
    if p.get("netbootArmed"):
        try:
            latest = current(api, server, allow_paused=True)
            known = known_file(latest, directory)
            facts = inspect(latest, known)
            if facts["bootID"] != p.get("sourceBootID"):
                keep = True
            elif facts['live']:
                run_stage(latest, directory, known, 'refresh-clear')
            else:
                run_stage(latest, directory, known, "clear")
                if "next_entry=" in inspect(latest, known)["grubEnvironment"]:
                    keep = True
        except Exception:
            keep = True
    checkpoint(
        api, server, "Blocked", maintenance=keep, netbootArmed=bool(keep and p.get("netbootArmed")),
        message=
        (failure_message or
         "Attempt blocked; inspect boot selection/disk before explicitly requesting another attempt"
         ))
    release_run(api, server)


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--preflight", action="store_true")
    options = args.parse_args()
    api = API()
    group, inventory = capture_inventory(api, os.environ["INVENTORY_CAPTURE_GROUP"])
    if group["spec"]["selector"].get("matchKinds") != [{
            "apiVersion": "homelab.io/v1alpha1",
            "kind": "Server"
    }]:
        raise OperatorError("ProvisioningBlocked",
                            "Provisioning requires a Server-only capture group")
    checkout = str(Path(__file__).resolve().parents[1])
    # Concourse's explicit Git input is root-owned; trust only this checkout for
    # this read, never a global wildcard or every repository on the runner.
    revision = subprocess.check_output(
        ["git", "-c", "safe.directory=" + checkout, "-C", checkout, "rev-parse", "HEAD"],
        text=True).strip()
    failed = False
    for name, variables in resolved_inventory_hosts(inventory).items():
        server = api.get("servers", name)
        if not eligible(server):
            continue
        server = bind_run(api, server)
        if host_address(server) != address(variables["ansible_host"]):
            raise OperatorError("ProvisioningBlocked", "Capture-group management address is stale")
        with tempfile.TemporaryDirectory(prefix="provision-operator-") as tmp:
            try:
                reconcile(api, server, variables, Path(tmp), revision, options.preflight)
            except Exception as exc:
                # Never print raw HTTP/Ansible exception bodies containing credentials.
                failed = True
                message = str(exc) if isinstance(
                    exc, OperatorError
                ) else 'Provisioning preflight/attempt blocked; inspect dependencies and checkpoints'
                print(name + ': ' + message, file=sys.stderr)
                if not options.preflight:
                    handle_failure(api, server, Path(tmp), message)
                if server.get('status', {}).get(
                        'provisioning', {}).get('phase') in ACTIVE or server.get('status', {}).get(
                            'provisioning', {}).get('maintenance'):
                    return 1
                continue
        if not options.preflight:
            break  # At most one destructive attempt per bounded pass.
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("Provisioning operator initialization/checkpoint failed", file=sys.stderr)
        sys.exit(1)
