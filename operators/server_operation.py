"""Small CAS-backed Server mutation ownership; no unsafe automatic expiry."""
from contextlib import contextmanager
from urllib.error import HTTPError
import uuid

from common import OperatorError


@contextmanager
def reservation(api, snapshot):
    """Claim the exact snapshot before work; release only our own operation ID."""
    status = snapshot.get('status', {})
    provisioning = status.get('provisioning', {})
    if (provisioning.get('maintenance') or provisioning.get('activeRunRef')
            or status.get('operation', {}).get('phase') == 'Held'):
        raise OperatorError('OperationBusy', 'Another operation owns the Server')
    operation_id = str(uuid.uuid4())
    try:
        claimed = api.patch_status(snapshot, {'operation': {'id': operation_id, 'phase': 'Held'}})
    except HTTPError as exc:
        if exc.code == 409:
            raise OperatorError('OperationBusy',
                                'Server changed before the operation claim') from exc
        raise
    snapshot.update(claimed)
    try:
        yield snapshot
    finally:
        for _ in range(5):
            latest = api.get('servers', snapshot['metadata']['name'])
            operation = latest.get('status', {}).get('operation', {})
            if (latest['metadata']['uid'] != snapshot['metadata']['uid'] or operation != {
                    'id': operation_id,
                    'phase': 'Held'
            }):
                raise RuntimeError('Operation ownership changed; refusing release')
            try:
                released = api.patch_status(
                    latest, {'operation': {
                        'id': operation_id,
                        'phase': 'Released'
                    }})
                snapshot.update(released)
                break
            except HTTPError as exc:
                if exc.code != 409:
                    raise
        else:
            raise RuntimeError('Operation release conflicted; inspect retained ownership')
