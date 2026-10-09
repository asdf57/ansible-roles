"""Read-only mount planning tests; never unmount or contact a managed node."""
import importlib.util
from pathlib import Path
import unittest

from provisioning import validate_disk
from common import OperatorError
from test_provisioning import facts

SOURCE = Path(__file__).resolve().parents[2] / 'roles/provision/files/probe.py'
SPEC = importlib.util.spec_from_file_location('disk_probe', SOURCE)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class StagingMountTests(unittest.TestCase):
    """Only the exact staging root, EFI and pseudo-filesystem binds are allowed."""

    def fixture(self):
        """Return an approved SSD and its partial installation mounts."""
        nodes = [{
            'path': '/dev/sda'
        }, {
            'path': '/dev/sda3',
            'mountpoints': ['/mnt']
        }, {
            'path': '/dev/sda1',
            'mountpoints': ['/mnt/boot/efi']
        }]
        mounts = [{
            'target': '/mnt',
            'source': '/dev/sda3',
            'fstype': 'ext4'
        }, {
            'target': '/mnt/boot/efi',
            'source': '/dev/sda1',
            'fstype': 'vfat'
        }, {
            'target': '/mnt/dev',
            'source': 'udev',
            'fstype': 'devtmpfs'
        }, {
            'target': '/mnt/dev/pts',
            'source': 'devpts',
            'fstype': 'devpts'
        }]
        return nodes, mounts

    def test_only_deepest_first_staging_unmounts(self):
        """Bind children and EFI must be removed before their parents."""
        nodes, mounts = self.fixture()
        paths = PROBE.cleanup_mounts('/dev/sda', nodes, mounts)
        self.assertLess(paths.index('/mnt/dev/pts'), paths.index('/mnt/dev'))
        self.assertEqual(paths[-1], '/mnt')

    def test_reject_unrelated_mounts(self):
        """Never touch another disk, running root or unexpected staged mount."""
        for target, source, kind in [('/mnt/home', '/dev/sdb1', 'ext4'),
                                     ('/mnt/dev', '/dev/sdb1', 'ext4'),
                                     ('/mnt/boot/efi', '/dev/sda2', 'vfat')]:
            nodes, mounts = self.fixture()
            mounts.append({'target': target, 'source': source, 'fstype': kind})
            with self.assertRaises(RuntimeError):
                PROBE.cleanup_mounts('/dev/sda', nodes, mounts)
        nodes, mounts = self.fixture()
        nodes[1]['mountpoints'].append('/')
        with self.assertRaises(RuntimeError):
            PROBE.cleanup_mounts('/dev/sda', nodes, mounts)

    def test_preflight_only_allows_verified_staging(self):
        """Ordinary erasure checks stay strict; cleanup requires an independent root."""
        data = facts()
        data.update(mounted=True, cleanupMounts=['/mnt'])
        validate_disk(data, True, allow_staging=True)
        with self.assertRaises(OperatorError):
            validate_disk(data, True)
        data['rootOnTarget'] = True
        with self.assertRaises(OperatorError):
            validate_disk(data, True, allow_staging=True)

    def test_unmounted_and_unrelated_staging_root(self):
        """An unrelated /mnt remains forbidden even when our disk is unmounted."""
        self.assertEqual(PROBE.cleanup_mounts('/dev/sda', [{'path': '/dev/sda'}], []), [])
        with self.assertRaises(RuntimeError):
            PROBE.cleanup_mounts('/dev/sda', [{
                'path': '/dev/sda'
            }], [{
                'target': '/mnt',
                'source': '/dev/sdb3',
                'fstype': 'ext4'
            }])
