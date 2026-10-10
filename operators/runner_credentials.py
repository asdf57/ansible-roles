"""Fetch existing client credentials for a disposable interactive runner shell."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile

from common import API, fingerprint, public_key, write_private
from models import JSONValue, Resource, json_object, json_string


@dataclass(frozen=True, repr=False)
class RunnerCredentials:
    """One existing subject key and its currently valid user certificate."""
    private_key: str
    public_key: str
    certificate: str


def require_ready(value: Resource, kind: str) -> None:
    """Refuse deleting, stale or unready resources before reading key material."""
    if (value['kind'] != kind or value['metadata'].get('deletionTimestamp')
            or value['status'].get('phase') != 'Ready'
            or value['status'].get('observedGeneration') != value['metadata'].get('generation')
            or 'generation' not in value['metadata']):
        raise ValueError(kind + ' is not current and Ready')


def require_reference(reference: JSONValue, value: Resource) -> None:
    """References must bind both name and UID, not just a reusable resource name."""
    identity = json_object(reference, 'resource reference')
    if (identity.get('name') != value['metadata']['name']
            or identity.get('uid') != value['metadata']['uid']):
        raise ValueError('Runner credential reference changed')


def require_current_certificate(certificate: Resource, key: Resource) -> None:
    """Check subject binding and validity before requesting the private-key Secret."""
    require_ready(certificate, 'SSHCertificate')
    require_reference(certificate['status'].get('keyPairRef'), key)
    subject = json_object(certificate['spec'].get('keyPairRef'), 'certificate subject')
    if subject.get('name') != key['metadata']['name']:
        raise ValueError('Certificate requests a different runner key')
    if subject.get('uid') not in (None, key['metadata']['uid']):
        raise ValueError('Certificate requests a replaced runner key')
    principals = certificate['spec'].get('principals')
    if not isinstance(principals, list) or 'ansible' not in principals:
        raise ValueError('Runner certificate must authorize the ansible principal')
    valid_after = datetime.fromisoformat(
        json_string(certificate['status'].get('validAfter'), 'certificate validAfter'))
    valid_before = datetime.fromisoformat(
        json_string(certificate['status'].get('validBefore'), 'certificate validBefore'))
    current_time = datetime.now(timezone.utc)
    if valid_after.tzinfo is None or valid_before.tzinfo is None:
        raise ValueError('Certificate validity must include a timezone')
    if not valid_after <= current_time < valid_before - timedelta(seconds=60):
        raise ValueError('Runner certificate is not valid or is too close to expiry')


def fetch_credentials(api: API) -> RunnerCredentials:
    """GET only the existing runner resources; never generate, sign or rotate keys."""
    key = api.get('ssh-key-pairs', 'ansible-runner')
    require_ready(key, 'SSHKeyPair')
    certificate = api.get('ssh-certificates', 'ansible-runner')
    require_current_certificate(certificate, key)
    reference = json_object(key['status'].get('secretRef'), 'runner Secret reference')
    secret = api.get('secrets', json_string(reference.get('name'), 'runner Secret name'))
    require_reference(reference, secret)
    require_ready(secret, 'Secret')
    if secret['metadata'].get('annotations',
                              {}).get('homelab.io/ssh-key-pair-uid') != key['metadata']['uid']:
        raise ValueError('Runner Secret ownership does not match the key pair')
    for field in ('path', 'secretStoreRef'):
        if secret['spec'].get(field) != key['spec'].get(field):
            raise ValueError('Runner Secret storage does not match the key pair')
    data = json_object(secret['spec'].get('data'), 'runner Secret data')
    expected_public = public_key(json_string(key['status'].get('publicKey'), 'runner publicKey'))
    stored_public = public_key(json_string(data.get('publicKey'), 'Secret publicKey'))
    if stored_public != expected_public or fingerprint(expected_public) != key['status'].get(
            'fingerprint'):
        raise ValueError('Runner public identity does not match its Secret')
    return RunnerCredentials(
        json_string(data.get('privateKey'), 'Secret privateKey').strip() + '\n',
        expected_public + '\n',
        json_string(certificate['status'].get('certificate'), 'certificate').strip() + '\n')


def validate_credentials(credentials: RunnerCredentials, directory: Path) -> None:
    """Use OpenSSH to check private/public/certificate subject agreement, without logging keys."""
    key_path = directory / 'key'
    cert_path = directory / 'key-cert.pub'
    write_private(key_path, credentials.private_key)
    write_private(cert_path, credentials.certificate)
    derived = subprocess.run(['ssh-keygen', '-y', '-f', str(key_path)], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=15, check=False)
    if derived.returncode or public_key(derived.stdout) != public_key(credentials.public_key):
        raise ValueError('Runner private/public key mismatch')
    description = subprocess.run(
        ['ssh-keygen', '-L', '-f', str(cert_path)], capture_output=True, text=True, timeout=15,
        check=False)
    identity = subprocess.run(['ssh-keygen', '-l', '-E', 'sha256', '-f',
                               str(cert_path)], capture_output=True, text=True, timeout=15,
                              check=False)
    if (description.returncode or ' user certificate' not in description.stdout
            or identity.returncode
            or fingerprint(credentials.public_key) not in identity.stdout.split()):
        raise ValueError('Runner certificate does not match the subject key')


def install_credentials(api: API, directory: Path) -> None:
    """Publish verified credentials into the container's private SSH directory."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    credentials = fetch_credentials(api)
    with tempfile.TemporaryDirectory(prefix='runner-credentials-', dir=directory) as temporary:
        validate_credentials(credentials, Path(temporary))
    write_private(directory / 'id_ansible_mgmt', credentials.private_key)
    write_private(directory / 'id_ansible_mgmt.pub', credentials.public_key)
    write_private(directory / 'id_ansible_mgmt-cert.pub', credentials.certificate)


def main() -> int:
    """Fail closed without exposing API error bodies or credential material."""
    try:
        install_credentials(API(), Path('/home/keiichi/.ssh'))
    except Exception:
        # HTTP bodies and key-validation errors may carry private data.
        print(
            'Runner credential retrieval failed; '
            'verify API permissions and Ready key/certificate resources.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
