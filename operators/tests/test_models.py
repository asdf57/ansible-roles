"""Typed transport checks must reject malformed responses and preserve metadata."""
import copy
import unittest

from models import JSONObject, JSONValue, json_object, json_string, resource


def response() -> JSONObject:
    """Return an API envelope including metadata used for ownership and lifecycle."""
    return {
        'apiVersion': 'homelab.io/v1alpha1',
        'kind': 'Server',
        'metadata': {
            'name': 'node',
            'uid': 'node-uid',
            'resourceVersion': '42',
            'generation': 3,
            'annotations': {
                'homelab.io/server-uid': 'node-uid'
            },
            'labels': {
                'group': 'managed'
            },
            'creationTimestamp': 'now',
            'finalizers': ['hold'],
        },
        'spec': {},
        'status': {
            'phase': 'Ready'
        },
    }


class TransportModelsTests(unittest.TestCase):
    """Exercise real narrowing rather than accepting a cast to the desired shape."""

    def test_envelope_preserves_all_defined_metadata_fields(self):
        raw = response()
        self.assertEqual(resource(raw), raw)

    def test_invalid_envelope_fields_are_rejected(self):
        changes: list[tuple[str, JSONValue]] = [('metadata', None), ('kind', 3), ('spec', []),
                                                ('status', 'Ready')]
        for field, invalid in changes:
            with self.subTest(field=field):
                raw = response()
                raw[field] = invalid
                with self.assertRaises(ValueError):
                    resource(raw)

    def test_invalid_metadata_fields_are_rejected(self):
        changes: list[tuple[str, JSONValue]] = [('uid', 3), ('generation', True),
                                                ('labels', {
                                                    'group': 3
                                                }), ('annotations', []), ('finalizers', [None]),
                                                ('deletionTimestamp', 3)]
        for field, invalid in changes:
            with self.subTest(field=field):
                raw = copy.deepcopy(response())
                json_object(raw['metadata'], 'metadata')[field] = invalid
                with self.assertRaises(ValueError):
                    resource(raw)

    def test_credential_and_mapping_narrowing_rejects_wrong_types(self):
        with self.assertRaises(ValueError):
            json_string(3, 'client_token')
        with self.assertRaises(ValueError):
            json_object(['not a mapping'], 'auth')
