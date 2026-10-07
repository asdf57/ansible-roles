"""Plan/verify firmware entries; all writes remain in Ansible."""
import json
import re
import subprocess
import sys

from uuid import UUID


def boot_plan(entries, partition_uuid):
    """Select an active Homelab loader on this exact EFI partition."""
    partition_uuid = str(UUID(partition_uuid))
    order_match = re.search(r'^BootOrder:\s*([0-9a-f,]+)', entries, re.MULTILINE | re.IGNORECASE)
    if not order_match:
        raise RuntimeError('No persistent firmware boot order')
    order = order_match.group(1).upper().split(',')
    matches = []
    for match in re.finditer(r'^Boot([0-9a-f]{4})\*\s+Homelab\s+(.*)$', entries,
                             re.MULTILINE | re.IGNORECASE):
        path = match.group(2).lower()
        if ('hd(1,gpt,' + partition_uuid + ',') in path and '\\efi\\homelab\\grubx64.efi' in path:
            matches.append(match.group(1).upper())
    selected = next((entry for entry in order if entry in matches), None)
    if selected is None and matches:
        selected = sorted(matches)[0]
    desired = [selected] + [entry for entry in order if entry != selected] if selected else order
    return {
        'selectedEntry': selected,
        'bootOrder': ','.join(desired),
        'needsCreate': selected is None,
        'needsOrder': desired != order
    }


if __name__ == '__main__':
    planning = sys.argv[1] == '--plan'
    uuid = sys.argv[2] if planning else sys.argv[1]
    plan = boot_plan(subprocess.check_output(['efibootmgr', '-v'], text=True, timeout=30), uuid)
    if planning:
        print(json.dumps(plan))
    elif plan['needsCreate'] or plan['needsOrder']:
        raise RuntimeError('Normal firmware boot does not select the new Homelab GRUB')
    else:
        print('Verified installed firmware boot path')
