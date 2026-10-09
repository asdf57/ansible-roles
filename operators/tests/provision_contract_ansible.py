"""Exercise real installation assertions locally, without disk commands or SSH."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml


def main():
    root = Path(__file__).resolve().parents[2]
    # Exercise the real asset gate locally, including the missing-helper failure.
    with tempfile.TemporaryDirectory(prefix='provision-assets-') as temporary:
        assets = Path(temporary) / 'setup'
        names = ('management/install.sh', 'management/ensure-ansible-user',
                 'management/ansible.sudoers', 'agent/install.sh', 'systemd/homelabd.service',
                 'systemd/ansible-account.service', 'systemd/ansible-account.timer',
                 'sshd/00-ansible-management.conf', 'sshd/10-homelabd.conf',
                 'agent/configure-debian-lldp.sh')
        for name in names:
            file = assets / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('fixture\n')
        play = Path(temporary) / 'assets.yml'
        play.write_text(
            yaml.safe_dump([{
                'hosts':
                'localhost',
                'connection':
                'local',
                'gather_facts':
                False,
                'vars': {
                    'provision_operating_system': {
                        'distribution': 'debian'
                    }
                },
                'tasks': [{
                    'ansible.builtin.include_tasks':
                    str(root / 'roles/provision/tasks/validate_management_assets.yml')
                }]
            }]))
        for complete in (True, False):
            if not complete:
                (assets / 'agent/configure-debian-lldp.sh').unlink()
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,',
                                     str(play)], env={
                                         **os.environ, 'HOMELABD_SETUP_SOURCE': str(assets)
                                     }, capture_output=True, text=True, timeout=60)
            assert (result.returncode == 0) == complete, result.stdout + result.stderr
            print('Complete bundle accepted' if complete else 'Missing helper rejected')
    tasks = yaml.safe_load((root / 'roles/provision/tasks/install.yml').read_text())
    assertions = [
        tasks[0],
        next(task for task in tasks
             if task['name'] == 'Require the same unmounted physical disk and live boot session')
    ]
    identity = {
        'serial': 'test-ssd',
        'wwn': 'test-wwn',
        'size': 10000000000,
        'model': 'SSD',
        'tran': 'sata'
    }
    probe = {
        'live': True,
        'liveBuildID': 'build',
        'bootID': 'live',
        'mounted': False,
        'disk': identity,
        'partitioned': True
    }
    variables = {
        'provision_destructive_authorized': True,
        'provision_run_uid': 'run-uid',
        'provision_attempt_id': 'run-uid',
        'provision_target_disk': '/dev/disk/by-id/test',
        'provision_operating_system': {
            'distribution': 'arch',
            'architecture': 'amd64',
            'bootMode': 'uefi',
            'version': 'rolling'
        },
        'provision_plan_digest': 'sha256:test',
        'storage': {
            'partitions': [{
                'fs_type': kind
            } for kind in ('efi', 'swap', 'ext4')]
        },
        'provision_live_build_id': 'build',
        'provision_live_boot_id': 'live',
        'provision_source_boot_id': 'source',
        'provision_disk_identity': identity,
        'destructive_probe': {
            'stdout': json.dumps(probe)
        }
    }
    cases = [('explicit run permits selected existing disk', variables, True)]
    for field, replacement in [('provision_run_uid', ''), ('provision_attempt_id', 'other'),
                               ('provision_destructive_authorized', False),
                               ('provision_live_boot_id', 'other'),
                               ('provision_source_boot_id', 'live'),
                               ('provision_disk_identity', {
                                   **identity, 'serial': 'other'
                               })]:
        case = copy.deepcopy(variables)
        case[field] = replacement
        cases.append((f'reject {field}', case, False))
    for label, case, expected in cases:
        with tempfile.TemporaryDirectory(prefix='provision-contract-') as temporary:
            play = Path(temporary) / 'assertions.yml'
            play.write_text(
                yaml.safe_dump([{
                    'hosts': 'localhost',
                    'connection': 'local',
                    'gather_facts': False,
                    'vars': case,
                    'tasks': assertions
                }]))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,',
                                     str(play)], capture_output=True, text=True, timeout=60)
            assert (
                result.returncode == 0) == expected, label + '\n' + result.stdout + result.stderr
        print(label + ': passed')

    refresh = yaml.safe_load((root / 'roles/provision/tasks/refresh_live.yml').read_text())
    guard = next(task for task in refresh
                 if task['name'] == 'Require an owned independent live session before a fresh boot')
    for source in ('arch', 'debian'):
        for independent in (True, False):
            case = {
                **variables, 'provision_source_boot_id': 'source',
                'provision_live_boot_arguments': ['BOOTIF=01-00-11-22-33-44-55'],
                'bootstrap_probe': {
                    'stdout':
                    json.dumps({
                        **probe, 'bootID': 'source',
                        'rootOnTarget': not independent,
                        'mounted': True,
                        'uefi': True,
                        'secureBoot': False,
                        'os': {
                            'ID': source
                        }
                    })
                }
            }
            with tempfile.TemporaryDirectory(prefix='handoff-contract-') as temporary:
                play = Path(temporary) / 'guard.yml'
                play.write_text(
                    yaml.safe_dump([{
                        'hosts': 'localhost',
                        'connection': 'local',
                        'gather_facts': False,
                        'vars': case,
                        'tasks': [guard]
                    }]))
                result = subprocess.run(['ansible-playbook', '-i', 'localhost,',
                                         str(play)], capture_output=True, text=True, timeout=60,
                                        check=False)
                assert (result.returncode == 0) == independent, result.stdout + result.stderr
            print(f'Live handoff {source} independent={independent}: passed')


if __name__ == '__main__':
    main()
