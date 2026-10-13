"""Install a labeled inventory group's Kubernetes context in an interactive runner."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.request import urlopen

from common import API, address, capture_inventory, inventory_hosts, management_address, ssh_args
from common import write_private
from models import JSONObject, JSONValue, Resource, json_object, json_string
from runner_trust import prepare

CLUSTER_LABEL = 'homelab.io/kube-cluster'

# Read-only on the node. Never export admin.conf until the protected installed
# identity and the actual API's namespace identity agree with this request.
REMOTE_READ = '''
import json, os, stat, subprocess, sys
expected = json.load(sys.stdin)
def protected(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('Unprotected cluster file')
protected('/var/lib/homelab/kube-cluster.json')
protected('/etc/kubernetes/admin.conf')
with open('/var/lib/homelab/kube-cluster.json', encoding='utf-8') as stream:
    marker = json.load(stream)
identity = marker['identity']
assert marker['phase'] == 'Ready'
assert identity['clusterUID'] == expected['clusterUID']
assert identity['serverUID'] == expected['serverUID']
assert identity['role'] == 'control-plane'
assert all(identity['settings'].get(field) == value for field, value in expected['settings'].items())
command = ['/usr/local/bin/kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s']
namespace = json.loads(subprocess.check_output(command + ['get', 'namespace', 'kube-system', '-o', 'json']))
assert namespace['metadata']['uid'] == marker['clusterNamespaceUID']
ownership = json.loads(subprocess.check_output(command + ['-n', 'kube-system', 'get', 'configmap', 'homelab-identity', '-o', 'json']))
assert ownership['data']['clusterUID'] == expected['clusterUID']
print(subprocess.check_output(command + ['config', 'view', '--raw', '--minify', '-o', 'json']).decode())
'''


def control_plane(api: API, inventory: JSONObject, cluster: Resource) -> Resource:
    """Select one captured control plane; group context does not define membership."""
    candidates = []
    for name in inventory_hosts(inventory):
        server = api.get('servers', name)
        labels = server['metadata'].get('labels', {})
        if (labels.get(CLUSTER_LABEL) == cluster['metadata']['name']
                and labels.get('homelab.io/kube-role') == 'control-plane'):
            candidates.append(server)
    if len(candidates) != 1:
        raise ValueError('The capture group must contain exactly one cluster control plane')
    server = candidates[0]
    bound_uid = server['metadata'].get('annotations', {}).get('homelab.io/kube-cluster-uid')
    if bound_uid not in (None, cluster['metadata']['uid']):
        raise ValueError('Control plane belongs to a replaced cluster')
    return server


def fetch_context(server: Resource, cluster: Resource) -> JSONObject:
    """Capture private kubeconfig over strict, UID-qualified SSH without logging it."""
    with tempfile.TemporaryDirectory(prefix='runner-kubernetes-') as temporary:
        known_hosts = Path(temporary) / 'known_hosts'
        # Reuse the same managed-identity checks as ordinary command startup.
        inventory: JSONObject = {'all': {'hosts': {server['metadata']['name']: {}}}}

        class SingleServerAPI:
            """Adapt the already-read Server to the shared trust verifier."""

            def get(self, _collection: str, _name: str) -> Resource:
                """Return this exact Server lifetime, never a replacement by name."""
                return server

        write_private(known_hosts, prepare(SingleServerAPI(), inventory))
        host = management_address(server)
        if host is None:
            raise ValueError('Control plane has no management address')
        command = [
            'ssh', '-T', *ssh_args(server, known_hosts), 'ansible@' + address(host),
            'sudo -n python3 -c ' + shlex.quote(REMOTE_READ)
        ]
        result = subprocess.run(
            command, input=json.dumps({
                'clusterUID': cluster['metadata']['uid'],
                'serverUID': server['metadata']['uid'],
                'settings': cluster['spec'],
            }), capture_output=True, text=True, timeout=90, check=False)
        if result.returncode:
            raise ValueError('Verified control-plane credential retrieval failed')
        decoded: JSONValue = json.loads(result.stdout)
        return json_object(decoded, 'kubeconfig')


def validate_context(config: JSONObject, cluster: Resource) -> JSONObject:
    """Accept only a self-contained TLS client-certificate context at the desired endpoint."""
    if config.get('apiVersion') != 'v1' or config.get('kind') != 'Config':
        raise ValueError('Invalid kubeconfig envelope')
    for field in ('clusters', 'users', 'contexts'):
        entries = config.get(field)
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError('Expected one self-contained Kubernetes context')
    clusters = config['clusters']
    users = config['users']
    contexts = config['contexts']
    if not isinstance(clusters, list) or not isinstance(users, list) or not isinstance(
            contexts, list):
        raise ValueError('Invalid kubeconfig entries')
    cluster_entry = json_object(clusters[0], 'cluster entry')
    user_entry = json_object(users[0], 'user entry')
    context_entry = json_object(contexts[0], 'context entry')
    connection = json_object(cluster_entry.get('cluster'), 'cluster connection')
    credentials = json_object(user_entry.get('user'), 'client credentials')
    context = json_object(context_entry.get('context'), 'context binding')
    endpoint = json_string(cluster['spec'].get('controlPlaneEndpoint'), 'control-plane endpoint')
    if connection.get('server') != 'https://' + endpoint:
        raise ValueError('Kubeconfig endpoint does not match the cluster')
    if set(connection) != {'server', 'certificate-authority-data'}:
        raise ValueError('Kubeconfig must use embedded CA trust without overrides')
    if set(credentials) != {'client-certificate-data', 'client-key-data'}:
        raise ValueError('Kubeconfig must use embedded client credentials without plugins')
    for value in (*credentials.values(), connection.get('certificate-authority-data')):
        if not json_string(value, 'embedded certificate material'):
            raise ValueError('Empty certificate material')
    if (context.get('cluster') != cluster_entry.get('name')
            or context.get('user') != user_entry.get('name')
            or config.get('current-context') != context_entry.get('name')):
        raise ValueError('Kubeconfig context binding is inconsistent')
    context_entry['name'] = cluster['metadata']['name']
    config['current-context'] = cluster['metadata']['name']
    return config


def install_kubectl(version: str, directory: Path) -> None:
    """Install the exact cluster client using its published HTTPS SHA-256 checksum."""
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', version):
        raise ValueError('Cluster Kubernetes version must be an exact release')
    release = 'v' + version.removeprefix('v')
    architecture = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
    if architecture is None:
        raise ValueError('Unsupported kubectl client architecture')
    url = f'https://dl.k8s.io/release/{release}/bin/linux/{architecture}/kubectl'
    with urlopen(url + '.sha256', timeout=30) as response:
        expected = response.read(1024).decode().strip()
    if not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise ValueError('Invalid kubectl checksum')
    destination = directory / 'kubectl'
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
        return
    with urlopen(url, timeout=60) as response:
        binary = response.read(128 * 1024 * 1024 + 1)
    if len(binary) > 128 * 1024 * 1024 or hashlib.sha256(binary).hexdigest() != expected:
        raise ValueError('kubectl checksum mismatch')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
        pending = Path(temporary.name)
        try:
            temporary.write(binary)
            temporary.flush()
            pending.chmod(0o700)
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)


def setup_context(api: API, group_name: str, home: Path) -> bool:
    """Refresh only our disposable context; never overwrite user-supplied kubeconfig."""
    directory = home / '.kube'
    config_path = directory / 'config'
    ownership = directory / 'homelab-context.json'
    if directory.is_symlink() or ownership.is_symlink() or config_path.is_symlink():
        raise ValueError('Refusing symlinked Kubernetes context files')
    # Remove our old credentials before resolving anything: a failed refresh must
    # not leave the shell pointing at a previous cluster or previous lifetime.
    if ownership.is_file():
        config_path.unlink(missing_ok=True)
        ownership.unlink()
    group, inventory = capture_inventory(api, group_name)
    name = group['metadata'].get('labels', {}).get(CLUSTER_LABEL)
    if name is None:
        return False
    if config_path.exists():
        raise ValueError('Refusing to overwrite an unmanaged kubeconfig')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', name):
        raise ValueError('Invalid cluster label')
    cluster = api.get('kube-clusters', name)
    if cluster['metadata'].get('deletionTimestamp'):
        raise ValueError('Cluster is terminating')
    server = control_plane(api, inventory, cluster)
    config = validate_context(fetch_context(server, cluster), cluster)
    current = api.get('kube-clusters', name)
    if current['metadata']['uid'] != cluster['metadata']['uid'] or current['spec'] != cluster[
            'spec']:
        raise ValueError('Cluster changed during credential retrieval')
    current_server = api.get('servers', server['metadata']['name'])
    current_group, _ = capture_inventory(api, group_name)
    if (current_server['metadata']['uid'] != server['metadata']['uid']
            or current_server['metadata'].get('labels') != server['metadata'].get('labels')
            or current_server['metadata'].get('deletionTimestamp')
            or current_server['status'].get('hostSSH') != server['status'].get('hostSSH')
            or management_address(current_server) != management_address(server)):
        raise ValueError('Control-plane identity changed during credential retrieval')
    if (current_group['metadata']['uid'] != group['metadata']['uid']
            or current_group['metadata'].get('generation') != group['metadata'].get('generation')
            or current_group['metadata'].get('labels') != group['metadata'].get('labels')):
        raise ValueError('Inventory context changed during credential retrieval')
    install_kubectl(json_string(cluster['spec'].get('kubernetesVersion'), 'Kubernetes version'),
                    home / '.local' / 'bin')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    write_private(ownership, json.dumps({'clusterUID': cluster['metadata']['uid']}))
    write_private(config_path, json.dumps(config))
    return True


def main() -> int:
    """Limit admin context retrieval to interactive mode and redact private failures."""
    if os.environ.get('CONTAINER_MODE') != 'normal':
        return 0
    try:
        installed = setup_context(API(), os.environ['INVENTORY_CAPTURE_GROUP'], Path.home())
    except Exception:
        print('Kubernetes context initialization failed; no credentials were logged.',
              file=sys.stderr)
        return 1
    if installed:
        print('Kubernetes context ready; use kubectl normally in this container.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
