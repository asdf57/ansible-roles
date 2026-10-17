"""Check the MetalLB component contract without changing cluster networking."""

from pathlib import Path
import shutil
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / 'roles/metallb'


@unittest.skipUnless(shutil.which('helm'), 'Helm is required for chart rendering')
class MetalLBRoleTests(unittest.TestCase):
    """Keep controller installation separate from user-authored address allocation."""

    def test_chart_installs_l2_components_without_allocating_addresses(self):
        """Install the controller/speaker and CRDs, but no FRR or network policy choices."""
        defaults = yaml.safe_load((ROLE / 'defaults/main.yml').read_text(encoding='utf-8'))
        result = subprocess.run([
            'helm', 'template', 'metallb',
            str(ROLE / 'helm'), '--namespace', 'metallb-system', '--values', '-'
        ], input=yaml.safe_dump(defaults['metallb_values']), text=True, capture_output=True,
                                check=True)
        manifests = [manifest for manifest in yaml.safe_load_all(result.stdout) if manifest]
        workloads = [
            manifest['metadata']['name'] for manifest in manifests
            if manifest['kind'] in ('Deployment', 'DaemonSet')
        ]
        self.assertCountEqual(workloads, ['metallb-controller', 'metallb-speaker'])
        self.assertTrue(
            any(manifest['kind'] == 'CustomResourceDefinition' for manifest in manifests))
        self.assertFalse(
            any(manifest['kind'] in ('IPAddressPool', 'L2Advertisement', 'BGPPeer')
                for manifest in manifests))

    def test_helm_modes_share_release_and_private_context(self):
        """Both explicit task files operate on the same release without runner escalation."""
        install = yaml.safe_load((ROLE / 'tasks/install.yml').read_text(encoding='utf-8'))
        uninstall = yaml.safe_load((ROLE / 'tasks/uninstall.yml').read_text(encoding='utf-8'))
        self.assertEqual(len(install), 1)
        self.assertEqual(len(uninstall), 1)
        install_helm = install[0]['kubernetes.core.helm']
        uninstall_helm = uninstall[0]['kubernetes.core.helm']
        for field in ('name', 'release_namespace', 'kubeconfig', 'wait', 'timeout'):
            self.assertEqual(install_helm[field], uninstall_helm[field])
        self.assertIn('KUBECONFIG', install_helm['kubeconfig'])
        self.assertEqual(install_helm['chart_ref'], '{{ role_path }}/helm')
        self.assertTrue(install_helm['atomic'])
        self.assertEqual(install_helm['state'], 'present')
        self.assertEqual(uninstall_helm['state'], 'absent')

    def test_play_requires_an_explicit_mode_and_local_execution(self):
        """The ordinary component play is the sole entry point for both modes."""
        play = yaml.safe_load((ROOT / 'plays/metallb.yml').read_text(encoding='utf-8'))[0]
        self.assertEqual(play['hosts'], 'localhost')
        self.assertEqual(play['connection'], 'local')
        self.assertFalse(play['become'])
        self.assertFalse(play['gather_facts'])
        self.assertIn('ansible.builtin.assert', play['tasks'][0])
        self.assertEqual(play['tasks'][1]['ansible.builtin.import_role']['tasks_from'], 'install')
        self.assertEqual(play['tasks'][1]['when'], 'mode == "install"')
        self.assertEqual(play['tasks'][2]['ansible.builtin.import_role']['tasks_from'], 'uninstall')
        self.assertEqual(play['tasks'][2]['when'], 'mode == "uninstall"')


if __name__ == '__main__':
    unittest.main()
