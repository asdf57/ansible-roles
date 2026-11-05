"""Check the Multus component contract without changing cluster networking."""

from pathlib import Path
import json
import shutil
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / 'roles/multus'


def render(values):
    """Render the bundled chart with CRDs exactly as Helm would install it."""
    result = subprocess.run([
        'helm', 'template', 'multus',
        str(ROLE / 'helm'), '--namespace', 'kube-system', '--include-crds', '--values', '-'
    ], input=yaml.safe_dump(values), text=True, capture_output=True, check=True)
    return [manifest for manifest in yaml.safe_load_all(result.stdout) if manifest]


def daemon_config(manifests):
    """Return the parsed multus-daemon configuration."""
    config = next(manifest for manifest in manifests if manifest['kind'] == 'ConfigMap')
    return json.loads(config['data']['daemon-config.json'])


@unittest.skipUnless(shutil.which('helm'), 'Helm is required for chart rendering')
class MultusRoleTests(unittest.TestCase):
    """Keep the pinned thick daemon in front of Flannel and its removal clean."""

    def test_chart_pins_the_thick_daemon_without_attachments(self):
        """The pinned image runs on every node; no extra pod networks are declared."""
        defaults = yaml.safe_load((ROLE / 'defaults/main.yml').read_text(encoding='utf-8'))
        manifests = render(defaults['multus_values'])
        daemon = next(manifest for manifest in manifests if manifest['kind'] == 'DaemonSet')
        pod = daemon['spec']['template']['spec']
        for container in pod['containers'] + pod['initContainers']:
            self.assertRegex(container['image'], r':v4\.3\.1-thick@sha256:[0-9a-f]{64}$')
        self.assertEqual(pod['containers'][0]['resources']['limits']['memory'], '300Mi')
        self.assertIn('checksum/daemon-config',
                      daemon['spec']['template']['metadata']['annotations'])
        self.assertFalse(daemon_config(manifests)['cleanupConfigOnExit'])
        self.assertEqual(daemon_config(manifests)['multusConfigFile'], 'auto')
        self.assertFalse(
            any(manifest['kind'] == 'NetworkAttachmentDefinition' for manifest in manifests))

    def test_uninstall_rolls_cleanup_daemons_before_removing_the_release(self):
        """Removing the release must not leave nodes delegating to an absent daemon."""
        install = yaml.safe_load((ROLE / 'tasks/install.yml').read_text(encoding='utf-8'))
        uninstall = yaml.safe_load((ROLE / 'tasks/uninstall.yml').read_text(encoding='utf-8'))
        install_helm = install[0]['kubernetes.core.helm']
        cleanup_helm, remove_helm = (task['kubernetes.core.helm'] for task in uninstall)
        for helm in (cleanup_helm, remove_helm):
            for field in ('name', 'release_namespace', 'kubeconfig', 'wait', 'timeout'):
                self.assertEqual(install_helm[field], helm[field])
        self.assertEqual(cleanup_helm['state'], 'present')
        self.assertTrue(cleanup_helm['atomic'])
        self.assertIn("'cleanupConfigOnExit': true", cleanup_helm['values'])
        self.assertEqual(remove_helm['state'], 'absent')
        self.assertTrue(daemon_config(render({'cleanupConfigOnExit': True}))['cleanupConfigOnExit'])

    def test_play_requires_an_explicit_mode_and_local_execution(self):
        """The ordinary component play is the sole entry point for both modes."""
        play = yaml.safe_load((ROOT / 'plays/multus.yml').read_text(encoding='utf-8'))[0]
        self.assertEqual(play['hosts'], 'localhost')
        self.assertEqual(play['connection'], 'local')
        self.assertFalse(play['become'])
        self.assertFalse(play['gather_facts'])
        roles = [task['ansible.builtin.import_role']['tasks_from'] for task in play['tasks'][1:]]
        self.assertEqual(roles, ['install', 'uninstall'])
