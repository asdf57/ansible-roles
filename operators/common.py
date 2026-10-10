"""Small shared API/SSH primitives for bounded external operators and runners."""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Credential-bearing requests must not redirect")


class OperatorError(RuntimeError):

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def request_json(url, token, method="GET", body=None, headers=None, token_header="Authorization"):
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http"
                                         and parsed.hostname in ("localhost", "127.0.0.1")):
        raise RuntimeError("Credentials require HTTPS (loopback HTTP is development-only)")
    values = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        values[token_header] = ("Bearer " if token_header == "Authorization" else "") + token
    values.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    with build_opener(NoRedirect()).open(Request(url, data=data, headers=values, method=method),
                                         timeout=30) as response:
        data = response.read()
        return json.loads(data) if data else {}


class API:

    def __init__(self):
        self.url = os.environ["STIGMERGY_API_URL"].rstrip("/") + "/api/v1alpha1"
        self.token = os.environ["STIGMERGY_API_TOKEN"].strip()
        if not self.token:
            raise RuntimeError("An API token is required")

    def get(self, collection, name):
        return request_json(self.url + "/" + collection + "/" + quote(name, safe=""), self.token)

    def list(self, collection):
        return request_json(self.url + "/" + collection, self.token)["items"]

    def patch_status(self, server, status):
        collection = {'Server': 'servers', 'ProvisioningRun': 'provisioning-runs'}[server['kind']]
        return request_json(
            self.url + "/" + collection + "/" + quote(server["metadata"]["name"], safe="") +
            "/status", self.token, "PATCH", {
                "metadata": {
                    "uid": server["metadata"]["uid"]
                },
                "status": status
            }, {
                "If-Match": '"' + server["metadata"]["resourceVersion"] + '"',
                "Content-Type": "application/merge-patch+json"
            })


def write_private(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # All callers use private fresh task/temporary directories.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    path.chmod(0o600)


def run_ansible(command, diagnostic, timeout):
    """Stream Ansible's standard, no_log-aware output and keep a private copy."""
    diagnostic = Path(diagnostic)
    write_private(diagnostic, '')
    environment = dict(os.environ)
    environment.update(ANSIBLE_STDOUT_CALLBACK='default', ANSIBLE_NOCOLOR='1',
                       ANSIBLE_DISPLAY_ARGS_TO_STDOUT='false', ANSIBLE_VERBOSITY='0',
                       ANSIBLE_DISPLAY_SKIPPED_HOSTS='false', ANSIBLE_SSH_USETTY='false',
                       ANSIBLE_INJECT_FACT_VARS='false', ANSIBLE_CALLBACK_RESULT_FORMAT='yaml')
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               bufsize=1, env=environment, start_new_session=True)

    def stream():
        with diagnostic.open('a') as log:
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line, end='', flush=True)

    output = threading.Thread(target=stream, daemon=True)
    output.start()
    try:
        result = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    finally:
        output.join(timeout=10)
        process.stdout.close()
    return subprocess.CompletedProcess(command, result, stdout=diagnostic.read_text(), stderr='')


def public_key(value):
    fields = value.strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise RuntimeError("An Ed25519 host key is required")
    raw = base64.b64decode(fields[1], validate=True)
    # Validate both the wire algorithm and the 32-byte key, not just its label.
    algorithm = b"ssh-ed25519"
    if raw[:4] != len(algorithm).to_bytes(
            4, "big") or raw[4:15] != algorithm or raw[15:19] != (32).to_bytes(
                4, "big") or len(raw) != 51:
        raise RuntimeError("Invalid Ed25519 host key encoding")
    return " ".join(fields[:2])


def fingerprint(key):
    raw = base64.b64decode(public_key(key).split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def alias(server):
    uid = server["metadata"]["uid"]
    if not re.fullmatch(r"[A-Za-z0-9-]+", uid):
        raise RuntimeError("Invalid Server UID")
    return "server-" + uid


def address(value):
    # Management inventory is an address, not arbitrary SSH arguments or a URL.
    return str(ipaddress.ip_address(value))


def ssh_args(server, known_hosts, checking="yes"):
    return [
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ConnectionAttempts=1", "-o",
        "IdentitiesOnly=yes", "-o", "IdentityAgent=none", "-o", "ForwardAgent=no", "-o",
        "UpdateHostKeys=no", "-o", "HashKnownHosts=no", "-o", "HostKeyAlgorithms=ssh-ed25519", "-o",
        "GlobalKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=" + checking, "-o",
        "UserKnownHostsFile=" + str(known_hosts), "-o", "HostKeyAlias=" + alias(server), "-o",
        "CertificateFile=" + os.environ["ANSIBLE_CERTIFICATE_FILE"], "-i",
        os.environ["ANSIBLE_PRIVATE_KEY_FILE"]
    ]


def ssh_probe(server, host, known_hosts, checking="yes"):
    result = subprocess.run(
        ["ssh", *ssh_args(server, known_hosts, checking), "ansible@" + address(host), "true"],
        capture_output=True, text=True, timeout=45)
    if result.returncode:
        if "Host key verification failed" in result.stderr or "REMOTE HOST IDENTIFICATION HAS CHANGED" in result.stderr:
            raise OperatorError(
                "HostIdentityMismatch",
                "SSH host identity changed; explicit recovery or reenrollment is required")
        if "Permission denied" in result.stderr:
            raise OperatorError("ManagementAuthenticationFailed",
                                "SSH management certificate authentication failed")
        raise OperatorError(
            "SSHConnectionFailed",
            "SSH identity/authentication verification failed or the target is unreachable")


def inventory_hosts(inventory):
    result = {}
    for group in inventory.values():
        for name, variables in group.get("hosts", {}).items():
            if name in result and result[name].get("ansible_host") != variables.get("ansible_host"):
                raise RuntimeError("Conflicting inventory addresses")
            result.setdefault(name, {}).update(variables)
    return result


def resolved_inventory_hosts(inventory):
    """Let Ansible resolve group inheritance and host-variable precedence."""
    with tempfile.TemporaryDirectory(prefix='operator-inventory-') as directory:
        path = Path(directory) / 'inventory.json'
        write_private(path, json.dumps(inventory))
        output = subprocess.check_output(
            ['ansible-inventory', '-i', str(path), '--list'], text=True, timeout=30)
    resolved = json.loads(output)['_meta']['hostvars']
    # Ignore synthetic/unrelated hosts introduced by local runner configuration.
    names = inventory_hosts(inventory)
    return {name: resolved[name] for name in names}


def capture_inventory(api, group_name):
    group = api.get("inventory-capture-groups", group_name)
    if group["metadata"].get("deletionTimestamp"):
        raise RuntimeError("Inventory capture group is terminating")
    status = group.get("status", {})
    if status.get("observedGeneration") != group["metadata"]["generation"]:
        raise RuntimeError("Inventory capture group has not reconciled its current generation")
    inventory = status.get("inventory")
    if not isinstance(inventory, dict):
        raise RuntimeError("Inventory capture group has no resolved inventory")
    return group, inventory
