"""Interactive context tests never connect to nodes or use real credentials."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import API
from models import JSONObject, Resource
import runner_kubernetes as runner


def fixture(kind: str, name: str, spec: JSONObject | None = None) -> Resource:
    """Build a concrete disposable resource envelope."""
    return {
        'kind': kind,
        'metadata': {
            'name': name,
            'uid': name + '-uid',
            'resourceVersion': '1',
            'generation': 1
        },
        'spec': spec or {},
        'status': {},
    }


def kubeconfig() -> JSONObject:
    """Use synthetic strings, not a usable Kubernetes identity."""
    return {
        'apiVersion':
        'v1',
        'kind':
        'Config',
        'current-context':
        'admin',
        'clusters': [{
            'name': 'lab',
            'cluster': {
                'server': 'https://lab.example:6443',
                'certificate-authority-data': 'fake-ca'
            }
        }],
        'users': [{
            'name': 'admin',
            'user': {
                'client-certificate-data': 'fake-cert',
                'client-key-data': 'fake-key'
            }
        }],
        'contexts': [{
            'name': 'admin',
            'context': {
                'cluster': 'lab',
                'user': 'admin'
            }
        }],
    }


class FixtureAPI(API):
    """Read-only in-memory inventory and cluster fixtures."""

    def __init__(self):
        self.reads: list[tuple[str, str]] = []
        self.group = fixture('InventoryCaptureGroup', 'managed')
        self.group['metadata']['labels'] = {runner.CLUSTER_LABEL: 'lab'}
        self.group['status'] = {
            'observedGeneration': 1,
            'inventory': {
                'all': {
                    'hosts': {
                        'control': {},
                        'worker': {},
                        'unrelated': {}
                    }
                }
            }
        }
        self.cluster = fixture('KubeCluster', 'lab', {
            'controlPlaneEndpoint': 'lab.example:6443',
            'kubernetesVersion': '1.37.1'
        })
        self.servers = {
            name: fixture('Server', name)
            for name in ('control', 'worker', 'unrelated')
        }
        self.servers['control']['metadata']['labels'] = {
            runner.CLUSTER_LABEL: 'lab',
            'homelab.io/kube-role': 'control-plane'
        }
        self.servers['worker']['metadata']['labels'] = {
            runner.CLUSTER_LABEL: 'lab',
            'homelab.io/kube-role': 'worker'
        }

    def get(self, collection: str, name: str) -> Resource:
        """Track reads and refuse every unexpected resource collection."""
        self.reads.append((collection, name))
        if collection == 'inventory-capture-groups':
            return copy.deepcopy(self.group)
        if collection == 'kube-clusters':
            return copy.deepcopy(self.cluster)
        if collection == 'servers':
            return copy.deepcopy(self.servers[name])
        raise AssertionError('Unexpected resource read')


class ContextTests(unittest.TestCase):
    """Cover context selection, lifetime checks, private storage and mode isolation."""

    def test_labeled_group_installs_private_named_context(self):
        api = FixtureAPI()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                runner, 'fetch_context',
                return_value=kubeconfig()) as fetch, patch.object(runner,
                                                                  'install_kubectl') as install:
            home = Path(temporary)
            self.assertTrue(runner.setup_context(api, 'managed', home))
            fetch.assert_called_once_with(api.servers['control'], api.cluster)
            install.assert_called_once_with('1.37.1', home / '.local' / 'bin')
            config = home / '.kube' / 'config'
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(config.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                json.loads(config.read_text(encoding='utf-8'))['current-context'], 'lab')

    def test_unlabeled_group_does_not_fetch_cluster_or_overwrite_user_config(self):
        api = FixtureAPI()
        api.group['metadata']['labels'] = {}
        with tempfile.TemporaryDirectory() as temporary, patch.object(runner,
                                                                      'fetch_context') as fetch:
            home = Path(temporary)
            (home / '.kube').mkdir()
            (home / '.kube' / 'config').write_text('user config', encoding='utf-8')
            self.assertFalse(runner.setup_context(api, 'managed', home))
            fetch.assert_not_called()
            self.assertEqual(api.reads, [('inventory-capture-groups', 'managed')])
            self.assertEqual((home / '.kube' / 'config').read_text(encoding='utf-8'), 'user config')

    def test_refresh_removes_owned_context_when_label_is_removed(self):
        api = FixtureAPI()
        api.group['metadata']['labels'] = {}
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            directory = home / '.kube'
            directory.mkdir()
            (directory / 'config').write_text('old credentials', encoding='utf-8')
            (directory / 'homelab-context.json').write_text('{}', encoding='utf-8')
            self.assertFalse(runner.setup_context(api, 'managed', home))
            self.assertFalse((directory / 'config').exists())

    def test_missing_or_multiple_control_planes_are_refused(self):
        api = FixtureAPI()
        inventory: JSONObject = {'all': {'hosts': {'worker': {}}}}
        with self.assertRaises(ValueError):
            runner.control_plane(api, inventory, api.cluster)
        api.servers['worker']['metadata']['labels']['homelab.io/kube-role'] = 'control-plane'
        with self.assertRaises(ValueError):
            runner.control_plane(api, {'all': {
                'hosts': {
                    'worker': {},
                    'control': {}
                }
            }}, api.cluster)

    def test_context_rejects_plugins_tls_bypasses_and_wrong_endpoints(self):
        cluster = FixtureAPI().cluster
        replacements: list[tuple[str, JSONObject]] = [('clusters', {
            'server': 'https://attacker:6443',
            'certificate-authority-data': 'ca'
        }), ('clusters', {
            'server': 'https://lab.example:6443',
            'insecure-skip-tls-verify': True
        }), ('users', {
            'exec': {
                'command': 'evil'
            }
        })]
        for section, replacement in replacements:
            config = kubeconfig()
            config[section] = [{
                'name': 'lab' if section == 'clusters' else 'admin',
                'cluster' if section == 'clusters' else 'user': replacement
            }]
            with self.subTest(section=section,
                              replacement=replacement), self.assertRaises(ValueError):
                runner.validate_context(config, cluster)

    def test_automation_modes_never_read_api(self):
        for mode in ('command', 'operator', 'init'):
            with patch.dict(os.environ, {'CONTAINER_MODE': mode}), patch.object(runner,
                                                                                'API') as api:
                self.assertEqual(runner.main(), 0)
                api.assert_not_called()

    def test_replaced_cluster_is_refused_before_installing_credentials(self):
        api = FixtureAPI()

        def replaced_context(_server: Resource, _cluster: Resource) -> JSONObject:
            """Simulate name reuse while the SSH response is in flight."""
            api.cluster['metadata']['uid'] = 'replacement-uid'
            return kubeconfig()

        with tempfile.TemporaryDirectory() as temporary, patch.object(
                runner, 'fetch_context',
                side_effect=replaced_context), patch.object(runner, 'install_kubectl') as install:
            home = Path(temporary)
            with self.assertRaises(ValueError):
                runner.setup_context(api, 'managed', home)
            install.assert_not_called()
            self.assertFalse((home / '.kube' / 'config').exists())

    def test_failed_refresh_never_leaves_old_credentials(self):
        api = FixtureAPI()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                runner, 'fetch_context', side_effect=ValueError('not ready')):
            home = Path(temporary)
            directory = home / '.kube'
            directory.mkdir()
            (directory / 'config').write_text('old credentials', encoding='utf-8')
            (directory / 'homelab-context.json').write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                runner.setup_context(api, 'managed', home)
            self.assertFalse((directory / 'config').exists())

    def test_exact_client_checksum_is_required(self):
        binary = b'fake client binary'
        checksum = hashlib.sha256(binary).hexdigest().encode()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                runner.platform, 'machine', return_value='x86_64'), patch.object(
                    runner, 'urlopen', side_effect=[io.BytesIO(checksum),
                                                    io.BytesIO(binary)]):
            directory = Path(temporary)
            runner.install_kubectl('1.37.1', directory)
            self.assertEqual((directory / 'kubectl').read_bytes(), binary)
            self.assertEqual((directory / 'kubectl').stat().st_mode & 0o777, 0o700)
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                runner.platform, 'machine', return_value='x86_64'), patch.object(
                    runner, 'urlopen', side_effect=[io.BytesIO(checksum),
                                                    io.BytesIO(b'corrupt')]):
            with self.assertRaises(ValueError):
                runner.install_kubectl('1.37.1', Path(temporary))
            self.assertFalse((Path(temporary) / 'kubectl').exists())


if __name__ == '__main__':
    unittest.main()
