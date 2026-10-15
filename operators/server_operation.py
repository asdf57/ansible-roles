"""Observable, exclusive target ownership without time-based unsafe release."""
from contextlib import contextmanager
from urllib.error import HTTPError
import uuid

from common import API, OperatorError
from models import JSONObject, Resource, json_object, json_string


def operation_reference(operation: Resource) -> JSONObject:
    """Keep references bound to the exact Operation lifetime."""
    return {'name': operation['metadata']['name'], 'uid': operation['metadata']['uid']}


def complete_operation(api: API, operation: Resource, phase: str, message: str) -> None:
    """Report a known outcome; the API atomically releases only terminal work."""
    for _ in range(5):
        latest = api.get('operations', operation['metadata']['name'])
        if (latest['metadata']['uid'] != operation['metadata']['uid']
                or latest['spec'] != operation['spec']):
            raise RuntimeError('Operation ownership changed; refusing completion')
        if latest['status'].get('phase') not in ('Running', 'Uncertain'):
            raise RuntimeError('Operation already completed; refusing to overwrite its outcome')
        try:
            api.patch_status(latest, {'phase': phase, 'message': message})
            return
        except HTTPError as error:
            if error.code != 409:
                raise
    raise RuntimeError('Operation completion conflicted; inspect retained ownership')


def verify_operation(api: API, target: Resource) -> Resource:
    """Require the API-owned reference and exact reciprocal, actively running claim."""
    reference = json_object(target['status'].get('operationRef'), 'active Operation')
    operation = api.get('operations', json_string(reference.get('name'), 'Operation name'))
    if (operation['metadata']['uid'] != reference.get('uid')
            or operation['status'].get('phase') != 'Running'
            or operation['spec'].get('targetRef') != {
                'kind': target['kind'],
                'name': target['metadata']['name'],
                'uid': target['metadata']['uid']
            }):
        raise OperatorError('OwnershipChanged', 'The active Operation changed')
    return operation


def operation_progress(api: API, target: Resource, step: str) -> None:
    """Publish public stage names without copying private Ansible output."""
    operation = verify_operation(api, target)
    api.patch_status(operation, {'currentStep': step})


@contextmanager
def reservation(api: API, snapshot: Resource, action: str = 'manage', operator: str = 'homelab',
                requested_by: JSONObject | None = None, collection: str = 'servers'):
    """Acquire one generic target; unknown remote outcomes always retain ownership."""
    provisioning = json_object(snapshot['status'].get('provisioning', {}), 'provisioning')
    if (snapshot['status'].get('operationRef') or provisioning.get('maintenance')
            or provisioning.get('activeRunRef')):
        raise OperatorError('OperationBusy', 'Another operation owns the target')
    spec: JSONObject = {
        'targetRef': {
            'kind': snapshot['kind'],
            'name': snapshot['metadata']['name'],
            'uid': snapshot['metadata']['uid']
        },
        'action': action,
        'operator': operator,
    }
    if requested_by is not None:
        spec['requestedBy'] = requested_by
    try:
        operation = api.create('operations', 'Operation', 'operation-' + str(uuid.uuid4()), spec)
    except HTTPError as error:
        if error.code == 409:
            raise OperatorError('OperationBusy', 'Target changed or is already owned') from error
        raise
    try:
        claimed = api.get(collection, snapshot['metadata']['name'])
        if (claimed['metadata']['uid'] != snapshot['metadata']['uid']
                or claimed['status'].get('operationRef') != operation_reference(operation)):
            raise OperatorError('OwnershipChanged', 'Target changed before execution')
        snapshot.update(claimed)
        yield operation
    except OperatorError as error:
        uncertain = error.reason == 'RemoteWorkUncertain'
        complete_operation(api, operation, 'Uncertain' if uncertain else 'Failed',
                           'Inspect remote work before resolution' if uncertain else error.reason)
        raise
    except BaseException:
        # A killed local process or unclassified failure does not attest remote completion.
        complete_operation(api, operation, 'Uncertain',
                           'Execution interrupted; inspect remote work')
        raise
    else:
        complete_operation(api, operation, 'Succeeded', 'Operation execution completed')
