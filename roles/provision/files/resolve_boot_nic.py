"""Resolve the pinned boot MAC against the current node's interface names."""
from pathlib import Path
import re
import sys


def resolve_interface(mac, root=Path('/sys/class/net')):
    mac = mac.lower()
    if not re.fullmatch(r'[a-f0-9]{2}(:[a-f0-9]{2}){5}', mac):
        raise RuntimeError('A valid pinned boot MAC is required')
    matches = []
    for interface in root.iterdir():
        try:
            address = (interface / 'address').read_text().strip().lower()
        except OSError:
            continue
        if address == mac:
            matches.append(interface.name)
    if len(matches) != 1:
        raise RuntimeError('Pinned boot MAC must match exactly one current interface')
    name = matches[0]
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,14}', name):
        raise RuntimeError('Resolved interface name is unsafe')
    return name


if __name__ == '__main__':
    print(resolve_interface(sys.argv[1]))
