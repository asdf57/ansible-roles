"""Per-request storage normalization and installed attestation, without nodes."""
import copy
import unittest

from common import OperatorError
import provisioning


def layout():
    """A deliberately non-contiguous numbering scheme with no swap."""
    return {
        'storagePlan': {
            'partitions': [
                {
                    'spec': {
                        'number': 2,
                        'role': 'efi',
                        'filesystem': 'vfat',
                        'mountPoint': '/boot/efi'
                    },
                    'startMiB': 1,
                    'endMiB': 513
                },
                {
                    'spec': {
                        'number': 7,
                        'role': 'root',
                        'filesystem': 'ext4',
                        'mountPoint': '/'
                    },
                    'startMiB': 513,
                    'endMiB': 4096
                },
            ]
        }
    }


class PartitionLayoutTests(unittest.TestCase):
    """Pin layout ownership and refuse unsafe geometry or physical drift."""

    def test_normalization_uses_pinned_numbers_not_array_indices(self):
        storage = provisioning.validate_storage(layout(), {'disk': {'size': 10 * 1024**3}})
        self.assertEqual([part['number'] for part in storage['partitions']], [2, 7])
        self.assertEqual([part['role'] for part in storage['partitions']], ['efi', 'root'])

    def test_layout_without_pin_or_with_geometry_drift_is_rejected(self):
        overlapping = layout()
        overlapping['storagePlan']['partitions'][1]['startMiB'] = 512
        for invalid in ({}, overlapping):
            with self.assertRaises(OperatorError):
                provisioning.validate_storage(invalid, {'disk': {'size': 10 * 1024**3}})

    def test_swap_must_be_active_in_the_installed_system(self):
        storage = {
            'partitions': [{
                'number': 4,
                'role': 'swap',
                'filesystem': 'swap',
                'startMiB': 4096,
                'endMiB': 5120
            }]
        }
        observed = {
            'partitions': [{
                'number': 4,
                'startBytes': 4096 * 1048576,
                'size': 1024 * 1048576,
                'filesystem': 'swap',
                'uuid': 'swap-uuid',
                'partUUID': 'swap-partuuid',
                'mountPoints': [],
                'activeSwap': True
            }]
        }
        provisioning.verify_installed_storage(storage, observed)
        observed['partitions'][0]['activeSwap'] = False
        with self.assertRaises(OperatorError):
            provisioning.verify_installed_storage(storage, observed)

    def test_installed_verification_checks_all_partitions_and_mounts(self):
        storage = provisioning.validate_storage(layout(), {'disk': {'size': 10 * 1024**3}})
        observed = {
            'partitions': [{
                'number': part['number'],
                'startBytes': part['startMiB'] * 1048576,
                'size': (part['endMiB'] - part['startMiB']) * 1048576,
                'filesystem': part['filesystem'],
                'uuid': 'uuid',
                'partUUID': 'partuuid',
                'mountPoints': [part['mountPoint']]
            } for part in storage['partitions']]
        }
        provisioning.verify_installed_storage(storage, observed)
        for field, wrong in [('number', 3), ('startBytes', 0), ('filesystem', 'xfs'),
                             ('mountPoints', []), ('uuid', '')]:
            drifted = copy.deepcopy(observed)
            drifted['partitions'][1][field] = wrong
            with self.assertRaises(OperatorError):
                provisioning.verify_installed_storage(storage, drifted)


if __name__ == '__main__':
    unittest.main()
