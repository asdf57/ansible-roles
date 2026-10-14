"""Configuration safety tests never connect to or mutate managed nodes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import configurations as executor
from common import API, OperatorError
from models import JSONObject, Resource


def resource(kind: str, name: str, spec: JSONObject, status: JSONObject | None = None) -> Resource:
    """Make a complete resource lifetime, rather than an untyped mock envelope."""
    return {
        'kind': kind,
        'metadata': {
            'name': name,
            'uid': name + '-uid',
            'resourceVersion': '1',
            'generation': 1
        },
        'spec': spec,
        'status': status or {}
    }


class FixtureAPI(API):
    """Typed in-memory transport with real resource identities and generations."""

    def __init__(self):
        self.values: dict[tuple[str, str], Resource] = {}

    def get(self, collection: str, name: str) -> Resource:
        return copy.deepcopy(self.values[(collection, name)])

    def list(self, collection: str) -> list[Resource]:
        return [
            copy.deepcopy(value) for (kind, _name), value in self.values.items()
            if kind == collection
        ]

    def patch_status(self, server: Resource, status: JSONObject) -> Resource:
        collection = {
            'Configuration': 'configurations',
            'Server': 'servers',
            'KubeCluster': 'kube-clusters'
        }[server['kind']]
        value = self.values[(collection, server['metadata']['name'])]
        value['status'].update(status)
        return copy.deepcopy(value)


class ConfigurationTests(unittest.TestCase):
    """Dependency freshness, replacement and installation boundaries are explicit."""

    def setUp(self):
        self.api = FixtureAPI()
        self.server = resource('Server', 'node', {}, {
            'machineRef': {
                'name': 'machine',
                'uid': 'machine-uid'
            },
            'provisioning': {
                'provisioned': True,
                'lastSuccessfulRunRef': {
                    'name': 'install',
                    'uid': 'install-uid'
                }
            },
            'hostSSH': {
                'keyPairRef': {
                    'name': 'key',
                    'uid': 'key-uid'
                }
            },
            'networking': {
                'management': {
                    'address': {
                        'address': '192.0.2.1'
                    }
                }
            }
        })
        self.group = resource(
            'InventoryCaptureGroup', 'group',
            {'selector': {
                'matchKinds': [{
                    'apiVersion': 'homelab.io/v1alpha1',
                    'kind': 'Server'
                }]
            }})
        self.configuration = resource(
            'Configuration', 'base', {
                'targetRef': {
                    'kind': 'Server',
                    'name': 'node',
                },
                'inventoryCaptureGroupRef': {
                    'name': 'group',
                },
                'playbook': {
                    'repositoryRef': {
                        'name': 'code',
                    },
                    'revision': 'a' * 40,
                    'path': 'plays/base.yml'
                }
            })
        for collection, value in (('servers', self.server), ('inventory-capture-groups',
                                                             self.group), ('configurations',
                                                                           self.configuration),
                                  ('git-repositories',
                                   resource('GitRepository', 'code',
                                            {'url': 'https://example.test/code.git'})),
                                  ('machines',
                                   resource('Machine', 'machine', {},
                                            {'serverRef': {
                                                'name': 'node',
                                                'uid': 'node-uid'
                                            }})),
                                  ('provisioning-runs',
                                   resource('ProvisioningRun', 'install',
                                            {'serverRef': {
                                                'name': 'node',
                                                'uid': 'node-uid'
                                            }}, {'phase': 'Succeeded'}))):
            self.api.values[(collection, value['metadata']['name'])] = value
        self.addCleanup(patch.stopall)
        patch.object(executor, 'source_commit', return_value='a' * 40).start()
        patch.object(executor, 'capture_inventory', return_value=(self.group, {})).start()
        patch.object(executor, 'resolved_inventory_hosts', return_value={'node': {}}).start()

    def test_claim_refresh_accepts_heartbeat_revision_only(self):
        snapshot = copy.deepcopy(self.server)
        self.server['metadata']['resourceVersion'] = '2'
        self.server['status']['agent'] = {'lastSeenTime': '2026-10-13T00:00:00Z'}
        executor.refresh_claim_target(self.api, snapshot)
        self.assertEqual(snapshot['metadata']['resourceVersion'], '2')

    def test_claim_refresh_refuses_changed_identity_or_transport(self):
        snapshot = copy.deepcopy(self.server)
        self.server['metadata']['uid'] = 'changed'
        with self.assertRaises(OperatorError):
            executor.refresh_claim_target(self.api, snapshot)
        self.server['metadata']['uid'] = snapshot['metadata']['uid']
        self.server['metadata']['generation'] = 2
        with self.assertRaises(OperatorError):
            executor.refresh_claim_target(self.api, snapshot)
        self.server['metadata']['generation'] = 1
        executor.mapping(self.server['status'], 'hostSSH')['phase'] = 'Pending'
        with self.assertRaises(OperatorError):
            executor.refresh_claim_target(self.api, snapshot)

    def test_ordering_and_cycles(self):
        dependent = copy.deepcopy(self.configuration)
        dependent['metadata']['name'] = 'dependent'
        dependent['spec']['dependsOn'] = [{'name': 'base'}]
        self.assertEqual([
            item['metadata']['name']
            for item in executor.dependency_order([dependent, self.configuration])
        ], ['base', 'dependent'])
        self.configuration['spec']['dependsOn'] = [{'name': 'dependent'}]
        with self.assertRaisesRegex(OperatorError, 'cycle'):
            executor.dependency_order([dependent, self.configuration])

    def test_dependencies_require_current_input_revision(self):
        dependent = copy.deepcopy(self.configuration)
        dependent['metadata']['name'] = 'applications'
        dependent['spec']['dependsOn'] = [{'name': 'base'}]
        with self.assertRaisesRegex(OperatorError, 'Prerequisite'):
            executor.plan(self.api, dependent)
        observed = executor.plan(self.api, self.configuration)
        self.configuration['status'].update(phase='Ready', observedGeneration=1,
                                            inputRevision=observed.input_revision)
        executor.plan(self.api, dependent)
        self.configuration['spec']['values'] = {'changed': True}
        with self.assertRaisesRegex(OperatorError, 'Prerequisite'):
            executor.plan(self.api, dependent)

    def test_new_installation_invalidates_prior_configuration(self):
        prior = executor.plan(self.api, self.configuration).input_revision
        replacement = resource('ProvisioningRun', 'replacement',
                               {'serverRef': {
                                   'name': 'node',
                                   'uid': 'node-uid'
                               }}, {'phase': 'Succeeded'})
        self.api.values[('provisioning-runs', 'replacement')] = replacement
        provisioning = executor.mapping(self.server['status'], 'provisioning')
        provisioning['lastSuccessfulRunRef'] = {'name': 'replacement', 'uid': 'replacement-uid'}
        self.assertNotEqual(prior, executor.plan(self.api, self.configuration).input_revision)

    def test_heartbeat_does_not_change_input_revision(self):
        prior = executor.plan(self.api, self.configuration).input_revision
        self.server['status']['agent'] = {'lastSeenTime': 'new heartbeat'}
        self.assertEqual(prior, executor.plan(self.api, self.configuration).input_revision)

    def test_omitted_capture_members_cannot_be_silently_treated_as_complete_setup(self):
        self.group['status']['omittedResources'] = [{'name': 'missing-worker', 'kind': 'Server'}]
        with self.assertRaisesRegex(OperatorError, 'complete capture'):
            executor.plan(self.api, self.configuration)

    def test_active_provisioning_and_replaced_targets_refuse_execution(self):
        provisioning = executor.mapping(self.server['status'], 'provisioning')
        provisioning['activeRunRef'] = {'uid': 'new-run'}
        with self.assertRaisesRegex(OperatorError, 'installed OS'):
            executor.plan(self.api, self.configuration)
        provisioning.pop('activeRunRef')
        self.server['metadata']['uid'] = 'replacement'
        with self.assertRaisesRegex(OperatorError, 'installation'):
            executor.plan(self.api, self.configuration)

    def test_stale_configuration_cannot_report_ready(self):
        snapshot = copy.deepcopy(self.configuration)
        self.configuration['metadata']['generation'] = 2
        with self.assertRaisesRegex(OperatorError, 'changed before reporting'):
            executor.report(self.api, snapshot, 'Ready', 'Verified')
        self.assertNotIn('phase', self.configuration['status'])

    def test_execution_claims_hosts_and_releases_before_reporting_ready(self):
        resolved = executor.plan(self.api, self.configuration)

        def verify_during_work(*_args):
            self.assertEqual(executor.mapping(self.server['status'], 'operation')['phase'], 'Held')

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(executor, 'checkout', return_value=Path('/fixture/plays/setup.yml')), \
                patch.object(executor, 'prepare_execution'), \
                patch.object(executor, 'run_plays', side_effect=verify_during_work):
            executor.execute(self.api, resolved, Path('/fixture'), Path(directory))
        self.assertEqual(self.configuration['status']['phase'], 'Ready')
        self.assertEqual(executor.mapping(self.server['status'], 'operation')['phase'], 'Released')

    def test_failed_play_never_reports_ready_and_releases_known_stopped_work(self):
        resolved = executor.plan(self.api, self.configuration)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(executor, 'checkout', return_value=Path('/fixture/plays/setup.yml')), \
                patch.object(executor, 'prepare_execution'), \
                patch.object(executor, 'run_plays', side_effect=OperatorError('PlaybookFailed', 'fixture failed')):
            with self.assertRaises(OperatorError):
                executor.execute(self.api, resolved, Path('/fixture'), Path(directory))
        self.assertNotEqual(self.configuration['status']['phase'], 'Ready')
        self.assertEqual(executor.mapping(self.server['status'], 'operation')['phase'], 'Released')

    def test_uncertain_timeout_retains_claim_and_never_reports_ready(self):
        resolved = executor.plan(self.api, self.configuration)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(executor, 'checkout', return_value=Path('/fixture/plays/setup.yml')), \
                patch.object(executor, 'prepare_execution'), \
                patch.object(executor, 'run_plays', side_effect=OperatorError('RemoteWorkUncertain', 'timeout')):
            with self.assertRaises(OperatorError):
                executor.execute(self.api, resolved, Path('/fixture'), Path(directory))
        self.assertNotEqual(self.configuration['status']['phase'], 'Ready')
        self.assertEqual(executor.mapping(self.server['status'], 'operation')['phase'], 'Held')

    def test_inputs_changed_during_play_cannot_become_ready(self):
        resolved = executor.plan(self.api, self.configuration)

        def change_inputs(*_args):
            self.server['metadata']['generation'] = 2

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(executor, 'checkout', return_value=Path('/fixture/plays/setup.yml')), \
                patch.object(executor, 'prepare_execution'), \
                patch.object(executor, 'run_plays', side_effect=change_inputs):
            with self.assertRaisesRegex(OperatorError, 'changed during execution'):
                executor.execute(self.api, resolved, Path('/fixture'), Path(directory))
        self.assertNotEqual(self.configuration['status']['phase'], 'Ready')

    def test_pinned_checkout_uses_committed_not_worktree_content(self):
        patch.stopall()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            (source / 'plays').mkdir(parents=True)
            play = source / 'plays/base.yml'
            play.write_text('# reviewed\n', encoding='utf-8')
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            subprocess.run([
                'git', '-C',
                str(source), 'remote', 'add', 'origin', 'https://example.test/code.git'
            ], check=True)
            subprocess.run(['git', '-C', str(source), 'add', 'plays/base.yml'], check=True)
            subprocess.run([
                'git', '-C',
                str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                'commit', '-q', '-m', 'fixture'
            ], check=True)
            executor.mapping(self.configuration['spec'],
                             'playbook')['revision'] = subprocess.check_output(
                                 ['git', '-C', str(source), 'rev-parse', 'HEAD'],
                                 text=True).strip()
            play.write_text('# unreviewed\n', encoding='utf-8')
            selected = executor.checkout(self.api, self.configuration, source, root / 'checkout')
            self.assertEqual(selected.read_text(encoding='utf-8'), '# reviewed\n')

    def test_target_is_context_not_inventory_selection(self):
        cluster = resource('KubeCluster', 'lab', {})
        self.api.values[('kube-clusters', 'lab')] = cluster
        self.configuration['spec']['targetRef'] = {'kind': 'KubeCluster', 'name': 'lab'}
        resolved = executor.plan(self.api, self.configuration)
        self.assertEqual([server['metadata']['name'] for server in resolved.servers], ['node'])
        self.assertEqual(resolved.target['metadata']['name'], 'lab')

    def test_target_observation_is_bound_to_resolved_lifetime_and_generation(self):
        cluster = resource('KubeCluster', 'lab', {})
        self.api.values[('kube-clusters', 'lab')] = cluster
        self.configuration['spec']['targetRef'] = {'kind': 'KubeCluster', 'name': 'lab'}
        resolved = executor.plan(self.api, self.configuration)
        executor.publish_target_status(self.api, resolved, {'clusterNamespaceUID': 'namespace'})
        self.assertEqual(cluster['status']['observedGeneration'], 1)
        cluster['metadata']['uid'] = 'replacement'
        with self.assertRaisesRegex(OperatorError, 'Target changed'):
            executor.publish_target_status(self.api, resolved, {'clusterNamespaceUID': 'wrong'})
        self.assertEqual(cluster['status']['clusterNamespaceUID'], 'namespace')

    def test_real_client_routes_cluster_observations_to_generic_status(self):
        cluster = resource('KubeCluster', 'lab', {})
        with patch.dict(os.environ, {'STIGMERGY_API_URL': 'https://api.example',
                                     'STIGMERGY_API_TOKEN': 'fixture'}), \
                patch('common.request_json', return_value=cluster) as request:
            API().patch_status(cluster, {'observedGeneration': 1})
            self.assertEqual(request.call_args.args[0],
                             'https://api.example/api/v1alpha1/kube-clusters/lab/status')
            self.assertEqual(request.call_args.args[2], 'PATCH')
            self.assertEqual(request.call_args.args[3]['metadata'], {'uid': 'lab-uid'})
            self.assertEqual(request.call_args.args[4]['If-Match'], '"1"')

    def test_failed_work_never_publishes_target_observations(self):
        resolved = executor.plan(self.api, self.configuration)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(executor, 'checkout', return_value=Path('/fixture/plays/setup.yml')), \
                patch.object(executor, 'prepare_execution'), \
                patch.object(executor, 'run_plays', side_effect=OperatorError('PlaybookFailed', 'fixture')), \
                patch.object(executor, 'publish_target_status') as publish:
            (Path(directory) / 'target-status.json').write_text('{}', encoding='utf-8')
            with self.assertRaises(OperatorError):
                executor.execute(self.api, resolved, Path('/fixture'), Path(directory))
            publish.assert_not_called()

    def test_recreated_target_invalidates_old_success(self):
        cluster = resource('KubeCluster', 'lab', {})
        self.api.values[('kube-clusters', 'lab')] = cluster
        self.configuration['spec']['targetRef'] = {'kind': 'KubeCluster', 'name': 'lab'}
        prior = executor.plan(self.api, self.configuration)
        cluster['metadata']['uid'] = 'replacement-cluster'
        current = executor.plan(self.api, self.configuration)
        self.assertNotEqual(prior.input_revision, current.input_revision)
        self.assertEqual(current.resolved_references[0]['uid'], 'replacement-cluster')

    def test_recreated_repository_and_new_commit_invalidate_success(self):
        prior = executor.plan(self.api, self.configuration).input_revision
        self.api.values[('git-repositories', 'code')]['metadata']['uid'] = 'new-repo'
        self.assertNotEqual(prior, executor.plan(self.api, self.configuration).input_revision)
        prior = executor.plan(self.api, self.configuration).input_revision
        with patch.object(executor, 'source_commit', return_value='b' * 40):
            self.assertNotEqual(prior, executor.plan(self.api, self.configuration).input_revision)

    def test_report_records_execution_identities_and_commit(self):
        resolved = executor.plan(self.api, self.configuration)
        executor.report(self.api, self.configuration, 'Ready', 'Verified', resolved.input_revision,
                        resolved)
        self.assertEqual(self.configuration['status']['playbookRevision'], 'a' * 40)
        self.assertEqual(self.configuration['status']['resolvedReferences'],
                         resolved.resolved_references)

    def test_verified_public_outputs_are_published_after_completed_work(self):
        resolved = executor.plan(self.api, self.configuration)

        def publish_result(_api, _resolved, _source, _play, directory):
            (directory / 'result.json').write_text('{"clusterNamespaceUID":"fixture"}',
                                                   encoding='utf-8')

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(executor, 'checkout', return_value=Path('/fixture/plays/setup.yml')), \
                patch.object(executor, 'prepare_execution'), \
                patch.object(executor, 'run_plays', side_effect=publish_result):
            executor.execute(self.api, resolved, Path('/fixture'), Path(directory))
        self.assertEqual(self.configuration['status']['outputs'],
                         {'clusterNamespaceUID': 'fixture'})

    def test_result_symlink_is_rejected_without_reading_its_target(self):
        resolved = executor.plan(self.api, self.configuration)

        def publish_result(_api, _resolved, _source, _play, directory):
            (directory / 'target.json').write_text('{"notPublic":"fixture"}', encoding='utf-8')
            (directory / 'result.json').symlink_to(directory / 'target.json')

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(executor, 'checkout', return_value=Path('/fixture/plays/setup.yml')), \
                patch.object(executor, 'prepare_execution'), \
                patch.object(executor, 'run_plays', side_effect=publish_result):
            with self.assertRaisesRegex(OperatorError, 'small regular JSON file'):
                executor.execute(self.api, resolved, Path('/fixture'), Path(directory))
        self.assertNotEqual(self.configuration['status']['phase'], 'Ready')

    def test_unreachable_transport_is_uncertain_work_not_a_releasable_failure(self):
        resolved = executor.plan(self.api, self.configuration)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(executor, 'verify_claims'), \
                patch.object(executor, 'run_ansible', return_value=subprocess.CompletedProcess([], 4)):
            with self.assertRaisesRegex(OperatorError, 'SSH became unreachable'):
                executor.run_plays(self.api, resolved, Path('/fixture'),
                                   Path('/fixture/plays/setup.yml'), Path(directory))

    def test_preserves_capture_groups_and_namespaced_context(self):
        resolved = executor.plan(self.api, self.configuration)
        resolved.inventory = {'selected': {'hosts': {'node': {}}, 'vars': {'example': 'value'}}}
        self.server['metadata']['labels'] = {'example.io/service': 'database'}
        # Plan hosts are snapshots, so recreate the plan's member with its labels.
        resolved.servers = [copy.deepcopy(self.server)]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(executor, 'prepare', return_value='public host trust'), \
                patch.object(executor, 'ssh_args', return_value=['-o', 'StrictHostKeyChecking=yes']):
            executor.prepare_execution(self.api, resolved, Path(directory))
            inventory = json.loads((Path(directory) / 'inventory.json').read_text(encoding='utf-8'))
            values = json.loads((Path(directory) / 'values.json').read_text(encoding='utf-8'))
        self.assertIn('node', inventory['selected']['hosts'])
        self.assertEqual(inventory['selected']['vars'], {'example': 'value'})
        self.assertEqual(inventory['all']['hosts']['node']['ansible_host'], '192.0.2.1')
        self.assertEqual(values['configuration_nodes'][0]['labels'],
                         {'example.io/service': 'database'})

    def test_unpinned_checkout_resolves_main_and_keeps_committed_content(self):
        patch.stopall()
        playbook = executor.mapping(self.configuration['spec'], 'playbook')
        playbook.pop('revision')
        repository = self.api.get('git-repositories', 'code')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            (source / 'plays').mkdir(parents=True)
            play = source / 'plays/base.yml'
            play.write_text('# committed\n', encoding='utf-8')
            subprocess.run(['git', 'init', '-q', '--initial-branch=main', str(source)], check=True)
            subprocess.run([
                'git', '-C',
                str(source), 'remote', 'add', 'origin', 'https://example.test/code.git'
            ], check=True)
            subprocess.run(['git', '-C', str(source), 'add', 'plays/base.yml'], check=True)
            subprocess.run([
                'git', '-C',
                str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                'commit', '-q', '-m', 'fixture'
            ], check=True)
            commit = subprocess.check_output(
                ['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
            self.assertEqual(executor.source_commit(repository, playbook, source), commit)
            play.write_text('# dirty\n', encoding='utf-8')
            selected = executor.checkout(self.api, self.configuration, source, root / 'checkout',
                                         commit)
            self.assertEqual(selected.read_text(encoding='utf-8'), '# committed\n')
            repository['spec']['ref'] = 'missing-branch'
            with self.assertRaisesRegex(OperatorError, 'missing from pipeline'):
                executor.source_commit(repository, playbook, source)
            repository['spec']['ref'] = 'main'
            repository['spec']['url'] = 'https://example.test/different.git'
            with self.assertRaisesRegex(OperatorError, 'differs from pipeline'):
                executor.source_commit(repository, playbook, source)
            with self.assertRaisesRegex(OperatorError, 'changed before checkout'):
                executor.checkout(self.api, self.configuration, source, root / 'changed', 'b' * 40)

    def test_recreated_target_during_execution_cannot_become_ready(self):
        cluster = resource('KubeCluster', 'lab', {})
        self.api.values[('kube-clusters', 'lab')] = cluster
        self.configuration['spec']['targetRef'] = {'kind': 'KubeCluster', 'name': 'lab'}
        resolved = executor.plan(self.api, self.configuration)

        def replace_target(*_args):
            cluster['metadata']['uid'] = 'replacement-cluster'

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(executor, 'checkout', return_value=Path('/fixture/plays/setup.yml')), \
                patch.object(executor, 'prepare_execution'), \
                patch.object(executor, 'run_plays', side_effect=replace_target):
            with self.assertRaisesRegex(OperatorError, 'changed during execution'):
                executor.execute(self.api, resolved, Path('/fixture'), Path(directory))
        self.assertNotEqual(self.configuration['status']['phase'], 'Ready')

    def test_pinned_checkout_refuses_path_escape(self):
        patch.stopall()
        playbook = executor.mapping(self.configuration['spec'], 'playbook')
        playbook['path'] = '../outside.yml'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # An actual Git fixture validates archive handling, not just a mocked hash.
            source = root / 'source'
            source.mkdir()
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            subprocess.run([
                'git', '-C',
                str(source), 'remote', 'add', 'origin', 'https://example.test/code.git'
            ], check=True)
            subprocess.run([
                'git', '-C',
                str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                'commit', '-q', '--allow-empty', '-m', 'fixture'
            ], check=True)
            playbook['revision'] = subprocess.check_output(
                ['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
            with self.assertRaisesRegex(OperatorError, 'reviewed plays directory'):
                executor.checkout(self.api, self.configuration, source, root / 'checkout')
