import fnmatch
import importlib.util
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'provision_probe',
    Path(__file__).resolve().parents[2] / 'roles/provision/files/probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakePath(PurePosixPath):
    files = {
        '/var/lib/is_live_env': '',
        '/etc/homelabd/live-build-id': 'build',
        '/proc/sys/kernel/random/boot_id': 'boot',
        '/etc/os-release': 'ID=arch\n',
        '/sys/firmware/efi/efivars/SecureBoot-fixture': b'\x00\x00\x00\x00\x00',
    }

    def exists(self):
        return str(self) in self.files

    def is_file(self):
        return self.exists()

    def is_dir(self):
        return str(self) == '/sys/firmware/efi'

    def read_text(self):
        return self.files[str(self)]

    def read_bytes(self):
        return self.files[str(self)]

    def glob(self, pattern):
        return [FakePath(name) for name in self.files if fnmatch.fnmatch(name, str(self / pattern))]


class ProbeTests(unittest.TestCase):

    def inspect(self, disk):

        def command(*args):
            if args[0] == 'lsblk':
                self.assertIn('--tree', args, 'PATH-only JSON must explicitly request children')
                return json.dumps({'blockdevices': [disk]})
            return 'overlay' if 'FSTYPE' in args else ''
        with patch.object(probe, 'Path', FakePath), patch.object(probe, 'command', side_effect=command), \
                patch.object(probe.os.path, 'realpath', return_value='/dev/sda'), \
                patch.object(probe.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            return probe.probe('/dev/disk/by-id/disk')

    def disk(self):
        return {
            'path': '/dev/sda',
            'type': 'disk',
            'serial': 'serial',
            'size': 512000000000,
            'tran': 'sata',
            'rm': False,
            'ro': False,
            'fstype': None,
            'mountpoints': [None]
        }

    def test_existing_nested_partitions_cannot_look_blank(self):
        disk = self.disk()
        disk['children'] = [{
            'path': '/dev/sda1',
            'type': 'part',
            'fstype': 'vfat',
            'mountpoints': [None]
        }]
        result = self.inspect(disk)
        self.assertTrue(result['partitioned'])
        self.assertFalse(result['mounted'])

    def test_mounted_or_swap_child_blocks_erasure(self):
        for mount in ('/', '/run/archiso/bootmnt', '[SWAP]'):
            disk = self.disk()
            disk['children'] = [{'path': '/dev/sda1', 'type': 'part', 'mountpoints': [mount]}]
            self.assertTrue(self.inspect(disk)['mounted'])

    def test_blank_disk_and_whole_disk_filesystem_are_distinct(self):
        disk = self.disk()
        self.assertFalse(self.inspect(disk)['partitioned'])
        disk['fstype'] = 'ext4'
        self.assertTrue(self.inspect(disk)['partitioned'])

    def test_missing_secure_boot_variable_is_unknown_not_disabled(self):
        files = {name: value for name, value in FakePath.files.items() if 'SecureBoot-' not in name}
        with patch.object(FakePath, 'files', files):
            self.assertIsNone(self.inspect(self.disk())['secureBoot'])

    def test_usb_removable_and_read_only_targets_rejected(self):
        for field, value in (('tran', 'usb'), ('rm', True), ('ro', True), ('type', 'loop'),
                             ('serial', '')):
            disk = self.disk()
            disk[field] = value
            with self.assertRaises(RuntimeError):
                self.inspect(disk)


if __name__ == '__main__':
    unittest.main()
