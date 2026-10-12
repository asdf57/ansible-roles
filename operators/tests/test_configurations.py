"""Configuration safety tests never connect to or mutate managed nodes."""
import copy
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
        collection = {'Configuration': 'configurations', 'Server': 'servers'}[server['kind']]
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
                    'uid': 'node-uid'
                },
                'inventoryCaptureGroupRef': {
                    'name': 'group',
                    'uid': 'group-uid'
                },
                'playbook': {
                    'repositoryRef': {
                        'name': 'code',
                        'uid': 'code-uid'
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
        patch.object(executor, 'capture_inventory', return_value=(self.group, {})).start()
        patch.object(executor, 'resolved_inventory_hosts', return_value={'node': {}}).start()

    def test_ordering_and_cycles(self):
        dependent = copy.deepcopy(self.configuration)
        dependent['metadata']['name'] = 'dependent'
        dependent['spec']['dependsOn'] = [{'name': 'base', 'uid': 'base-uid'}]
        self.assertEqual([
            item['metadata']['name']
            for item in executor.dependency_order([dependent, self.configuration])
        ], ['base', 'dependent'])
        self.configuration['spec']['dependsOn'] = [{'name': 'dependent', 'uid': 'dependent-uid'}]
        with self.assertRaisesRegex(OperatorError, 'cycle'):
            executor.dependency_order([dependent, self.configuration])

    def test_dependencies_require_current_input_revision(self):
        dependent = copy.deepcopy(self.configuration)
        dependent['metadata']['name'] = 'applications'
        dependent['spec']['dependsOn'] = [{'name': 'base', 'uid': 'base-uid'}]
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

    def test_active_provisioning_and_replaced_targets_refuse_execution(self):
        provisioning = executor.mapping(self.server['status'], 'provisioning')
        provisioning['activeRunRef'] = {'uid': 'new-run'}
        with self.assertRaisesRegex(OperatorError, 'installed OS'):
            executor.plan(self.api, self.configuration)
        provisioning.pop('activeRunRef')
        self.server['metadata']['uid'] = 'replacement'
        with self.assertRaisesRegex(OperatorError, 'replaced'):
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

    def test_cluster_uid_and_role_membership(self):
        cluster = resource('KubeCluster', 'lab', {})
        self.server['metadata']['labels'] = {
            'homelab.io/kube-cluster': 'lab',
            'homelab.io/kube-role': 'control-plane'
        }
        with self.assertRaisesRegex(OperatorError, 'Cluster UID'):
            executor.cluster_members(self.api, cluster)
        self.server['metadata']['annotations'] = {'homelab.io/kube-cluster-uid': 'lab-uid'}
        self.assertEqual(
            executor.cluster_members(self.api, cluster)[0]['metadata']['uid'], 'node-uid')

    def test_pinned_checkout_refuses_path_escape(self):
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
