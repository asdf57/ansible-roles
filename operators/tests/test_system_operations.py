"""No remote actions: safety contract for ordinary system-operation Commands."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import system_operations
from test_operators import API


def fixture():
    """Create a fake UID-bound Server with verified public SSH identity."""
    api = API()
    value = api.server
    value['spec'] = {}
    value['status']['machineRef'] = {'name': 'machine', 'uid': 'machine-uid'}
    host = value['status']['hostSSH']
    host.update(phase='Ready', installedKeyPairRef=host['keyPairRef'],
                installedFingerprint=host['fingerprint'])
    return api


class SystemOperationTests(unittest.TestCase):
    """Exercise selection and trust without executing a playbook."""

    def test_identity_and_maintenance_gates(self):
        """Reject changed lifetimes and existing maintenance ownership."""
        for mutate in (lambda value: value['metadata'].update(uid='replacement'),
                       lambda value: value['status']['machineRef'].update(uid='replacement'),
                       lambda value: value['status'].update(provisioning={'activeRunRef': {}}),
                       lambda value: value['status'].update(provisioning={'maintenance': True}),
                       lambda value: value['spec'].update(reconciliation={'paused': True})):
            api = fixture()
            mutate(api.server)
            with self.assertRaises(RuntimeError):
                system_operations.selected_server(api, 'node', 'server-uid', 'machine-uid')

    def test_single_host_and_strict_transport(self):
        """Inherited inventory cannot redirect or expand the selected target."""
        api = fixture()
        hosts = {
            'node': {
                'ansible_host': 'attacker',
                'ansible_connection': 'local',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            },
            'other': {}
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
                os.environ, {
                    'ANSIBLE_CERTIFICATE_FILE': '/private/cert',
                    'ANSIBLE_PRIVATE_KEY_FILE': '/private/key'
                }), patch.object(system_operations, 'capture_inventory',
                                 return_value=({}, {})), patch.object(system_operations,
                                                                      'resolved_inventory_hosts',
                                                                      return_value=hosts):
            inventory = system_operations.prepare_target(api, 'group', 'node', 'server-uid',
                                                         'machine-uid', Path(tmp))
            self.assertEqual(set(inventory['all']['hosts']), {'node'})
            values = inventory['all']['hosts']['node']
            self.assertEqual(values['ansible_host'], '127.0.0.1')
            self.assertEqual(values['ansible_connection'], 'ssh')
            self.assertEqual(values['ansible_ssh_common_args'], '')
            self.assertIn('StrictHostKeyChecking=yes', values['ansible_ssh_args'])
            self.assertIn('HostKeyAlias=server-server-uid', values['ansible_ssh_args'])

    def test_missing_capture_target_and_unverified_key_fail_closed(self):
        """Require capture membership and the verified managed key."""
        api = fixture()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                system_operations, 'capture_inventory',
                return_value=({}, {})), patch.object(system_operations, 'resolved_inventory_hosts',
                                                     return_value={}):
            with self.assertRaises(RuntimeError):
                system_operations.prepare_target(api, 'group', 'node', 'server-uid', 'machine-uid',
                                                 Path(tmp))
        api.server['status']['hostSSH']['installedFingerprint'] = 'wrong'
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                system_operations, 'capture_inventory',
                return_value=({}, {})), patch.object(system_operations, 'resolved_inventory_hosts',
                                                     return_value={'node': {}}):
            with self.assertRaises(RuntimeError):
                system_operations.prepare_target(api, 'group', 'node', 'server-uid', 'machine-uid',
                                                 Path(tmp))

    def test_identity_changes_between_preparation_checks_fail(self):
        """Do not proceed when provisioning reserves the target during preparation."""
        api = fixture()
        changed = copy.deepcopy(api.server)
        changed['status']['provisioning'] = {'maintenance': True}
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                system_operations, 'capture_inventory', return_value=({}, {})), patch.object(
                    system_operations, 'resolved_inventory_hosts',
                    return_value={'node': {}}), patch.object(
                        api, 'get',
                        side_effect=[copy.deepcopy(api.server),
                                     copy.deepcopy(api.server), changed]):
            with self.assertRaises(RuntimeError):
                system_operations.prepare_target(api, 'group', 'node', 'server-uid', 'machine-uid',
                                                 Path(tmp))


if __name__ == '__main__':
    unittest.main()
