"""Packaging gates and safe failure labels; never contact managed nodes."""
from pathlib import Path
import unittest

import yaml
from provisioning import failed_ansible_task


class ProvisioningAssetTests(unittest.TestCase):
    """Keep incomplete images from reaching destructive installation tasks."""

    def test_setup_validation_precedes_wipe(self):
        root = Path(__file__).resolve().parents[2]
        tasks = yaml.safe_load((root / 'roles/provision/tasks/install.yml').read_text())
        check = next(i for i, t in enumerate(tasks)
                     if t.get('ansible.builtin.include_tasks') == 'validate_management_assets.yml')
        wipe = next(i for i, t in enumerate(tasks)
                    if t['name'] == "Remove only the approved disk's filesystem signatures")
        self.assertLess(check, wipe)

    def test_failure_not_masked_by_cleanup(self):
        output = ('TASK [management : Install daemon] ***\n'
                  'fatal: [node]: FAILED! => {redacted}\n'
                  'TASK [management : Remove staging directory] ***\nchanged: [node]\n')
        self.assertEqual(failed_ansible_task(output), 'management : Install daemon')

    def test_unreachable_task(self):
        self.assertEqual(failed_ansible_task('TASK [Connect] ***\nfatal: [node]: UNREACHABLE!\n'),
                         'Connect')

    def test_unstructured_error_does_not_claim_last_task_failed(self):
        self.assertEqual(failed_ansible_task('TASK [cleanup] ***\nok: [node]\n'),
                         'initialization (see playbook output)')
