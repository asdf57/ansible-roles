import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'resolve_boot_nic',
    Path(__file__).resolve().parents[2] / 'roles/provision/files/resolve_boot_nic.py')
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)


class BootNICTests(unittest.TestCase):
    mac = 'e8:ff:1e:d4:03:fa'

    def resolve(self, interfaces, mac=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, address in interfaces.items():
                (root / name).mkdir()
                if address is not None:
                    (root / name / 'address').write_text(address + '\n')
            return resolver.resolve_interface(mac or self.mac, root)

    def test_installed_name_replaces_live_eth0(self):
        self.assertEqual(self.resolve({
            'enp1s0': self.mac,
            'enp2s0': '00:11:22:33:44:55'
        }), 'enp1s0')

    def test_live_name_and_case_normalization(self):
        self.assertEqual(self.resolve({'eth0': self.mac.upper()}, self.mac.upper()), 'eth0')

    def test_missing_or_disappeared_interface_blocks(self):
        with self.assertRaises(RuntimeError):
            self.resolve({'eth0': None, 'enp2s0': '00:11:22:33:44:55'})

    def test_duplicate_mac_blocks(self):
        with self.assertRaises(RuntimeError):
            self.resolve({'enp1s0': self.mac, 'bridge0': self.mac})

    def test_invalid_mac_blocks(self):
        with self.assertRaises(RuntimeError):
            self.resolve({'enp1s0': self.mac}, '--all')

    def test_unsafe_interface_name_blocks(self):
        for name in ('-all', 'a' * 16, 'eth 0'):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                self.resolve({name: self.mac})
