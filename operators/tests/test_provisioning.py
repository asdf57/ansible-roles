"""Run ownership, no-replay and physical attestation regression tests."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from test_operators import server
import provisioning as p
import runner_trust
import ssh_host_keys


def host():
    value = server()
    key = value['status']['hostSSH']
    key.update(phase='Ready', installedKeyPairRef=key['keyPairRef'],
               installedFingerprint=key['fingerprint'])
    value['spec'] = {
        'provisioning': {
            'enabled': True
        },
        'operatingSystem': {
            'distribution': 'arch',
            'version': 'rolling',
            'architecture': 'amd64',
            'bootMode': 'uefi'
        }
    }
    value['status']['machineRef'] = {'name': 'machine', 'uid': 'machine-uid'}
    value['status']['networking']['management']['interface'] = {
        'name': 'enp1s0',
        'mac': '00:11:22:33:44:55'
    }
    value['status']['provisioning'] = {
        'provisioned': False,
        'maintenance': True,
        'activeRunRef': {
            'name': 'run',
            'uid': 'run-uid'
        }
    }
    return value


def facts():
    return {
        'uefi': True,
        'secureBoot': False,
        'partitioned': True,
        'mounted': False,
        'live': True,
        'liveBuildID': 'build',
        'bootID': 'live-boot',
        'disk': {
            'serial': 'serial',
            'wwn': 'wwn',
            'size': 10000000000,
            'model': 'SSD',
            'tran': 'sata'
        },
        'targetDisk': '/dev/disk/by-id/disk',
        'marker': None,
        'grubReady': True,
        'grubEnvironment': '',
        'rootUUID': '',
        'rootType': 'overlay',
        'os': {
            'ID': 'arch'
        },
        'rootOnTarget': False
    }


def snapshot(value):
    return {
        'serverUID': value['metadata']['uid'],
        'machineRef': value['status']['machineRef'],
        'keyPairRef': value['status']['hostSSH']['keyPairRef'],
        'isoRef': {
            'name': 'iso',
            'uid': 'iso-uid'
        },
        'authorityRef': {
            'name': 'ca',
            'uid': 'ca-uid'
        },
        'trustBundleDigest': 'digest',
        'isoBuildID': 'build',
        'distribution': 'arch',
        'artifacts': [],
        'targetDisk': facts()['targetDisk'],
        'diskIdentity': facts()['disk'],
        'codeRevision': 'revision',
        'planDigest': 'plan',
        'bootMAC': '00:11:22:33:44:55',
        'inputs': {
            'serverSpec': {
                'operatingSystem': value['spec']['operatingSystem']
            },
            'storage': {},
            'users': [],
            'disableEEE': True
        }
    }


class RunAPI:
    """Separate CAS-versioned Server and run objects, like the real API."""

    def __init__(self, phase='PreparingBoot'):
        self.server = host()
        self.run = {
            'metadata': {
                'name': 'run',
                'uid': 'run-uid',
                'resourceVersion': '1'
            },
            'spec': {
                'serverRef': {
                    'name': 'node',
                    'uid': 'server-uid'
                },
                'machineRef': self.server['status']['machineRef'],
                'storage': {
                    'disks': [{
                        'deviceID': 'wwn:wwn',
                        'role': 'system'
                    }]
                }
            },
            'status': {
                'phase': phase,
                'maintenance': True,
                'selectedDisk': facts()['disk']
            }
        }
        if phase != 'Pending':
            self.run['status'].update(attemptID='run-uid', snapshot=snapshot(self.server),
                                      sourceBootID='live-boot', liveBootID='live-boot')
        self.writes = []

    def get(self, collection, _name):
        return copy.deepcopy(self.run if collection == 'provisioning-runs' else self.server)

    def list(self, collection):
        return [copy.deepcopy(self.server)] if collection == 'servers' else []

    def patch_status(self, value, fields):
        target = self.run if value['metadata']['uid'] == 'run-uid' else self.server
        if value['metadata']['resourceVersion'] != target['metadata']['resourceVersion']:
            raise HTTPError('', 409, 'Conflict', {}, None)
        self.writes.append((target['metadata']['uid'], copy.deepcopy(fields)))

        def merge(destination, updates):
            for key, item in updates.items():
                if item is None:
                    destination.pop(key, None)
                elif isinstance(item, dict):
                    merge(destination.setdefault(key, {}), item)
                else:
                    destination[key] = copy.deepcopy(item)

        merge(target['status'], fields)
        target['metadata']['resourceVersion'] = str(int(target['metadata']['resourceVersion']) + 1)
        return copy.deepcopy(target)

    def view(self):
        return p.bind_run(self, self.get('servers', 'node'))


class ProvisioningTests(unittest.TestCase):

    def test_eligibility_requires_an_explicit_run_not_os_edits(self):
        value = host()
        value['status']['provisioning'] = {'provisioned': False}
        self.assertFalse(p.eligible(value))
        value['metadata']['generation'] += 1
        self.assertFalse(p.eligible(value))
        value['status']['provisioning']['activeRunRef'] = {'name': 'run', 'uid': 'run-uid'}
        self.assertTrue(p.eligible(value))

    def test_explicit_run_still_requires_unmounted_live_target_and_secure_boot_off(self):
        data = facts()
        p.validate_disk(data, True)
        for field, invalid in (('mounted', True), ('secureBoot', True), ('live', False),
                               ('secureBoot', None), ('firmwareBootNext', '0001')):
            bad = copy.deepcopy(data)
            bad[field] = invalid
            with self.assertRaises(p.OperatorError):
                p.validate_disk(bad, True)

    def test_interrupted_installation_never_runs_ansible_again(self):
        api = RunAPI('Installing')
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'run_stage') as run:
            p.reconcile(api, api.view(), {}, Path(tmp), 'revision')
            run.assert_not_called()
        self.assertEqual(api.run['status']['phase'], 'Blocked')
        self.assertTrue(api.run['status']['maintenance'])
        self.assertTrue(p.eligible(api.server))

    def test_preflight_never_claims_or_mutates_active_attempt(self):
        api = RunAPI('Installing')
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'run_stage') as run:
            p.reconcile(api, api.view(), {}, Path(tmp), 'revision', True)
            run.assert_not_called()
        self.assertEqual(api.writes, [])

    def test_metadata_edits_allowed_but_os_run_or_binding_changes_block(self):
        api = RunAPI()
        before = api.view()
        api.server['metadata']['generation'] += 1
        p.current(api, before)
        api.server['spec']['operatingSystem']['distribution'] = 'debian'
        with self.assertRaises(p.OperatorError):
            p.current(api, before)
        api = RunAPI()
        before = api.view()
        api.run['metadata']['uid'] = 'replacement'
        with self.assertRaises(p.OperatorError):
            p.current(api, before)

    def test_normal_and_ssh_operators_respect_pending_reservation(self):
        api = RunAPI('Pending')
        with self.assertRaises(RuntimeError):
            runner_trust.prepare(api, {'all': {'hosts': {'node': {'ansible_host': '127.0.0.1'}}}})
        with tempfile.TemporaryDirectory() as tmp, patch.object(ssh_host_keys,
                                                                'host_private_key') as private:
            ssh_host_keys.reconcile(api, api.server, '127.0.0.1', Path(tmp))
            private.assert_not_called()

    def test_installed_attestation_checks_run_uid_boot_disk_and_marker(self):
        value = RunAPI('Verifying').view()
        data = self.installed(value)
        p.verify_installed(value, data)
        for mutate in (lambda d: d.update(live=True), lambda d: d.update(rootOnTarget=False),
                       lambda d: d.update(bootID='live-boot'),
                       lambda d: d['marker'].update(runUID='other'),
                       lambda d: d['disk'].update(serial='other'),
                       lambda d: d.update(grubEnvironment='next_entry=homelab-netboot')):
            invalid = copy.deepcopy(data)
            mutate(invalid)
            with self.assertRaises(p.OperatorError):
                p.verify_installed(value, invalid)

    @staticmethod
    def installed(value):
        data = facts()
        attempt = value['status']['provisioning']
        data.update(
            live=False, bootID='installed-boot', rootUUID='root-uuid', rootType='ext4',
            rootOnTarget=True, marker={
                'serverUID': 'server-uid',
                'attemptID': attempt['attemptID'],
                'runUID': 'run-uid',
                'planDigest': 'plan',
                'rootUUID': 'root-uuid'
            })
        return data

    def test_live_handoff_keeps_both_pins_until_reload_cleanup(self):
        api = RunAPI('AwaitingLive')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            known = root / 'known_hosts'
            bootstrap = 'server-server-uid ssh-ed25519 bootstrap-fixture\n'
            known.write_text(bootstrap)
            desired = api.server['status']['hostSSH']['publicKey']

            def play(_server, _host, _root, trust, _name, _variables):
                self.assertIn(bootstrap.strip(), trust.read_text().splitlines())
                self.assertIn('server-server-uid ' + p.public_key(desired),
                              trust.read_text().splitlines())
            with patch.object(p, 'host_private_key', return_value='private-fixture'), \
                    patch.object(p.subprocess, 'check_output', return_value=desired), \
                    patch.object(ssh_host_keys, 'run_play', side_effect=play), patch.object(p, 'ssh_probe'):
                result = p.enroll_live(api, api.view(), facts(), known, root)
            self.assertEqual(result.read_text(),
                             'server-server-uid ' + p.public_key(desired) + '\n')

    def test_stale_live_bootstrap_checkpoints_before_kexec_never_installs_early(self):
        api = RunAPI()
        api.run['status']['liveBootID'] = None
        old = facts()
        old['liveBuildID'] = ''
        stages = []

        def stage(value, _directory, _known, name):
            stages.append((name, value['status']['provisioning']['phase']))
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'inspect', return_value=old), \
                patch.object(p, 'verify_dependencies'), patch.object(p, 'drain_commands'), \
                patch.object(p, 'artifact_preflight'), patch.object(p, 'run_stage', side_effect=stage), \
                patch.object(p, 'await_session', side_effect=p.OperatorError('ProvisioningBlocked', 'fixture wait')):
            with self.assertRaises(p.OperatorError):
                p.reconcile(api, api.view(), {}, Path(tmp), 'revision')
        self.assertEqual(stages, [('prime', 'PreparingBoot'), ('refresh-prepare', 'PreparingBoot'),
                                  ('refresh-boot', 'AwaitingLive')])
        self.assertTrue(api.run['status']['maintenance'])

    def test_regular_reboot_uses_managed_node_playbook(self):
        value = RunAPI().view()
        with patch.object(p, 'run_stage') as stage:
            p.reboot(value, Path('/private/known_hosts'))
        stage.assert_called_once_with(value, Path('/private'), Path('/private/known_hosts'),
                                      'reboot')

    def test_successful_live_first_flow_installs_once_and_records_run(self):
        api = RunAPI('Pending')

        def observed(value, _known):
            if value['status']['provisioning'].get('phase') == 'Verifying':
                return self.installed(value)
            data = facts()
            if value['status']['provisioning'].get('phase') == 'AwaitingInstalled':
                data.update(
                    stagedBootReady=True, stagedMarker={
                        'serverUID': 'server-uid',
                        'attemptID': 'run-uid',
                        'runUID': 'run-uid',
                        'planDigest': 'plan'
                    })
            return data
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'inspect', side_effect=observed), \
                patch.object(p, 'plan', return_value=snapshot(api.server)), patch.object(p, 'verify_dependencies'), \
                patch.object(p, 'drain_commands'), patch.object(p, 'artifact_preflight'), \
                patch.object(p, 'enroll_live', side_effect=lambda _api, _value, _facts, known, _dir: known), \
                patch.object(p, 'run_stage') as stage, patch.object(p, 'reboot') as reboot, \
                patch.object(p, 'await_session', side_effect=lambda _api, value, *a, **kw: (self.installed(value), Path('known'))), \
                patch.object(p.subprocess, 'run') as services, \
                patch.dict(os.environ, {'ANSIBLE_CERTIFICATE_FILE':'cert','ANSIBLE_PRIVATE_KEY_FILE':'key'}):
            services.return_value.returncode = 0
            p.reconcile(api, api.view(), {}, Path(tmp), 'revision')
        self.assertEqual([call.args[3] for call in stage.call_args_list], ['install', 'post'])
        self.assertEqual(reboot.call_count, 1)
        self.assertEqual(api.run['status']['phase'], 'Succeeded')
        self.assertEqual(api.server['status']['provisioning']['lastSuccessfulRunRef']['uid'],
                         'run-uid')
        self.assertFalse(p.eligible(api.server))

    def test_lost_completion_release_never_reinstalls(self):
        api = RunAPI('Succeeded')
        api.run['status']['maintenance'] = False
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'run_stage') as stage:
            p.reconcile(api, api.view(), {}, Path(tmp), 'revision')
            stage.assert_not_called()
        self.assertFalse(p.eligible(api.server))


if __name__ == '__main__':
    unittest.main()
