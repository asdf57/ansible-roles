"""Reconcile coherent, ordered playbooks independently of OS provisioning.

Configuration dependencies order setups; Ansible orders roles inside each setup.
The executor never bootstraps, resets or upgrades Kubernetes on its own.
"""
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from urllib.error import HTTPError

from common import (API, OperatorError, capture_inventory, resolved_inventory_hosts, run_ansible,
                    ssh_args, write_private, management_address)
from models import JSONObject, Resource, json_object, json_string
from runner_trust import prepare
from server_operation import reservation


@dataclass
class Plan:
    """Resolved public inputs; private runner credentials never enter this record."""
    configuration: Resource
    target: Resource
    servers: list[Resource]
    hosts: dict[str, JSONObject]
    input_revision: str


def mapping(parent: JSONObject, field: str) -> JSONObject:
    """Narrow a required API mapping before inspecting resource-specific fields."""
    return json_object(parent.get(field), field)


def reference(parent: JSONObject, field: str) -> JSONObject:
    """Require a lifetime-bound reference, not just a reusable name."""
    value = mapping(parent, field)
    json_string(value.get('name'), field + '.name')
    json_string(value.get('uid'), field + '.uid')
    return value


def fetch_reference(api: API, collection: str, ref: JSONObject) -> Resource:
    """Resolve the expected lifetime and refuse deletion/replacement."""
    try:
        value = api.get(collection, json_string(ref.get('name'), 'reference name'))
    except HTTPError as error:
        if error.code != 404:
            raise
        raise OperatorError('ReferenceMissing', 'Referenced resource is not available') from error
    if value['metadata']['uid'] != ref.get('uid') or value['metadata'].get('deletionTimestamp'):
        raise OperatorError('ReferenceChanged', 'Referenced resource was replaced or deleted')
    return value


def dependencies(configuration: Resource) -> list[JSONObject]:
    """Return validated Configuration references in declaration order."""
    values = configuration['spec'].get('dependsOn', [])
    if not isinstance(values, list):
        raise ValueError('dependsOn must be an array')
    return [json_object(value, 'dependency') for value in values]


def dependency_order(configurations: list[Resource]) -> list[Resource]:
    """Detect cycles before any work; traverse dependencies before dependents."""
    indexed = {value['metadata']['name']: value for value in configurations}
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[Resource] = []

    def visit(name: str) -> None:
        """Walk one component without treating missing prerequisites as success."""
        if name in visiting:
            raise OperatorError('DependencyCycle', 'Configuration dependency cycle at ' + name)
        if name in visited:
            return
        visiting.add(name)
        for ref in dependencies(indexed[name]):
            dependency_name = json_string(ref.get('name'), 'dependency name')
            if dependency_name in indexed:
                visit(dependency_name)
        visiting.remove(name)
        visited.add(name)
        ordered.append(indexed[name])

    for name in sorted(indexed):
        visit(name)
    return ordered


def installed_server(api: API, server: Resource) -> None:
    """Require the current successful installation and reciprocal hardware binding."""
    state = server['status']
    provisioning = mapping(state, 'provisioning')
    if server['spec'].get('reconciliation', {}) and mapping(server['spec'],
                                                            'reconciliation').get('paused'):
        raise OperatorError('TargetPending', 'Server reconciliation is paused')
    if (not provisioning.get('provisioned') or provisioning.get('activeRunRef')
            or provisioning.get('maintenance') or server['metadata'].get('deletionTimestamp')):
        raise OperatorError('TargetPending',
                            'Server needs a verified installed OS without provisioning ownership')
    run = fetch_reference(api, 'provisioning-runs', reference(provisioning, 'lastSuccessfulRunRef'))
    if run['status'].get('phase') != 'Succeeded' or mapping(
            run['spec'], 'serverRef').get('uid') != server['metadata']['uid']:
        raise OperatorError('TargetPending',
                            'Successful installation does not match Server identity')
    machine = fetch_reference(api, 'machines', reference(state, 'machineRef'))
    if mapping(machine['status'], 'serverRef').get('uid') != server['metadata']['uid']:
        raise OperatorError('TargetPending', 'Machine binding is not reciprocal')
    management_address(server)


def cluster_members(api: API, cluster: Resource) -> list[Resource]:
    """Labels select members; UID annotations prevent same-name cluster adoption."""
    members = []
    node_names: set[str] = set()
    for server in api.list('servers'):
        metadata = server['metadata']
        labels = metadata.get('labels', {})
        if labels.get('homelab.io/kube-cluster') != cluster['metadata']['name']:
            continue
        annotations = metadata.get('annotations', {})
        node_name = annotations.get('homelab.io/kube-node-name', metadata['name'])
        if annotations.get('homelab.io/kube-cluster-uid') != cluster['metadata']['uid']:
            raise OperatorError('MembershipConflict', 'Cluster UID binding is invalid')
        if labels.get('homelab.io/kube-role') not in ('control-plane', 'worker'):
            raise OperatorError('MembershipConflict', 'Kubernetes node role is invalid')
        if not valid_node_name(node_name) or node_name in node_names:
            raise OperatorError('MembershipConflict',
                                'Kubernetes node names must be valid and unique')
        node_names.add(node_name)
        members.append(server)
    control_planes = [
        value for value in members
        if value['metadata'].get('labels', {}).get('homelab.io/kube-role') == 'control-plane'
    ]
    if len(control_planes) != 1:
        raise OperatorError('MembershipConflict', 'V1 requires exactly one control-plane Server')
    return sorted(members, key=lambda value: value['metadata']['name'])


def valid_node_name(name: str) -> bool:
    """Kubernetes node names use DNS subdomains with bounded DNS labels."""
    if not 1 <= len(name) <= 253:
        return False
    return all(
        len(part) <= 63 and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', part)
        for part in name.split('.'))


def member_inputs(server: Resource) -> JSONObject:
    """Fingerprint identities/settings, not pulse timestamps or temporary claims."""
    return dict(
        uid=server['metadata']['uid'], generation=server['metadata'].get('generation', 0),
        machineRef=server['status'].get('machineRef'),
        installationRef=mapping(server['status'], 'provisioning').get('lastSuccessfulRunRef'),
        labels=dict(server['metadata'].get('labels', {})), annotations=dict(server['metadata'].get(
            'annotations', {})), keyPairRef=mapping(server['status'], 'hostSSH').get('keyPairRef'),
        management=mapping(server['status'], 'networking').get('management'))


def resolve_targets(
        api: API, configuration: Resource
) -> tuple[Resource, list[Resource], Resource, dict[str, JSONObject]]:
    """Select installed targets and resolve their inherited inventory variables."""
    ref = reference(configuration['spec'], 'targetRef')
    kind = json_string(ref.get('kind'), 'target kind')
    target = fetch_reference(api, {'Server': 'servers', 'KubeCluster': 'kube-clusters'}[kind], ref)
    servers = [target] if kind == 'Server' else cluster_members(api, target)
    group_ref = reference(configuration['spec'], 'inventoryCaptureGroupRef')
    group = fetch_reference(api, 'inventory-capture-groups', group_ref)
    if mapping(group['spec'], 'selector').get('matchKinds') != [{
            'apiVersion': 'homelab.io/v1alpha1',
            'kind': 'Server'
    }]:
        raise OperatorError('InventoryPending',
                            'Configuration requires a Server-only capture group')
    captured, inventory = capture_inventory(api, group['metadata']['name'])
    if (captured['metadata']['uid'] != group['metadata']['uid']
            or captured['metadata'].get('generation') != group['metadata'].get('generation')):
        raise OperatorError('InventoryPending', 'Capture group changed during resolution')
    raw_hosts = resolved_inventory_hosts(inventory)
    hosts: dict[str, JSONObject] = {}
    for server in servers:
        installed_server(api, server)
        server_name = server['metadata']['name']
        if server_name not in raw_hosts:
            raise OperatorError('InventoryPending',
                                'Selected Server is absent from current inventory')
        hosts[server_name] = dict(raw_hosts[server_name])
    return target, servers, group, hosts


def prerequisite_inputs(api: API, configuration: Resource, stack: tuple[str, ...]) -> JSONObject:
    """Require the prerequisite's verification to describe its current inputs."""
    prerequisite_revisions: JSONObject = {}
    for dependency_ref in dependencies(configuration):
        dependency = fetch_reference(api, 'configurations', dependency_ref)
        try:
            resolved = plan(api, dependency, (*stack, configuration['metadata']['name']))
        except OperatorError as error:
            if error.reason not in ('Suspended', 'TargetPending', 'InventoryPending',
                                    'DependencyPending', 'ReferenceMissing'):
                raise
            raise OperatorError('DependencyPending',
                                'Prerequisite is unavailable or suspended') from error
        if (dependency['status'].get('phase') != 'Ready'
                or dependency['status'].get('observedGeneration')
                != dependency['metadata'].get('generation')
                or dependency['status'].get('inputRevision') != resolved.input_revision):
            raise OperatorError('DependencyPending',
                                'Prerequisite is not verified against current inputs')
        prerequisite_revisions[dependency['metadata']['uid']] = resolved.input_revision
    return prerequisite_revisions


def plan(api: API, configuration: Resource, stack: tuple[str, ...] = ()) -> Plan:
    """Resolve current relevant inputs recursively, not stale Ready flags alone."""
    if configuration['metadata']['name'] in stack:
        raise OperatorError('DependencyCycle', 'Configuration dependency cycle')
    if configuration['spec'].get('suspended') or configuration['metadata'].get('deletionTimestamp'):
        raise OperatorError('Suspended', 'Configuration is suspended or terminating')
    target, servers, group, hosts = resolve_targets(api, configuration)
    repository = fetch_reference(
        api, 'git-repositories',
        reference(mapping(configuration['spec'], 'playbook'), 'repositoryRef'))
    inputs: JSONObject = dict(spec=configuration['spec'], targetUID=target['metadata']['uid'],
                              targetGeneration=target['metadata'].get('generation', 0),
                              targetSpec=target['spec'], groupUID=group['metadata']['uid'],
                              groupGeneration=group['metadata'].get('generation', 0),
                              repositorySpec=repository['spec'], dependencies=prerequisite_inputs(
                                  api, configuration,
                                  stack), members=[member_inputs(server) for server in servers])
    revision = 'sha256:' + hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return Plan(configuration, target, servers, hosts, revision)


def report(api: API, configuration: Resource, phase: str, reason: str,
           revision: str | None = None) -> None:
    """Report only for the same desired generation, preserving other writers."""
    latest = api.get('configurations', configuration['metadata']['name'])
    if (latest['metadata']['uid'] != configuration['metadata']['uid']
            or latest['metadata'].get('generation') != configuration['metadata'].get('generation')
            or latest['metadata'].get('deletionTimestamp')):
        raise OperatorError('InputsChanged', 'Configuration changed before reporting')
    generation = configuration['metadata'].get('generation', 0)
    status: JSONObject = {
        'phase':
        phase,
        'reason':
        reason,
        'observedGeneration':
        generation,
        'conditions': [{
            'type': 'Ready',
            'status': 'True' if phase == 'Ready' else 'False',
            'reason': reason,
            'observedGeneration': generation
        }]
    }
    if revision:
        status['inputRevision'] = revision
    if phase == 'Ready':
        status['lastVerifiedAt'] = datetime.now(timezone.utc).isoformat()
    build_url = os.environ.get('BUILD_URL')
    if build_url:
        status['buildURL'] = build_url
    api.patch_status(latest, status)


def checkout(api: API, configuration: Resource, source: Path, destination: Path) -> Path:
    """Extract an exact reviewed commit from the trusted pipeline Git input.

    No arbitrary repository fetch, shell interpolation, floating branch or unsafe
    archive extraction. An unavailable commit is a visible rollout prerequisite.
    """
    playbook = mapping(configuration['spec'], 'playbook')
    repository = fetch_reference(api, 'git-repositories', reference(playbook, 'repositoryRef'))
    git = ['git', '-c', 'safe.directory=' + str(source), '-C', str(source)]
    origin = subprocess.check_output([*git, 'remote', 'get-url', 'origin'], text=True).strip()
    if origin != repository['spec'].get('url'):
        raise OperatorError('SourceMismatch',
                            'Playbook repository differs from pipeline code input')
    revision = json_string(playbook.get('revision'), 'playbook revision')
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise OperatorError('SourceMismatch', 'An immutable full commit is required')
    relative = json_string(playbook.get('path'), 'playbook path')
    if not re.fullmatch(r'plays/[a-zA-Z0-9_/-]+\.ya?ml', relative):
        raise OperatorError('SourceMismatch',
                            'Playbook must be a file inside the reviewed plays directory')
    archive = subprocess.check_output([*git, 'archive', '--format=tar', revision])
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(destination, filter='data')
    play = (destination / relative).resolve()
    if not play.is_relative_to((destination / 'plays').resolve()) or not play.is_file():
        raise OperatorError('SourceMismatch',
                            'Playbook must be a file inside the reviewed plays directory')
    return play


def prepare_execution(api: API, resolved: Plan, directory: Path) -> None:
    """Write scoped inventory and namespaced inputs with strict host trust."""
    configuration = resolved.configuration
    wire_hosts: JSONObject = {name: values for name, values in resolved.hosts.items()}
    inventory: JSONObject = {'all': {'hosts': wire_hosts}}
    known = directory / 'known_hosts'
    write_private(known, prepare(api, inventory))
    for server in resolved.servers:
        variables = resolved.hosts[server['metadata']['name']]
        variables.update(
            ansible_host=management_address(server), ansible_user='ansible',
            ansible_connection='ssh', ansible_become=True,
            ansible_ssh_args=shlex.join(ssh_args(server, known)), ansible_ssh_common_args='',
            ansible_ssh_extra_args='', configuration_server_uid=server['metadata']['uid'],
            configuration_run_uid=reference(mapping(server['status'], 'provisioning'),
                                            'lastSuccessfulRunRef')['uid'])
    cluster_groups: JSONObject = {}
    if resolved.target['kind'] == 'KubeCluster':
        for role in ('control-plane', 'worker'):
            cluster_groups[role.replace('-', '_')] = {
                'hosts': {
                    server['metadata']['name']: {}
                    for server in resolved.servers
                    if server['metadata'].get('labels', {}).get('homelab.io/kube-role') == role
                }
            }
        inventory['all'] = {'hosts': wire_hosts, 'children': cluster_groups}
    write_private(directory / 'inventory.json', json.dumps(inventory))
    write_private(
        directory / 'values.json',
        json.dumps({
            'configuration_values':
            configuration['spec'].get('values', {}),
            'configuration_target': {
                'kind': resolved.target['kind'],
                'metadata': resolved.target['metadata'],
                'spec': resolved.target['spec']
            },
            'configuration_nodes': [{
                'name':
                server['metadata']['name'],
                'nodeName':
                server['metadata'].get('annotations', {}).get('homelab.io/kube-node-name',
                                                              server['metadata']['name']),
                'role':
                server['metadata'].get('labels', {}).get('homelab.io/kube-role')
            } for server in resolved.servers]
        }))


def verify_claims(api: API, servers: list[Resource]) -> None:
    """A released/replaced host claim is not permission to continue old work."""
    for server in servers:
        latest = api.get('servers', server['metadata']['name'])
        if (latest['metadata']['uid'] != server['metadata']['uid']
                or latest['status'].get('operation') != server['status'].get('operation')
                or mapping(latest['status'], 'operation').get('phase') != 'Held'):
            raise OperatorError('OwnershipChanged', 'Server operation ownership changed')


def run_plays(api: API, resolved: Plan, source: Path, play: Path, directory: Path) -> None:
    """Run the installed-host guard around the reviewed playbook, streaming logs."""
    previous = os.environ.get('ANSIBLE_ROLES_PATH')
    os.environ['ANSIBLE_ROLES_PATH'] = str(play.parent.parent / 'roles')
    try:
        for selected_play in (source / 'plays/configuration_guard.yml', play,
                              source / 'plays/configuration_guard.yml'):
            verify_claims(api, resolved.servers)
            result = run_ansible([
                'ansible-playbook', '-i',
                str(directory / 'inventory.json'),
                str(selected_play), '-e', '@' + str(directory / 'values.json')
            ], directory / 'ansible.log', 660)
            if result.returncode:
                raise OperatorError('PlaybookFailed',
                                    'Reviewed configuration or verification play failed')
    except subprocess.TimeoutExpired as error:
        # Killing local SSH/Ansible cannot prove all remote work has stopped.
        # Retain every acquired host claim until an administrator inspects it.
        raise OperatorError(
            'RemoteWorkUncertain',
            'Timed out; inspect remote work before releasing host claims') from error
    finally:
        if previous is None:
            os.environ.pop('ANSIBLE_ROLES_PATH', None)
        else:
            os.environ['ANSIBLE_ROLES_PATH'] = previous


def execute(api: API, resolved: Plan, source: Path, directory: Path) -> None:
    """Claim all selected hosts, verify installed sessions, execute and recheck inputs."""
    configuration = resolved.configuration
    play = checkout(api, configuration, source, directory / 'code')
    prepare_execution(api, resolved, directory)
    with ExitStack() as owners:
        for server in resolved.servers:
            owners.enter_context(reservation(api, server))
        refreshed = plan(api, configuration)
        if refreshed.input_revision != resolved.input_revision:
            raise OperatorError('InputsChanged', 'Configuration inputs changed before execution')
        report(api, configuration, 'Reconciling', 'ExecutingPlaybook', resolved.input_revision)
        run_plays(api, resolved, source, play, directory)
        verify_claims(api, resolved.servers)
        if plan(api, configuration).input_revision != resolved.input_revision:
            raise OperatorError('InputsChanged', 'Configuration inputs changed during execution')
    report(api, configuration, 'Ready', 'PlaybookVerified', resolved.input_revision)


def main() -> int:
    """A bounded scheduled/manual pass; unrelated configurations still get examined."""
    api = API()
    configurations = [
        value for value in api.list('configurations')
        if mapping(value['spec'], 'inventoryCaptureGroupRef').get('name') ==
        os.environ['INVENTORY_CAPTURE_GROUP']
    ]
    try:
        ordered = dependency_order(configurations)
    except OperatorError as error:
        # Plans still refuse their own cycles; unrelated setups can continue.
        print(error.reason + ': invalid components will be reported individually', flush=True)
        ordered = sorted(configurations, key=lambda value: value['metadata']['name'])
    failed = False
    for configuration in ordered:
        try:
            resolved = plan(api, configuration)
            with tempfile.TemporaryDirectory(prefix='configuration-') as temporary:
                execute(api, resolved, Path(__file__).resolve().parents[1], Path(temporary))
            print(configuration['metadata']['name'] + ': verified', flush=True)
        except Exception as error:
            reason = error.reason if isinstance(error, OperatorError) else 'ExecutionFailed'
            pending = reason in ('ReferenceMissing', 'TargetPending', 'InventoryPending',
                                 'DependencyPending', 'OperationBusy', 'InputsChanged')
            phase = 'Suspended' if reason == 'Suspended' else 'Pending' if pending else 'Failed'
            print(configuration['metadata']['name'] + ': ' + reason, flush=True)
            try:
                report(api, configuration, phase, reason)
            except Exception:
                print(configuration['metadata']['name'] + ': status report conflicted', flush=True)
            failed = failed or phase == 'Failed'
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
