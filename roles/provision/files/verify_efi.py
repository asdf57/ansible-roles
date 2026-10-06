"""Require firmware's default entry to reference the newly installed GRUB."""
import re
import subprocess
import sys

uuid = sys.argv[1].lower()
if not re.fullmatch(r'[a-f0-9-]{36}', uuid):
    raise RuntimeError('Invalid EFI partition UUID')
entries = subprocess.check_output(['efibootmgr', '-v'], text=True, timeout=30)
order = re.search(r'^BootOrder:\s*([0-9a-f]{4})', entries, re.MULTILINE | re.IGNORECASE)
if not order:
    raise RuntimeError('No persistent firmware boot order')
line = re.search(r'^Boot' + order.group(1) + r'\*?\s+Homelab\s+(.*)$', entries, re.MULTILINE | re.IGNORECASE)
if not line or uuid not in line.group(1).lower() or '\\efi\\homelab\\grubx64.efi' not in line.group(1).lower():
    raise RuntimeError('Normal firmware boot does not select the new Homelab GRUB')
print('Verified installed firmware boot path')
