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
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakePath(PurePosixPath):
    files = {
        '/var/lib/is_live_env': '',
        '/etc/homelabd/live-build-id': 'build',
        '/proc/sys/kernel/random/boot_id': 'boot',
        '/proc/swaps': 'Filename\tType\tSize\tUsed\tPriority\n',
        '/etc/os-release': 'ID=arch\n',
        '/sys/class/block/sda1/partition': '1',
        '/sys/class/block/sda1/start': '2048',
        '/sys/class/block/sda/queue/logical_block_size': '512',
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

    def iterdir(self):
        return iter([FakePath('/dev/disk/by-id/disk')])


class ProbeTests(unittest.TestCase):

    def inspect(self, disk, selector=None):

        def command(*args):
            if args[0] == 'lsblk':
                if '--nodeps' not in args:
                    self.assertIn('--tree', args, 'PATH-only JSON must explicitly request children')
                return json.dumps({'blockdevices': [disk]})
            if args[0] == 'swapon':
                return json.dumps({'swapdevices': []})
            return 'overlay' if 'FSTYPE' in args else ''
        with patch.object(probe, 'Path', FakePath), patch.object(probe, 'command', side_effect=command), \
                patch.object(probe.os.path, 'realpath', side_effect=lambda path: disk['path']
                             if str(path).startswith('/dev/disk/by-id/') else str(path)), \
                patch.object(probe.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            return probe.probe(selector or '/dev/disk/by-id/disk')

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
            'size': 536870912,
            'mountpoints': [None]
        }]
        result = self.inspect(disk)
        self.assertTrue(result['partitioned'])
        self.assertFalse(result['mounted'])

    def test_mounted_or_swap_child_blocks_erasure(self):
        for mount in ('/', '/run/archiso/bootmnt', '[SWAP]'):
            disk = self.disk()
            disk['children'] = [{
                'path': '/dev/sda1',
                'type': 'part',
                'size': 536870912,
                'mountpoints': [mount]
            }]
            self.assertTrue(self.inspect(disk)['mounted'])

    def test_kernel_swap_state_blocks_erasure_without_a_mountpoint(self):
        disk = self.disk()
        disk['children'] = [{
            'path': '/dev/sda1',
            'type': 'part',
            'size': 1024**3,
            'fstype': 'swap',
            'mountpoints': [None]
        }]
        files = {
            **FakePath.files, '/proc/swaps':
            'Filename\tType\tSize\tUsed\tPriority\n/dev/sda1 partition 1024 0 -2\n'
        }
        with patch.object(FakePath, 'files', files):
            result = self.inspect(disk)
        self.assertTrue(result['mounted'])
        self.assertTrue(result['partitions'][0]['activeSwap'])

    def test_kernel_swap_paths_decode_escaped_whitespace(self):
        files = {
            **FakePath.files, '/proc/swaps':
            'Filename\tType\tSize\tUsed\tPriority\n/tmp/swap\\040file file 1024 0 -2\n'
        }
        with patch.object(probe, 'Path', FakePath), patch.object(FakePath, 'files', files):
            self.assertEqual(probe.active_swap_paths(), {'/tmp/swap file'})

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

    def test_mmc_main_disk_and_existing_partitions_are_supported(self):
        disk = self.disk()
        disk.update(path='/dev/mmcblk12', tran='mmc', serial='0x7722479b')
        disk['children'] = [{
            'path': '/dev/mmcblk12p3',
            'type': 'part',
            'size': 1024**3,
            'fstype': 'ext4',
            'mountpoints': [None]
        }]
        files = {
            **FakePath.files, '/sys/class/block/mmcblk12/queue/logical_block_size': '512',
            '/sys/class/block/mmcblk12p3/partition': '3',
            '/sys/class/block/mmcblk12p3/start': '2048'
        }
        with patch.object(FakePath, 'files', files):
            result = self.inspect(disk, json.dumps(probe.disk_identity(disk)))
        self.assertEqual(result['device'], '/dev/mmcblk12')
        self.assertEqual(result['partitions'][0]['number'], 3)
        self.assertTrue(result['partitioned'])
        self.assertFalse(result['mounted'])

    def test_mmc_hardware_areas_are_rejected_even_if_writable_and_identified(self):
        for name in ('mmcblk0boot0', 'mmcblk12boot1', 'mmcblk5rpmb', 'mmcblk8gp0'):
            disk = dict(self.disk(), path='/dev/' + name, tran='mmc')
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'hardware areas'):
                self.inspect(disk)

    def test_serial_only_selection_resolves_stable_alias_and_normalizes_null_wwn(self):
        disk = self.disk()
        disk['wwn'] = None
        expected = probe.disk_identity(disk)
        result = self.inspect(disk, json.dumps(expected))
        self.assertEqual(result['targetDisk'], '/dev/disk/by-id/disk')
        self.assertEqual(result['disk'], expected)
        self.assertEqual(result['disk']['wwn'], '')

    def test_changed_or_ambiguous_identity_is_rejected_before_alias_resolution(self):
        disk = self.disk()
        expected = probe.disk_identity(disk)
        changed = dict(disk, serial='replacement')
        for devices in ([changed], [disk, disk]):
            with patch.object(probe, 'command', return_value=json.dumps({'blockdevices': devices})):
                with self.assertRaises(RuntimeError):
                    probe.probe(json.dumps(expected))


if __name__ == '__main__':
    unittest.main()
