"""Ownership collisions and failures never permit overlapping mutations."""
import unittest
from common import OperatorError
from server_operation import reservation
from test_operators import API


class ReservationTests(unittest.TestCase):

    def test_success_is_observable_and_releases_target(self):
        api = API()
        with reservation(api, api.get('servers', 'node'), 'configure',
                         'configuration-operator') as operation:
            self.assertEqual(operation['spec']['targetRef']['kind'], 'Server')
            self.assertEqual(operation['spec']['action'], 'configure')
        self.assertEqual(next(iter(api.operations.values()))['status']['phase'], 'Succeeded')
        self.assertNotIn('operationRef', api.server['status'])

    def test_unclassified_interruption_retains_observable_ownership(self):
        api = API()
        with self.assertRaisesRegex(RuntimeError, 'unclassified'):
            with reservation(api, api.get('servers', 'node')):
                raise RuntimeError('unclassified')
        self.assertTrue(api.server['status']['operationRef'])
        self.assertEqual(next(iter(api.operations.values()))['status']['phase'], 'Uncertain')

    def test_uncertain_remote_work_retains_the_claim(self):
        api = API()
        with self.assertRaises(OperatorError):
            with reservation(api, api.get('servers', 'node')):
                raise OperatorError('RemoteWorkUncertain', 'Remote task may still be running')
        self.assertTrue(api.server['status']['operationRef'])
        self.assertEqual(next(iter(api.operations.values()))['status']['phase'], 'Uncertain')

    def test_one_owner_and_release_after_failure(self):
        api = API()
        snapshot = api.get('servers', 'node')
        with self.assertRaisesRegex(OperatorError, 'work failed'):
            with reservation(api, snapshot):
                self.assertTrue(api.server['status']['operationRef'])
                with self.assertRaises(OperatorError):
                    with reservation(api, api.get('servers', 'node')):
                        self.fail('second owner entered')
                raise OperatorError('PlaybookFailed', 'work failed')
        self.assertNotIn('operationRef', api.server['status'])
        self.assertEqual(next(iter(api.operations.values()))['status']['phase'], 'Failed')

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
                api.server['status']['operationRef']['uid'] = 'other'
        self.assertEqual(api.server['status']['operationRef']['uid'], 'other')
