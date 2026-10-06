import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_operators import API, server
import provisioning as p
import runner_trust
import ssh_host_keys


def host():
    value = server()
    key = value['status']['hostSSH']
    key.update(phase='Ready', installedKeyPairRef=key['keyPairRef'], installedFingerprint=key['fingerprint'])
    value['spec'] = {'provisioning': {'enabled': True, 'reprovision': 0, 'targetDisk': '/dev/disk/by-id/disk'},
                     'operatingSystem': {'distribution': 'arch', 'version': 'rolling', 'architecture': 'amd64', 'bootMode': 'uefi'}}
    value['status']['machineRef'] = {'name': 'machine', 'uid': 'machine-uid'}
    value['status']['networking']['management']['interface'] = {'name': 'enp1s0', 'mac': '00:11:22:33:44:55'}
    return value


def facts():
    return {'uefi': True, 'secureBoot': False, 'partitioned': False, 'mounted': False, 'live': True,
            'liveBuildID': 'build', 'bootID': 'live-boot', 'disk': {'serial': 'serial', 'size': 10000000000},
            'marker': None, 'grubReady': True, 'grubEnvironment': '', 'rootUUID': '', 'rootType': 'overlay',
            'os': {'ID': 'arch'}, 'rootOnTarget': False}


def snapshot(value):
    return {'serverUID': value['metadata']['uid'], 'machineRef': value['status']['machineRef'],
            'keyPairRef': value['status']['hostSSH']['keyPairRef'], 'isoRef': {'name': 'iso', 'uid': 'iso-uid'},
            'authorityRef': {'name': 'ca', 'uid': 'ca-uid'}, 'trustBundleDigest': 'digest', 'isoBuildID': 'build',
            'distribution': 'arch', 'artifacts': [], 'targetDisk': '/dev/disk/by-id/disk', 'diskIdentity': facts()['disk'],
            'codeRevision': 'revision', 'planDigest': 'plan',
            'bootMAC': '00:11:22:33:44:55',
            'inputs': {'serverSpec': {'operatingSystem': value['spec']['operatingSystem']},
                       'storage': {}, 'users': [], 'disableEEE': True}}


def claimed(phase='PreparingBoot'):
    value = host()
    value['status']['provisioning'] = {'provisioned': False, 'observedReprovision': 0, 'requestedReprovision': 0,
                                      'phase': phase, 'attemptID': 'attempt', 'snapshot': snapshot(value),
                                      'maintenance': True, 'sourceBootID': 'live-boot', 'liveBootID': 'live-boot'}
    return value


class ProvisioningTests(unittest.TestCase):
    def test_eligibility_never_replays_failed_or_successful_requests(self):
        value = host()
        self.assertTrue(p.eligible(value))
        value['status']['provisioning'] = {'provisioned': True, 'phase': 'Succeeded', 'attemptID': 'old',
                                          'requestedReprovision': 0, 'observedReprovision': 0}
        self.assertFalse(p.eligible(value))
        value['metadata']['generation'] += 1
        self.assertFalse(p.eligible(value))
        value['spec']['provisioning']['reprovision'] = 1
        self.assertTrue(p.eligible(value))
        value['status']['provisioning'].update(phase='Blocked', requestedReprovision=1)
        self.assertFalse(p.eligible(value))
        value['spec']['provisioning']['reprovision'] = 2
        self.assertTrue(p.eligible(value))
        value['status']['provisioning']['maintenance'] = True
        self.assertFalse(p.eligible(value))

    def test_initial_disk_safety_and_explicit_replacement(self):
        data = facts(); p.validate_disk(data, 0, True)
        data['partitioned'] = True
        with self.assertRaises(RuntimeError): p.validate_disk(data, 0, True)
        p.validate_disk(data, 1, True)
        for field in ('mounted', 'secureBoot'):
            unsafe = copy.deepcopy(data); unsafe[field] = True
            with self.assertRaises(RuntimeError): p.validate_disk(unsafe, 1, True)

    def test_interrupted_installation_never_runs_ansible_again(self):
        api = API(); api.server = claimed('Installing')
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'run_stage') as run:
            p.reconcile(api, api.get('servers', 'node'), {}, Path(tmp), 'revision')
            run.assert_not_called()
        self.assertEqual(api.server['status']['provisioning']['phase'], 'Blocked')
        self.assertTrue(api.server['status']['provisioning']['maintenance'])

    def test_preflight_does_not_mutate_an_active_attempt(self):
        api = API(); api.server = claimed('Installing')
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'run_stage') as run:
            p.reconcile(api, api.get('servers', 'node'), {}, Path(tmp), 'revision', True)
            run.assert_not_called()
        self.assertEqual(api.writes, [])

    def test_current_allows_metadata_not_destructive_input_changes(self):
        api = API(); api.server = claimed()
        before = api.get('servers', 'node')
        api.server['metadata']['generation'] += 1
        api.server['metadata']['labels'] = {'safe': 'edit'}
        p.current(api, before)
        api.server['spec']['operatingSystem']['distribution'] = 'debian'
        with self.assertRaises(RuntimeError): p.current(api, before)

    def test_normal_and_ssh_runners_respect_reservation(self):
        api = API(); api.server = claimed()
        with self.assertRaises(RuntimeError):
            runner_trust.prepare(api, {'all': {'hosts': {'node': {'ansible_host': '127.0.0.1'}}}})
        with tempfile.TemporaryDirectory() as tmp, patch.object(ssh_host_keys, 'host_private_key') as private:
            ssh_host_keys.reconcile(api, api.server, '127.0.0.1', Path(tmp))
            private.assert_not_called()

    def test_installed_attestation_is_not_just_port_22(self):
        value = claimed('Verifying')
        data = facts()
        data.update(live=False, bootID='installed-boot', rootUUID='root-uuid', rootType='ext4', rootOnTarget=True,
                    marker={'serverUID': 'server-uid', 'attemptID': 'attempt', 'reprovision': 0,
                            'planDigest': 'plan', 'rootUUID': 'root-uuid'})
        p.verify_installed(value, data)
        for mutate in (lambda d: d.update(live=True), lambda d: d.update(rootOnTarget=False),
                       lambda d: d.update(bootID='live-boot'), lambda d: d['marker'].update(attemptID='old'),
                       lambda d: d.update(grubEnvironment='next_entry=homelab-netboot')):
            invalid = copy.deepcopy(data); mutate(invalid)
            with self.assertRaises(RuntimeError): p.verify_installed(value, invalid)

    def test_stale_live_bootstrap_checkpoints_before_kexec_and_never_installs_early(self):
        api = API(); api.server = claimed()
        api.server['status']['provisioning']['liveBootID'] = None
        old = facts(); old['liveBuildID'] = ''
        stages = []
        def stage(value, directory, known, name):
            stages.append((name, value['status']['provisioning']['phase']))
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'inspect', return_value=old), \
                patch.object(p, 'verify_dependencies'), patch.object(p, 'drain_commands'), \
                patch.object(p, 'artifact_preflight'), patch.object(p, 'run_stage', side_effect=stage), \
                patch.object(p, 'await_session', side_effect=p.OperatorError('ProvisioningBlocked', 'fixture wait')):
            with self.assertRaises(p.OperatorError):
                p.reconcile(api, api.get('servers', 'node'), {}, Path(tmp), 'revision')
        self.assertEqual(stages, [('prime', 'PreparingBoot'), ('refresh-prepare', 'PreparingBoot'),
                                  ('refresh-boot', 'AwaitingLive')])
        self.assertIsNone(api.server['status']['provisioning'].get('liveBootID'))
        self.assertTrue(api.server['status']['provisioning']['maintenance'])

    def test_regular_reboot_uses_managed_node_playbook(self):
        value = claimed()
        with patch.object(p, 'run_stage') as stage:
            p.reboot(value, Path('/private/known_hosts'))
        stage.assert_called_once_with(value, Path('/private'), Path('/private/known_hosts'), 'reboot')

    def test_successful_live_first_flow_executes_install_once(self):
        api = API(); api.server = host()
        def observed(value, known):
            data = facts()
            attempt = value.get('status', {}).get('provisioning', {})
            if attempt.get('phase') == 'AwaitingInstalled':
                data['stagedBootReady'] = True
                data['stagedMarker'] = {'serverUID': 'server-uid', 'attemptID': attempt['attemptID'],
                                        'reprovision': 0, 'planDigest': 'plan'}
            if attempt.get('phase') == 'Verifying':
                return installed(value)[0]
            return data
        def installed(value, *args, **kwargs):
            data = facts(); attempt = value['status']['provisioning']
            data.update(live=False, bootID='installed-boot', rootUUID='root-uuid', rootType='ext4', rootOnTarget=True,
                        marker={'serverUID': 'server-uid', 'attemptID': attempt['attemptID'], 'reprovision': 0,
                                'planDigest': 'plan', 'rootUUID': 'root-uuid'})
            return data, Path('fixture-known')
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'inspect', side_effect=observed), \
                patch.object(p, 'plan', return_value=snapshot(api.server)), patch.object(p, 'verify_dependencies'), \
                patch.object(p, 'drain_commands'), patch.object(p, 'artifact_preflight'), \
                patch.object(p, 'enroll_live', side_effect=lambda api, value, facts, known, directory: known), \
                patch.object(p, 'run_stage') as stage, patch.object(p, 'reboot') as reboot, \
                patch.object(p, 'await_session', side_effect=lambda api, value, *a, **kw: installed(value)), \
                patch.object(p.subprocess, 'run') as service_check, \
                patch.dict(os.environ, {'ANSIBLE_CERTIFICATE_FILE': 'fixture-cert', 'ANSIBLE_PRIVATE_KEY_FILE': 'fixture-key'}):
            service_check.return_value.returncode = 0
            p.reconcile(api, api.get('servers', 'node'), {}, Path(tmp), 'revision')
        self.assertEqual([call.args[3] for call in stage.call_args_list], ['install', 'post'])
        self.assertEqual(reboot.call_count, 1)
        status = api.server['status']['provisioning']
        self.assertEqual(status['phase'], 'Succeeded')
        self.assertEqual(status['observedReprovision'], 0)
        self.assertTrue(status['provisioned'])
        self.assertFalse(status['maintenance'])
        self.assertFalse(p.eligible(api.server))


if __name__ == '__main__':
    unittest.main()
