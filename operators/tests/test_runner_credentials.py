"""Bootstrap tests use disposable OpenSSH identities, never managed nodes."""
import copy
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from common import API, fingerprint
from models import JSONValue, Resource, json_object
import runner_credentials as runner


class FixtureAPI(API):
    """Serve only the existing client resources and record every read."""

    def __init__(self, credentials: runner.RunnerCredentials):
        self.reads: list[tuple[str, str]] = []
        current_time = datetime.now(timezone.utc)
        self.key: Resource = {
            'kind': 'SSHKeyPair',
            'metadata': {
                'name': 'ansible-runner',
                'uid': 'key-uid',
                'generation': 1,
                'resourceVersion': '1'
            },
            'spec': {
                'path': 'ssh/clients/ansible-runner',
                'secretStoreRef': {
                    'name': 'openbao'
                }
            },
            'status': {
                'phase': 'Ready',
                'observedGeneration': 1,
                'publicKey': credentials.public_key,
                'fingerprint': fingerprint(credentials.public_key),
                'secretRef': {
                    'name': 'runner-secret',
                    'uid': 'secret-uid'
                }
            },
        }
        self.certificate: Resource = {
            'kind': 'SSHCertificate',
            'metadata': {
                'name': 'ansible-runner',
                'uid': 'cert-uid',
                'generation': 1,
                'resourceVersion': '1'
            },
            'spec': {
                'keyPairRef': {
                    'name': 'ansible-runner'
                },
                'principals': ['ansible']
            },
            'status': {
                'phase': 'Ready',
                'observedGeneration': 1,
                'keyPairRef': {
                    'name': 'ansible-runner',
                    'uid': 'key-uid'
                },
                'validAfter': (current_time - timedelta(minutes=1)).isoformat(),
                'validBefore': (current_time + timedelta(minutes=5)).isoformat(),
                'certificate': credentials.certificate
            },
        }
        self.secret: Resource = {
            'kind': 'Secret',
            'metadata': {
                'name': 'runner-secret',
                'uid': 'secret-uid',
                'generation': 1,
                'resourceVersion': '1',
                'annotations': {
                    'homelab.io/ssh-key-pair-uid': 'key-uid'
                }
            },
            'spec': {
                **self.key['spec'], 'data': {
                    'privateKey': credentials.private_key,
                    'publicKey': credentials.public_key
                }
            },
            'status': {
                'phase': 'Ready',
                'observedGeneration': 1
            },
        }

    def get(self, collection: str, name: str) -> Resource:
        """Failed public-state checks must not reach the private-key Secret."""
        self.reads.append((collection, name))
        values = {
            ('ssh-key-pairs', 'ansible-runner'): self.key,
            ('ssh-certificates', 'ansible-runner'): self.certificate,
            ('secrets', 'runner-secret'): self.secret
        }
        return copy.deepcopy(values[(collection, name)])


class RunnerCredentialTests(unittest.TestCase):
    """Validate existing identities and fail without publishing bad credentials."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='runner-test-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for name in ('key', 'other', 'ca'):
            subprocess.run(
                ['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f',
                 str(self.directory / name)], check=True, capture_output=True)
        subprocess.run([
            'ssh-keygen', '-q', '-s',
            str(self.directory / 'ca'), '-I', 'fixture', '-n', 'ansible', '-V', '-1m:+5m',
            str(self.directory / 'key.pub')
        ], check=True, capture_output=True)
        self.credentials = runner.RunnerCredentials((self.directory / 'key').read_text(),
                                                    (self.directory / 'key.pub').read_text(),
                                                    (self.directory / 'key-cert.pub').read_text())
        self.api = FixtureAPI(self.credentials)

    def test_existing_identity_and_private_files(self):
        destination = self.directory / 'installed'
        runner.install_credentials(self.api, destination)
        self.assertEqual(self.api.reads, [('ssh-key-pairs', 'ansible-runner'),
                                          ('ssh-certificates', 'ansible-runner'),
                                          ('secrets', 'runner-secret')])
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        for name in ('id_ansible_mgmt', 'id_ansible_mgmt.pub', 'id_ansible_mgmt-cert.pub'):
            self.assertEqual((destination / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual((destination / 'id_ansible_mgmt').read_text(),
                         self.credentials.private_key)

    def test_public_checks_fail_before_secret_read(self):
        changes: list[tuple[str, JSONValue]] = [('phase', 'Pending'), ('observedGeneration', 0),
                                                ('keyPairRef', {
                                                    'name': 'ansible-runner',
                                                    'uid': 'replacement'
                                                }), ('validBefore', '2000-01-01T00:00:00Z')]
        for field, invalid in changes:
            with self.subTest(field=field):
                api = FixtureAPI(self.credentials)
                api.certificate['status'][field] = invalid
                with self.assertRaises(ValueError):
                    runner.fetch_credentials(api)
                self.assertFalse(any(collection == 'secrets' for collection, _ in api.reads))

    def test_secret_replacement_and_wrong_owner(self):
        for field in ('uid', 'annotations'):
            with self.subTest(field=field):
                api = FixtureAPI(self.credentials)
                if field == 'uid':
                    api.secret['metadata']['uid'] = 'replacement'
                else:
                    api.secret['metadata']['annotations'] = {'homelab.io/ssh-key-pair-uid': 'other'}
                with self.assertRaises(ValueError):
                    runner.fetch_credentials(api)

    def test_deletion_storage_and_readiness(self):
        for fault in ('deleted', 'path', 'store', 'stale'):
            with self.subTest(fault=fault):
                api = FixtureAPI(self.credentials)
                if fault == 'deleted':
                    api.key['metadata']['deletionTimestamp'] = 'now'
                elif fault == 'stale':
                    api.secret['status']['observedGeneration'] = 0
                else:
                    api.secret['spec']['path' if fault == 'path' else 'secretStoreRef'] = 'wrong'
                with self.assertRaises(ValueError):
                    runner.fetch_credentials(api)

    def test_private_key_mismatch_never_publishes(self):
        data = json_object(self.api.secret['spec']['data'], 'fixture')
        data['privateKey'] = (self.directory / 'other').read_text()
        destination = self.directory / 'installed'
        with self.assertRaises(ValueError):
            runner.install_credentials(self.api, destination)
        self.assertFalse((destination / 'id_ansible_mgmt').exists())

    def test_wrong_certificate_subject(self):
        subprocess.run([
            'ssh-keygen', '-q', '-s',
            str(self.directory / 'ca'), '-I', 'other', '-n', 'ansible', '-V', '-1m:+5m',
            str(self.directory / 'other.pub')
        ], check=True, capture_output=True)
        self.api.certificate['status']['certificate'] = (self.directory /
                                                         'other-cert.pub').read_text()
        with self.assertRaises(ValueError):
            runner.install_credentials(self.api, self.directory / 'installed')

    def test_certificate_newline_normalization(self):
        self.api.certificate['status']['certificate'] = self.credentials.certificate + '\n\n'
        runner.install_credentials(self.api, self.directory / 'installed')
        self.assertEqual(
            (self.directory / 'installed/id_ansible_mgmt-cert.pub').read_text().count('\n'), 1)

    def test_auth_failure_never_exposes_credentials(self):
        error = HTTPError('', 403, 'Forbidden', Message(), None)
        original_get = self.api.get

        def denied_secret(collection: str, name: str) -> Resource:
            if collection == 'secrets':
                raise error
            return original_get(collection, name)

        with patch.object(self.api, 'get', side_effect=denied_secret):
            with self.assertRaises(HTTPError):
                runner.install_credentials(self.api, self.directory / 'installed')
        self.assertFalse((self.directory / 'installed/id_ansible_mgmt').exists())
        with patch.object(runner, 'install_credentials', side_effect=ValueError('PRIVATE-FIXTURE')), \
                patch.object(runner, 'API'), patch('sys.stderr') as output:
            self.assertEqual(runner.main(), 1)
        self.assertNotIn('PRIVATE-FIXTURE', str(output.write.call_args_list))
