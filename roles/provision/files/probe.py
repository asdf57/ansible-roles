"""Read-only facts for provisioning preflight and installed verification."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def command(*args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def disk_identity(disk):
    """Match Go discovery's empty strings for nullable lsblk text columns."""
    return {
        "size": disk.get("size"),
        **{
            field: disk.get(field) or ""
            for field in ("serial", "wwn", "model", "tran")
        }
    }


def probe(target):
    expected = None
    if target.startswith('{'):
        expected = json.loads(target)
        devices = json.loads(
            command('lsblk', '--json', '--bytes', '--nodeps', '-o',
                    'PATH,TYPE,SIZE,MODEL,SERIAL,WWN,TRAN'))['blockdevices']
        matches = [
            disk for disk in devices if disk['type'] == 'disk' and disk_identity(disk) == expected
        ]
        if len(matches) != 1:
            raise RuntimeError('Selected disk identity changed or is ambiguous')
        aliases = sorted(
            str(path) for path in Path('/dev/disk/by-id').iterdir()
            if os.path.realpath(path) == matches[0]['path'])
        if not aliases:
            raise RuntimeError('Selected disk has no stable by-id alias')
        target = aliases[0]
    if not re.fullmatch(r"/dev/disk/by-id/[^/]+", target):
        raise RuntimeError("A stable physical disk identity is required")
    resolved = os.path.realpath(target)
    disks = json.loads(
        command(
            "lsblk", "--json", "--tree", "--bytes", "-o",
            "PATH,TYPE,SIZE,MODEL,SERIAL,WWN,TRAN,RM,RO,FSTYPE,UUID,MOUNTPOINTS"))["blockdevices"]
    matches = [disk for disk in disks if disk["path"] == resolved]
    if len(matches) != 1:
        raise RuntimeError("Target is not exactly one top-level physical disk")
    disk = matches[0]
    if expected is not None and disk_identity(disk) != expected:
        raise RuntimeError('Selected disk changed during inspection')
    if disk["type"] != "disk" or disk.get("rm") or disk.get("ro") or disk.get("tran") not in (
            "sata", "nvme", "ata"):
        raise RuntimeError("USB, removable, read-only and nonphysical targets are forbidden")
    if not disk.get("serial") and not disk.get("wwn"):
        raise RuntimeError("Target has no verifiable serial/WWN")

    def descendants(node):
        yield node
        for child in node.get("children", []):
            yield from descendants(child)

    nodes = list(descendants(disk))
    live = Path("/var/lib/is_live_env").exists()
    mounted = any(any(node.get('mountpoints') or []) for node in nodes)
    marker_file = Path("/var/lib/homelab/provisioning.json")
    marker = json.loads(marker_file.read_text()) if marker_file.is_file() else None
    staged_file = Path('/mnt/var/lib/homelab/provisioning.json')
    staged_marker = json.loads(staged_file.read_text()) if staged_file.is_file() else None
    secure = list(Path("/sys/firmware/efi/efivars").glob("SecureBoot-*"))
    secure_data = secure[0].read_bytes() if secure else b''
    secure_boot = bool(secure_data[4]) if len(secure_data) >= 5 else None
    next_variables = list(Path('/sys/firmware/efi/efivars').glob('BootNext-*'))
    boot_next = format(int.from_bytes(next_variables[0].read_bytes()[4:6], 'little'),
                       '04X') if next_variables else ''
    priority_services = []
    for unit in ('linux-priority-boot.service', 'bootorderd.service'):
        if subprocess.run(['systemctl', 'is-enabled', unit], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=10).returncode == 0:
            priority_services.append(unit)
    return {
        "targetDisk":
        target,
        "bootID":
        Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "live":
        live,
        "liveBuildID":
        Path("/etc/homelabd/live-build-id").read_text().strip()
        if Path("/etc/homelabd/live-build-id").exists() else "",
        "uefi":
        Path("/sys/firmware/efi").is_dir(),
        "secureBoot":
        secure_boot,
        "firmwareBootNext":
        boot_next,
        "competingBootServices":
        priority_services,
        "disk":
        disk_identity(disk),
        "device":
        resolved,
        "partitioned":
        bool(disk.get("children")) or bool(disk.get("fstype")),
        "mounted":
        mounted,
        "marker":
        marker,
        "stagedMarker":
        staged_marker,
        "stagedBootReady":
        Path('/mnt/boot/grub/grub.cfg').is_file() and Path('/mnt/boot/ipxe/ipxe.efi').is_file(),
        "rootUUID":
        command("findmnt", "-n", "-o", "UUID", "--target", "/"),
        "rootType":
        command("findmnt", "-n", "-o", "FSTYPE", "--target", "/"),
        "rootOnTarget":
        any("/" in (node.get("mountpoints") or []) for node in nodes),
        "os":
        dict(
            line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
            if "=" in line),
        "grubReady":
        all(
            Path(p).is_file()
            for p in ("/boot/grub/grub.cfg", "/boot/grub/grubenv", "/boot/ipxe/ipxe.efi"))
        and "homelab-netboot" in Path("/boot/grub/grub.cfg").read_text(),
        "grubEnvironment":
        command("grub-editenv", "/boot/grub/grubenv", "list")
        if Path("/boot/grub/grubenv").exists() else "",
    }


if __name__ == "__main__":
    print(json.dumps(probe(sys.argv[1])))
