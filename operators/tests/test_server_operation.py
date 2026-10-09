"""Ownership collisions and failures never permit overlapping mutations."""
import unittest
from common import OperatorError
from server_operation import reservation
from test_operators import API


class ReservationTests(unittest.TestCase):

    def test_one_owner_and_release_after_failure(self):
        api = API()
        snapshot = api.get('servers', 'node')
        with self.assertRaisesRegex(RuntimeError, 'work failed'):
            with reservation(api, snapshot):
                self.assertEqual(api.server['status']['operation']['phase'], 'Held')
                with self.assertRaises(OperatorError):
                    with reservation(api, api.get('servers', 'node')):
                        self.fail('second owner entered')
                raise RuntimeError('work failed')
        self.assertEqual(api.server['status']['operation']['phase'], 'Released')

    def test_provisioning_and_cas_conflicts_do_not_start_work(self):
        for status in ({'maintenance': True}, {'activeRunRef': {'uid': 'run'}}):
            api = API()
            api.server['status']['provisioning'] = status
            with self.assertRaises(OperatorError):
                with reservation(api, api.get('servers', 'node')):
                    self.fail('entered reserved Server')
            self.assertFalse(api.writes)
        api = API()
        api.conflict = True
        with self.assertRaises(OperatorError):
            with reservation(api, api.get('servers', 'node')):
                self.fail('entered after conflict')

    def test_foreign_owner_is_not_released(self):
        api = API()
        with self.assertRaisesRegex(RuntimeError, 'ownership changed'):
            with reservation(api, api.get('servers', 'node')):
                api.server['status']['operation']['id'] = 'other'
        self.assertEqual(api.server['status']['operation'], {'id': 'other', 'phase': 'Held'})
