"""Exercise offline network setup; never change the test host's network."""
import configparser
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'roles/os/files/configure-network-manager.sh'


class NetworkManagerImageTests(unittest.TestCase):
    """The shared recipe must take ownership without runtime cleanup hacks."""

    def test_image_masks_competitors_and_installs_a_private_persistent_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wants = root / 'etc/systemd/system/multi-user.target.wants'
            wants.mkdir(parents=True)
            for unit in ('systemd-networkd.service', 'dhcpcd.service', 'dhcpcd@eth0.service',
                         'networking.service'):
                (wants / unit).symlink_to('/usr/lib/systemd/system/' + unit)
            (root / 'etc/resolv.conf').write_text('nameserver 192.0.2.1\n', encoding='utf-8')
            for _ in range(2):
                subprocess.run(['sh', str(SCRIPT), str(root)], check=True, capture_output=True)
            for unit in ('systemd-networkd.service', 'systemd-networkd.socket',
                         'systemd-networkd-wait-online.service', 'dhcpcd.service',
                         'dhcpcd@.service', 'dhclient.service', 'dhclient@.service',
                         'networking.service', 'ifup@.service'):
                self.assertEqual((wants.parent / unit).readlink(), Path('/dev/null'))
            self.assertEqual(sorted(path.name for path in wants.iterdir()),
                             ['NetworkManager.service'])
            self.assertEqual((root / 'etc/resolv.conf').readlink(),
                             Path('/run/NetworkManager/resolv.conf'))
            configuration = configparser.ConfigParser()
            configuration.read(root / 'etc/NetworkManager/conf.d/90-homelab.conf')
            self.assertEqual(configuration['main']['dhcp'], 'internal')
            self.assertEqual(configuration['main']['plugins'], 'keyfile')
            self.assertEqual(configuration['device-homelab-wired']['keep-configuration'], 'no')
            profile_path = root / 'etc/NetworkManager/system-connections/homelab-wired.nmconnection'
            profile = configparser.ConfigParser()
            profile.read(profile_path)
            self.assertEqual(profile['connection']['multi-connect'], '3')
            self.assertEqual(profile['ipv4']['method'], 'auto')
            self.assertEqual(profile['ipv4']['dhcp-client-id'], 'mac')
            self.assertEqual(stat.S_IMODE(profile_path.stat().st_mode), 0o600)

    def test_missing_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['sh', str(SCRIPT), directory], check=False,
                                    capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_builders_and_installer_use_the_same_recipe(self):
        arch = (ROOT / 'roles/os/files/arch/build.sh').read_text(encoding='utf-8')
        debian = (ROOT / 'roles/os/files/debian-trixie/build.sh').read_text(encoding='utf-8')
        installed = (ROOT /
                     'roles/provision/tasks/configure_installed.yml').read_text(encoding='utf-8')
        self.assertIn('configure-network-manager.sh', arch)
        self.assertIn('append_if_missing networkmanager', arch)
        self.assertIn('homelab-wired.nmconnection', arch)
        self.assertIn('network-manager\n', debian)
        self.assertIn('0910-network-manager.hook.chroot', debian)
        self.assertIn('name: network_manager', installed)


if __name__ == '__main__':
    unittest.main()
