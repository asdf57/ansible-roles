"""Reconcile coherent, ordered playbooks independently of OS provisioning.

Configuration dependencies order setups; Ansible orders roles inside each setup.
The executor never bootstraps, resets or upgrades Kubernetes on its own.
"""
from contextlib import ExitStack
from copy import deepcopy
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
from server_operation import reservation, operation_progress, verify_operation


@dataclass
class Plan:
    """Resolved public inputs; private runner credentials never enter this record."""
    configuration: Resource
    target: Resource
    servers: list[Resource]
    hosts: dict[str, JSONObject]
    input_revision: str
    playbook_revision: str
    resolved_references: list[JSONObject]
    inventory: JSONObject


def mapping(parent: JSONObject, field: str) -> JSONObject:
    """Narrow a required API mapping before inspecting resource-specific fields."""
    return json_object(parent.get(field), field)


def reference(parent: JSONObject, field: str) -> JSONObject:
    """Validate a declarative reference by name; observed refs may also carry a UID."""
    value = mapping(parent, field)
    json_string(value.get('name'), field + '.name')
    return value


def pinned_reference(parent: JSONObject, field: str) -> JSONObject:
    """Observed installation/hardware bindings must still identify a lifetime."""
    value = reference(parent, field)
    json_string(value.get('uid'), field + '.uid')
    return value


def fetch_reference(api: API, collection: str, ref: JSONObject) -> Resource:
    """Resolve desired names; enforce UID pins only on observed execution refs."""
    try:
        value = api.get(collection, json_string(ref.get('name'), 'reference name'))
    except HTTPError as error:
        if error.code != 404:
            raise
        raise OperatorError('ReferenceMissing', 'Referenced resource is not available') from error
    if (ref.get('uid') is not None and value['metadata']['uid'] != ref['uid']
            or value['metadata'].get('deletionTimestamp')):
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
    run = fetch_reference(api, 'provisioning-runs',
                          pinned_reference(provisioning, 'lastSuccessfulRunRef'))
    if run['status'].get('phase') != 'Succeeded' or mapping(
            run['spec'], 'serverRef').get('uid') != server['metadata']['uid']:
        raise OperatorError('TargetPending',
                            'Successful installation does not match Server identity')
    machine = fetch_reference(api, 'machines', pinned_reference(state, 'machineRef'))
    if mapping(machine['status'], 'serverRef').get('uid') != server['metadata']['uid']:
        raise OperatorError('TargetPending', 'Machine binding is not reciprocal')
    management_address(server)


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
) -> tuple[Resource, list[Resource], Resource, dict[str, JSONObject], JSONObject]:
    """Target supplies context; the capture group supplies the execution boundary."""
    ref = reference(configuration['spec'], 'targetRef')
    kind = json_string(ref.get('kind'), 'target kind')
    target = fetch_reference(api, {'Server': 'servers', 'KubeCluster': 'kube-clusters'}[kind], ref)
    group_ref = reference(configuration['spec'], 'inventoryCaptureGroupRef')
    group = fetch_reference(api, 'inventory-capture-groups', group_ref)
    if group['status'].get('omittedResources'):
        raise OperatorError(
            'InventoryPending',
            'Configuration requires a complete capture; selected resources were omitted')
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
    if not raw_hosts:
        raise OperatorError('InventoryPending', 'Capture group has no managed hosts')
    servers = [fetch_reference(api, 'servers', {'name': name}) for name in sorted(raw_hosts)]
    hosts: dict[str, JSONObject] = {}
    for server in servers:
        installed_server(api, server)
        server_name = server['metadata']['name']
        hosts[server_name] = dict(raw_hosts[server_name])
    return target, servers, group, hosts, json_object(inventory, 'capture inventory')


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


def observed_reference(value: Resource) -> JSONObject:
    """Persist the resource lifetime resolved for this execution, never credentials."""
    return dict(kind=value['kind'], name=value['metadata']['name'], uid=value['metadata']['uid'],
                generation=value['metadata'].get('generation', 0))


def source_commit(repository: Resource, playbook: JSONObject, source: Path) -> str:
    """Resolve a local trusted Git input to a commit without fetching arbitrary code."""
    git = ['git', '-c', 'safe.directory=' + str(source), '-C', str(source)]
    origin = subprocess.check_output([*git, 'remote', 'get-url', 'origin'], text=True).strip()
    if origin != repository['spec'].get('url'):
        raise OperatorError('SourceMismatch',
                            'Playbook repository differs from pipeline code input')
    revision = playbook.get('revision')
    if revision is not None:
        commit = json_string(revision, 'playbook revision')
        if not re.fullmatch('[0-9a-f]{40}', commit):
            raise OperatorError('SourceMismatch', 'Revision must be a full immutable commit')
        candidates = [commit]
    else:
        ref = json_string(repository['spec'].get('ref', 'main'), 'repository ref')
        candidates = ['refs/remotes/origin/' + ref, 'refs/heads/' + ref, 'refs/tags/' + ref]
    for candidate in candidates:
        result = subprocess.run(
            [*git, 'rev-parse', '--verify', '--end-of-options', candidate + '^{commit}'],
            check=False, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    raise OperatorError('SourceUnavailable',
                        'Requested commit/ref is missing from pipeline Git input')


def plan(api: API, configuration: Resource, stack: tuple[str, ...] = ()) -> Plan:
    """Resolve current relevant inputs recursively, not stale Ready flags alone."""
    if configuration['metadata']['name'] in stack:
        raise OperatorError('DependencyCycle', 'Configuration dependency cycle')
    if configuration['spec'].get('suspended') or configuration['metadata'].get('deletionTimestamp'):
        raise OperatorError('Suspended', 'Configuration is suspended or terminating')
    target, servers, group, hosts, inventory = resolve_targets(api, configuration)
    repository = fetch_reference(
        api, 'git-repositories',
        reference(mapping(configuration['spec'], 'playbook'), 'repositoryRef'))
    commit = source_commit(repository, mapping(configuration['spec'], 'playbook'),
                           Path(__file__).resolve().parents[1])
    references = [observed_reference(value) for value in (target, group, repository, *servers)]
    references.extend(
        observed_reference(fetch_reference(api, 'configurations', ref))
        for ref in dependencies(configuration))
    inputs: JSONObject = dict(spec=configuration['spec'], targetUID=target['metadata']['uid'],
                              targetGeneration=target['metadata'].get('generation', 0),
                              targetSpec=target['spec'], groupUID=group['metadata']['uid'],
                              groupGeneration=group['metadata'].get('generation', 0),
                              repositorySpec=repository['spec'], playbookRevision=commit,
                              inventory=inventory, dependencies=prerequisite_inputs(
                                  api, configuration,
                                  stack), members=[member_inputs(server) for server in servers])
    inputs['resolvedReferences'] = [ref for ref in references]
    inputs['hosts'] = {name: variables for name, variables in hosts.items()}
    revision = 'sha256:' + hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return Plan(configuration, target, servers, hosts, revision, commit, references, inventory)


def report(api: API, configuration: Resource, phase: str, reason: str, revision: str | None = None,
           resolved: Plan | None = None, outputs: JSONObject | None = None) -> None:
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
    if resolved:
        status['playbookRevision'] = resolved.playbook_revision
        status['resolvedReferences'] = [ref for ref in resolved.resolved_references]
    if phase == 'Ready':
        status['lastVerifiedAt'] = datetime.now(timezone.utc).isoformat()
        status['outputs'] = outputs or {}
    build_url = os.environ.get('BUILD_URL')
    if build_url:
        status['buildURL'] = build_url
    api.patch_status(latest, status)


def checkout(api: API, configuration: Resource, source: Path, destination: Path,
             expected_commit: str | None = None) -> Path:
    """Extract an exact reviewed commit from the trusted pipeline Git input.

    Optional pins and configured refs both resolve to immutable commits. No remote
    fetch or unsafe archive extraction; missing code is a visible prerequisite.
    """
    playbook = mapping(configuration['spec'], 'playbook')
    repository = fetch_reference(api, 'git-repositories', reference(playbook, 'repositoryRef'))
    git = ['git', '-c', 'safe.directory=' + str(source), '-C', str(source)]
    revision = source_commit(repository, playbook, source)
    if expected_commit is not None and revision != expected_commit:
        raise OperatorError('InputsChanged', 'Playbook source changed before checkout')
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
    """Preserve capture groups; protect host transport and provide namespaced context."""
    configuration = resolved.configuration
    wire_hosts: JSONObject = {name: values for name, values in resolved.hosts.items()}
    inventory: JSONObject = deepcopy(resolved.inventory)
    # Normalize every occurrence to resolved host vars so inherited transport
    # settings cannot override the verified management connection.
    for group_value in inventory.values():
        group = json_object(group_value, 'inventory group')
        if 'hosts' in group:
            group['hosts'] = {name: resolved.hosts[name] for name in mapping(group, 'hosts')}
    all_group = json_object(inventory.setdefault('all', {}), 'all inventory group')
    all_group['hosts'] = wire_hosts
    known = directory / 'known_hosts'
    write_private(known, prepare(api, inventory))
    for server in resolved.servers:
        variables = resolved.hosts[server['metadata']['name']]
        variables.update(
            ansible_host=management_address(server), ansible_user='ansible',
            ansible_connection='ssh', ansible_become=True,
            ansible_ssh_args=shlex.join(ssh_args(server, known)), ansible_ssh_common_args='',
            ansible_ssh_extra_args='', configuration_server_uid=server['metadata']['uid'],
            configuration_run_uid=pinned_reference(mapping(server['status'], 'provisioning'),
                                                   'lastSuccessfulRunRef')['uid'])
    write_private(directory / 'inventory.json', json.dumps(inventory))
    write_private(
        directory / 'values.json',
        json.dumps({
            'configuration_values':
            configuration['spec'].get('values', {}),
            'configuration_workspace':
            str(directory),
            'configuration_playbook_revision':
            resolved.playbook_revision,
            'configuration_ref': {
                'name': configuration['metadata']['name'],
                'uid': configuration['metadata']['uid']
            },
            'configuration_generation':
            configuration['metadata'].get('generation', 0),
            'configuration_input_revision':
            resolved.input_revision,
            'configuration_observed':
            configuration['status'].get('outputs', {}),
            'configuration_target': {
                'kind': resolved.target['kind'],
                'metadata': resolved.target['metadata'],
                'spec': resolved.target['spec']
            },
            'configuration_nodes': [{
                'name': server['metadata']['name'],
                'uid': server['metadata']['uid'],
                'labels': dict(server['metadata'].get('labels', {})),
                'annotations': dict(server['metadata'].get('annotations', {})),
            } for server in resolved.servers]
        }))


def verify_claims(api: API, servers: list[Resource]) -> None:
    """A released/replaced host claim is not permission to continue old work."""
    for server in servers:
        latest = api.get('servers', server['metadata']['name'])
        if (latest['metadata']['uid'] != server['metadata']['uid']
                or not server['status'].get('operationRef')
                or latest['status'].get('operationRef') != server['status'].get('operationRef')):
            raise OperatorError('OwnershipChanged', 'Server operation ownership changed')
        verify_operation(api, latest)


def run_plays(api: API, resolved: Plan, source: Path, play: Path, directory: Path) -> None:
    """Run the installed-host guard around the reviewed playbook, streaming logs."""
    paths = {
        'ANSIBLE_ROLES_PATH': str(play.parent.parent / 'roles'),
        'ANSIBLE_FILTER_PLUGINS': str(play.parent.parent / 'filter_plugins')
    }
    previous = {name: os.environ.get(name) for name in paths}
    os.environ.update(paths)
    try:
        for selected_play in (source / 'plays/configuration_guard.yml', play,
                              source / 'plays/configuration_guard.yml'):
            verify_claims(api, resolved.servers)
            for server in resolved.servers:
                operation_progress(api, server, 'Execute ' + selected_play.name)
            result = run_ansible([
                'ansible-playbook', '-i',
                str(directory / 'inventory.json'),
                str(selected_play), '-e', '@' + str(directory / 'values.json')
            ], directory / 'ansible.log', 3600)
            if result.returncode in (4, 6):
                # Transport loss does not prove a remote module/process stopped.
                raise OperatorError(
                    'RemoteWorkUncertain',
                    'SSH became unreachable; inspect remote work before releasing claims')
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
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def refresh_claim_target(api: API, server: Resource) -> None:
    """Refresh heartbeat-only revisions without accepting changed execution inputs."""
    latest = api.get('servers', server['metadata']['name'])
    if (latest['metadata'].get('deletionTimestamp')
            or member_inputs(latest) != member_inputs(server)
            or latest['status'].get('hostSSH') != server['status'].get('hostSSH')):
        raise OperatorError('InputsChanged', 'Server inputs changed before operation acquisition')
    # Checkout/inventory preparation can outlive several agent reports. The CAS
    # must use a fresh revision; reservation still refuses actual held ownership.
    server.update(latest)


def read_public_result(path: Path) -> JSONObject | None:
    """Read one optional bounded public object, refusing links and special files."""
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise OperatorError('InvalidResult', 'Playbook result must be a small regular JSON file')
    try:
        return json_object(json.loads(path.read_text(encoding='utf-8')), 'public playbook result')
    except (ValueError, UnicodeError) as error:
        raise OperatorError('InvalidResult',
                            'Playbook result must be a public JSON object') from error


def publish_target_status(api: API, resolved: Plan, status: JSONObject) -> None:
    """Publish verified target observations under its original lifetime/generation."""
    target = resolved.target
    collection = {'Server': 'servers', 'KubeCluster': 'kube-clusters'}[target['kind']]
    for _ in range(5):
        latest = api.get(collection, target['metadata']['name'])
        if (latest['metadata']['uid'] != target['metadata']['uid']
                or latest['metadata'].get('generation') != target['metadata'].get('generation')
                or latest['metadata'].get('deletionTimestamp')):
            raise OperatorError('InputsChanged', 'Target changed before reporting observations')
        try:
            api.patch_status(latest, status)
            return
        except HTTPError as error:
            if error.code != 409:
                raise
    raise OperatorError('InputsChanged',
                        'Target status conflicted; observations were not published')


def execute(api: API, resolved: Plan, source: Path, directory: Path) -> None:
    """Claim all selected hosts, verify installed sessions, execute and recheck inputs."""
    configuration = resolved.configuration
    play = checkout(api, configuration, source, directory / 'code', resolved.playbook_revision)
    prepare_execution(api, resolved, directory)
    with ExitStack() as owners:
        for server in resolved.servers:
            refresh_claim_target(api, server)
            owners.enter_context(
                reservation(
                    api, server, 'configure', 'configurations', {
                        'kind': 'Configuration',
                        'name': configuration['metadata']['name'],
                        'uid': configuration['metadata']['uid']
                    }))
        refreshed = plan(api, configuration)
        if refreshed.input_revision != resolved.input_revision:
            raise OperatorError('InputsChanged', 'Configuration inputs changed before execution')
        report(api, configuration, 'Reconciling', 'ExecutingPlaybook', resolved.input_revision,
               resolved)
        try:
            run_plays(api, resolved, source, play, directory)
        except OperatorError as error:
            # A completed failed play may still have useful public observations.
            # Never publish its success file, nor report after uncertain remote
            # work, ownership loss or changed inputs. Domain roles own payloads.
            if error.reason == 'PlaybookFailed':
                publish_observations(api, resolved, directory)
            raise
        verify_claims(api, resolved.servers)
        if plan(api, configuration).input_revision != resolved.input_revision:
            raise OperatorError('InputsChanged', 'Configuration inputs changed during execution')
        outputs = read_public_result(directory / 'result.json') or {}
        publish_observations(api, resolved, directory)
        target_status = read_public_result(directory / 'target-status.json')
        if target_status is not None:
            publish_target_status(api, resolved, target_status)
    report(api, configuration, 'Ready', 'PlaybookVerified', resolved.input_revision, resolved,
           outputs)


def publish_observations(api: API, resolved: Plan, directory: Path) -> None:
    """Publish optional observation-only output without claiming a successful setup."""
    observations = read_public_result(directory / 'target-observation.json')
    if observations is None:
        return
    verify_claims(api, resolved.servers)
    if plan(api, resolved.configuration).input_revision != resolved.input_revision:
        raise OperatorError('InputsChanged', 'Inputs changed before reporting observations')
    publish_target_status(api, resolved, observations)


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
