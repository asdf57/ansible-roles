"""Wire types and validated resource envelopes, without an unchecked escape type."""
from typing import NotRequired, TypeAlias, TypedDict

JSONValue: TypeAlias = bool | int | float | str | None | list['JSONValue'] | dict[str, 'JSONValue']
JSONObject: TypeAlias = dict[str, JSONValue]


class Metadata(TypedDict):
    """Identity fields returned by the resource API."""
    name: str
    uid: str
    resourceVersion: str
    generation: NotRequired[int]
    deletionTimestamp: NotRequired[str]
    annotations: NotRequired[dict[str, str]]
    labels: NotRequired[dict[str, str]]
    creationTimestamp: NotRequired[str]
    finalizers: NotRequired[list[str]]


class Resource(TypedDict):
    """Validated envelope; spec/status contents remain JSON until interpreted."""
    kind: str
    apiVersion: NotRequired[str]
    metadata: Metadata
    spec: JSONObject
    status: JSONObject


def json_object(value: JSONValue, context: str) -> JSONObject:
    """Reject arrays/scalars where an endpoint promises a JSON mapping."""
    if not isinstance(value, dict):
        raise ValueError(context + ' must be a JSON mapping')
    return value


def json_string(value: JSONValue, context: str) -> str:
    """Require a string before using response data as an identity or credential."""
    if not isinstance(value, str):
        raise ValueError(context + ' must be a string')
    return value


def resource(value: JSONObject) -> Resource:
    """Check the resource envelope before it enters the API client's typed interface."""
    raw_metadata = json_object(value.get('metadata'), 'metadata')
    metadata: Metadata = {
        'name': json_string(raw_metadata.get('name'), 'metadata.name'),
        'uid': json_string(raw_metadata.get('uid'), 'metadata.uid'),
        'resourceVersion': json_string(raw_metadata.get('resourceVersion'),
                                       'metadata.resourceVersion'),
    }
    if 'generation' in raw_metadata:
        generation = raw_metadata['generation']
        if not isinstance(generation, int) or isinstance(generation, bool):
            raise ValueError('metadata.generation must be an integer')
        metadata['generation'] = generation
    if 'deletionTimestamp' in raw_metadata:
        metadata['deletionTimestamp'] = json_string(raw_metadata['deletionTimestamp'],
                                                    'metadata.deletionTimestamp')
    for field in ('annotations', 'labels'):
        if field in raw_metadata:
            values = json_object(raw_metadata[field], 'metadata.' + field)
            metadata[field] = {
                key: json_string(item, 'metadata.' + field + '.' + key)
                for key, item in values.items()
            }
    if 'creationTimestamp' in raw_metadata:
        metadata['creationTimestamp'] = json_string(raw_metadata['creationTimestamp'],
                                                    'metadata.creationTimestamp')
    if 'finalizers' in raw_metadata:
        finalizers = raw_metadata['finalizers']
        if not isinstance(finalizers, list):
            raise ValueError('metadata.finalizers must be a list')
        metadata['finalizers'] = [json_string(item, 'finalizer') for item in finalizers]
    result: Resource = {
        'kind': json_string(value.get('kind'), 'kind'),
        'metadata': metadata,
        'spec': json_object(value.get('spec', {}), 'spec'),
        'status': json_object(value.get('status', {}), 'status'),
    }
    if 'apiVersion' in value:
        result['apiVersion'] = json_string(value['apiVersion'], 'apiVersion')
    return result
