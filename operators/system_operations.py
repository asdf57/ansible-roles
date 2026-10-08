"""UID-bound single-Server operations executed by ordinary Command builds."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

from common import (API, address, capture_inventory, resolved_inventory_hosts, run_ansible,
                    ssh_args, write_private)
from runner_trust import prepare


def selected_server(api, name, uid, machine_uid):
    """Recheck current identity and lifecycle gates; discovery is not SSH trust."""
    server = api.get('servers', name)
    status = server.get('status', {})
    reservation = status.get('provisioning', {})
    if (server['metadata']['uid'] != uid or server['metadata'].get('deletionTimestamp')
            or status.get('machineRef', {}).get('uid') != machine_uid):
        raise RuntimeError('Server or Machine identity changed; create a newly reviewed Command')
    if reservation.get('maintenance') or reservation.get('activeRunRef') is not None:
        raise RuntimeError('Provisioning owns the Server; system operation refused')
    if server.get('spec', {}).get('reconciliation', {}).get('paused'):
        raise RuntimeError('Server reconciliation is paused')
    address(status['networking']['management']['address']['address'])
    return server


def prepare_target(api, group, name, uid, machine_uid, directory):
    """Resolve inherited vars, then override transport and target from fresh API state."""
    _, captured = capture_inventory(api, group)
    hosts = resolved_inventory_hosts(captured)
    if name not in hosts:
        raise RuntimeError('Selected Server is not in the current executor capture group')
    server = selected_server(api, name, uid, machine_uid)
    inventory = {'all': {'hosts': {name: dict(hosts[name])}}}
    known = directory / 'known_hosts'
    # This also independently validates the public managed key/fingerprint.
    write_private(known, prepare(api, inventory))
    latest = selected_server(api, name, uid, machine_uid)
    if (latest['status'].get('hostSSH') != server['status'].get('hostSSH')
            or latest['status'].get('machineRef') != server['status'].get('machineRef')
            or latest['status']['networking']['management']
            != server['status']['networking']['management']):
        raise RuntimeError('Target management identity changed during operation preparation')
    variables = inventory['all']['hosts'][name]
    variables.update(
        ansible_host=address(latest['status']['networking']['management']['address']['address']),
        ansible_user='ansible', ansible_connection='ssh', ansible_become=True,
        ansible_ssh_args=shlex.join(ssh_args(latest, known)), ansible_ssh_common_args='',
        ansible_ssh_extra_args='', system_operation_server_name=name,
        system_operation_server_uid=uid, system_operation_machine_uid=machine_uid)
    return inventory


def main():
    """Execute a reviewed operation with container-supplied credentials and inventory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['reboot'])
    parser.add_argument('--server', required=True)
    parser.add_argument('--uid', required=True)
    parser.add_argument('--machine-uid', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='system-operation-') as temporary:
        directory = Path(temporary)
        inventory = prepare_target(API(), os.environ['INVENTORY_CAPTURE_GROUP'], args.server,
                                   args.uid, args.machine_uid, directory)
        write_private(directory / 'inventory.json', json.dumps(inventory))
        play = Path(__file__).resolve().parent.parent / 'plays' / 'reboot.yml'
        result = run_ansible(
            ['ansible-playbook', '-i',
             str(directory / 'inventory.json'),
             str(play)], directory / 'reboot.log', 660)
        if result.returncode:
            raise RuntimeError(
                'Reboot or changed-boot SSH verification failed; inspect Command logs')
    print('Reboot complete: changed boot identity and fresh strict SSH verified', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, KeyError) as error:
        print('System operation refused/failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
