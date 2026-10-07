"""Exercise real installation assertions locally, without disk commands or SSH."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile

import yaml


def main():
    root = Path(__file__).resolve().parents[2]
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
        'provision_disk_identity': identity,
        'destructive_probe': {
            'stdout': json.dumps(probe)
        }
    }
    cases = [('explicit run permits selected existing disk', variables, True)]
    for field, replacement in [('provision_run_uid', ''), ('provision_attempt_id', 'other'),
                               ('provision_destructive_authorized', False),
                               ('provision_live_boot_id', 'other'),
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


if __name__ == '__main__':
    main()
