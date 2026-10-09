"""UID-bound single-Server operations executed by ordinary Command builds."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

from common import (API, OperatorError, address, capture_inventory, resolved_inventory_hosts,
                    run_ansible, ssh_args, write_private)
from runner_trust import prepare
from server_operation import reservation


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
    if status.get('operation', {}).get('phase') == 'Held':
        raise RuntimeError('Another operation owns the Server')
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
    parser.add_argument('operation', choices=['reboot', 'install-homelabd'])
    parser.add_argument('--server', required=True)
    parser.add_argument('--uid', required=True)
    parser.add_argument('--machine-uid', required=True)
    parser.add_argument('--binary-url')
    parser.add_argument('--sha256')
    args = parser.parse_args()
    if args.operation == 'install-homelabd':
        if not args.binary_url or not args.binary_url.startswith('https://'):
            parser.error('Agent installation requires an immutable HTTPS binary URL')
        if not args.sha256 or len(args.sha256) != 64 or any(char not in '0123456789abcdef'
                                                            for char in args.sha256):
            parser.error('Agent installation requires a SHA-256 checksum')
    with tempfile.TemporaryDirectory(prefix='system-operation-') as temporary:
        directory = Path(temporary)
        inventory = prepare_target(API(), os.environ['INVENTORY_CAPTURE_GROUP'], args.server,
                                   args.uid, args.machine_uid, directory)
        write_private(directory / 'inventory.json', json.dumps(inventory))
        play_name = 'reboot' if args.operation == 'reboot' else 'install_homelabd'
        play = Path(__file__).resolve().parent.parent / 'plays' / (play_name + '.yml')
        variables = {'homelabd_binary_url': args.binary_url, 'homelabd_binary_sha256': args.sha256}
        write_private(directory / 'variables.json', json.dumps(variables))
        snapshot = selected_server(API(), args.server, args.uid, args.machine_uid)
        if inventory['all']['hosts'][args.server]['ansible_host'] != address(
                snapshot['status']['networking']['management']['address']['address']):
            raise RuntimeError('Management address changed before the operation claim')
        with reservation(API(), snapshot):
            result = run_ansible([
                'ansible-playbook', '-i',
                str(directory / 'inventory.json'),
                str(play), '-e', '@' + str(directory / 'variables.json')
            ], directory / (play_name + '.log'), 660)
            if result.returncode:
                raise RuntimeError('System operation failed; inspect Command logs')
    print(args.operation + ' complete', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, KeyError, OperatorError) as error:
        print('System operation refused/failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
