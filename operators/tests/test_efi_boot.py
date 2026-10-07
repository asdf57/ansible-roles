"""Firmware planning regression tests; no EFI writes or managed-node access."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    'verify_efi',
    Path(__file__).resolve().parents[2] / 'roles/provision/files/verify_efi.py')
efi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(efi)


class EFIBootTests(unittest.TestCase):
    uuid = 'befa5286-7266-4e06-b301-32a5ab2eb20f'

    def entry(self, number='0005', uuid=None, active='*'):
        return f'Boot{number}{active} Homelab\tHD(1,GPT,{uuid or self.uuid},0x800,0x100000)/\\EFI\\Homelab\\grubx64.efi\n'

    def test_stale_same_label_partition_does_not_count_as_registered(self):
        result = efi.boot_plan(
            'BootOrder: 0000,0002\n' + self.entry('0000', 'a86a43d4-cb70-4668-9e0f-a90b369e0ea0'),
            self.uuid)
        self.assertTrue(result['needsCreate'])

    def test_correct_entry_is_idempotent(self):
        result = efi.boot_plan('BootOrder: 0005,0000,0002\n' + self.entry(), self.uuid)
        self.assertFalse(result['needsCreate'])
        self.assertFalse(result['needsOrder'])

    def test_reordering_preserves_other_entries(self):
        result = efi.boot_plan('BootOrder: 0000,0002,0005\n' + self.entry(), self.uuid)
        self.assertEqual(result['bootOrder'], '0005,0000,0002')
        self.assertTrue(result['needsOrder'])

    def test_inactive_entry_is_not_selected(self):
        self.assertTrue(
            efi.boot_plan('BootOrder: 0005\n' + self.entry(active=' '), self.uuid)['needsCreate'])

    def test_invalid_uuid_or_missing_order_blocks(self):
        with self.assertRaises(ValueError):
            efi.boot_plan('BootOrder: 0000\n', 'invalid')
        with self.assertRaises(RuntimeError):
            efi.boot_plan('', self.uuid)
