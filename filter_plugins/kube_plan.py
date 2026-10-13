"""Pure Kubernetes planning filters; never query the API or mutate a host."""
from collections.abc import Callable
from copy import deepcopy
from ipaddress import IPv4Network, ip_network
import hashlib
import json
import re
from typing import TypeAlias

JSONValue: TypeAlias = bool | int | float | str | None | list['JSONValue'] | dict[str, 'JSONValue']
JSONObject: TypeAlias = dict[str, JSONValue]


def mapping(value: JSONValue, context: str) -> JSONObject:
    """Require a mapping at the Ansible data boundary."""
    if not isinstance(value, dict):
        raise ValueError(context + ' must be a mapping')
    return value


def text(value: JSONValue, context: str) -> str:
    """Reject absent or non-string identities instead of coercing them."""
    if not isinstance(value, str) or not value:
        raise ValueError(context + ' must be a nonempty string')
    return value


def dns_name(value: str) -> bool:
    """Use Kubernetes DNS-subdomain node names, including per-label limits."""
    return len(value) <= 253 and all(
        len(label) <= 63 and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label)
        for label in value.split('.'))


def ipv4_network(value: JSONValue, context: str) -> IPv4Network:
    """V1 supports explicit IPv4 networks with canonical boundaries."""
    network = ip_network(text(value, context), strict=True)
    if not isinstance(network, IPv4Network):
        raise ValueError(context + ' must be IPv4 in v1')
    return network


def selected_members(nodes: JSONValue, cluster_name: str, cluster_uid: str) -> list[JSONObject]:
    """Select labeled members, not every host in the execution capture group."""
    if not isinstance(nodes, list):
        raise ValueError('configuration_nodes must be an array')
    members: list[JSONObject] = []
    names: set[str] = set()
    for raw_node in nodes:
        node = mapping(raw_node, 'captured Server')
        labels = mapping(node.get('labels', {}), 'Server labels')
        if labels.get('homelab.io/kube-cluster') != cluster_name:
            continue
        annotations = mapping(node.get('annotations', {}), 'Server annotations')
        # A user may opt into a stricter binding; no copied UID is required.
        binding = annotations.get('homelab.io/kube-cluster-uid')
        if binding is not None and binding != cluster_uid:
            raise ValueError('Server cluster UID annotation does not match the target')
        name = text(node.get('name'), 'Server name')
        node_name = text(annotations.get('homelab.io/kube-node-name', name), 'Kubernetes node name')
        role = text(labels.get('homelab.io/kube-role'), 'Kubernetes role')
        if role not in ('control-plane', 'worker'):
            raise ValueError('Kubernetes role must be control-plane or worker')
        if not dns_name(node_name) or node_name in names:
            raise ValueError('Kubernetes node names must be valid and unique')
        names.add(node_name)
        members.append(
            dict(name=name, uid=text(node.get('uid'), 'Server UID'), nodeName=node_name, role=role))
    control_planes = [node for node in members if node['role'] == 'control-plane']
    if len(control_planes) != 1:
        raise ValueError('V1 requires exactly one control-plane Server')
    return sorted(members, key=lambda node: text(node['name'], 'Server name'))


def validated_settings(raw_spec: JSONValue) -> JSONObject:
    """Keep software, endpoint and network validation separate from membership."""
    spec = mapping(raw_spec, 'cluster spec')
    for field in ('kubernetesVersion', 'containerdVersion'):
        if not re.fullmatch(r'\d+\.\d+\.\d+', text(spec.get(field), field)):
            raise ValueError(field + ' must be an exact release')
    if not text(spec['kubernetesVersion'], 'Kubernetes version').startswith('1.'):
        raise ValueError('Only Kubernetes 1.x is supported')
    if int(text(spec['kubernetesVersion'], 'Kubernetes version').split('.')[1]) < 31:
        raise ValueError('The kubeadm v1beta4 configuration requires Kubernetes 1.31 or newer')
    network = mapping(spec.get('network'), 'cluster network')
    if network.get('provider') != 'flannel':
        raise ValueError('The initial platform playbook supports Flannel only')
    if not re.fullmatch(r'\d+\.\d+\.\d+', text(network.get('version'), 'Flannel version')):
        raise ValueError('Flannel version must be an exact release')
    networks = [ipv4_network(spec.get(field), field) for field in ('podCIDR', 'serviceCIDR')]
    if networks[0].overlaps(networks[1]):
        raise ValueError('Pod and Service networks must not overlap')
    endpoint = text(spec.get('controlPlaneEndpoint'), 'control-plane endpoint')
    host, separator, port = endpoint.rpartition(':')
    if not separator or not dns_name(host) or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError('controlPlaneEndpoint must be an IPv4/DNS host and explicit valid port')
    settings = dict(spec)
    for field, default in (('runcVersion', '1.5.2'), ('cniPluginsVersion', '1.9.1')):
        settings[field] = settings.get(field, default)
        if not re.fullmatch(r'\d+\.\d+\.\d+', text(settings[field], field)):
            raise ValueError(field + ' must be an exact release')
    return settings


def cluster_plan(raw_target: JSONValue, nodes: JSONValue) -> JSONObject:
    """Validate cluster settings and return concrete role inputs for a playbook."""
    target = mapping(raw_target, 'configuration_target')
    if target.get('kind') != 'KubeCluster':
        raise ValueError('Kubernetes setup requires a KubeCluster target')
    metadata = mapping(target.get('metadata'), 'cluster metadata')
    name = text(metadata.get('name'), 'cluster name')
    uid = text(metadata.get('uid'), 'cluster UID')
    settings = validated_settings(target.get('spec'))
    members = selected_members(nodes, name, uid)
    network = ipv4_network(settings['podCIDR'], 'podCIDR')
    if network.prefixlen > 24 or 2**(24 - network.prefixlen) < len(members):
        raise ValueError('Pod network needs one /24 subnet per selected node')
    return dict(name=name, uid=uid, settings=settings, members=[node for node in members],
                controlPlane=next(node for node in members if node['role'] == 'control-plane'))


def validate_host_network(raw_plan: JSONValue, raw_facts: JSONValue) -> JSONObject:
    """Reject overlap with the node's connected IPv4 networks before installation."""
    plan = mapping(raw_plan, 'cluster plan')
    spec = mapping(plan.get('settings'), 'cluster settings')
    facts = mapping(raw_facts, 'Ansible host facts')
    interfaces = facts.get('interfaces')
    if not isinstance(interfaces, list) or not interfaces:
        raise ValueError('Host interface facts are required for network validation')
    desired = [ipv4_network(spec.get(field), field) for field in ('podCIDR', 'serviceCIDR')]
    for interface in interfaces:
        interface_name = text(interface, 'interface name')
        details = mapping(facts.get(interface_name, {}), 'interface facts')
        addresses = details.get('ipv4_secondaries', [])
        if not isinstance(addresses, list):
            raise ValueError('Secondary IPv4 interface facts must be an array')
        addresses = list(addresses)
        if 'ipv4' in details:
            addresses.append(details['ipv4'])
        for raw_address in addresses:
            address = mapping(raw_address, 'IPv4 interface facts')
            connected = ip_network(
                text(address.get('address'), 'interface address') + '/' +
                text(address.get('netmask'), 'interface netmask'), strict=False)
            if not isinstance(connected, IPv4Network):
                raise ValueError('IPv4 interface facts must contain IPv4 addresses')
            if (plan.get('allowManagedCNI') is True and interface_name in ('cni0', 'flannel.1')
                    and connected.subnet_of(desired[0])):
                continue
            if any(connected.overlaps(network) for network in desired):
                raise ValueError('Cluster CIDRs overlap connected network on ' + interface_name)
    return plan


def changed_binaries(raw_results: JSONValue, _context: JSONValue = None) -> JSONObject:
    """Compare remote destination hashes with verified local release artifacts."""
    if not isinstance(raw_results, list):
        raise ValueError('Binary inspection results must be an array')
    changed: list[JSONValue] = []
    for raw_result in raw_results:
        result = mapping(raw_result, 'destination inspection')
        actual = mapping(result.get('stat'), 'destination stat')
        source = mapping(result.get('item'), 'verified artifact')
        expected = mapping(source.get('stat'), 'verified artifact stat')
        if not actual.get('exists') or actual.get('checksum') != expected.get('checksum'):
            changed.append(
                text(mapping(source.get('item'), 'artifact file').get('dest'), 'destination path'))
    return {'paths': changed}


def installed_artifacts(raw_identity: JSONValue, _context: JSONValue = None) -> JSONObject:
    """Reuse only the fixed binary manifest previously verified and stored as root."""
    identity = mapping(raw_identity, 'protected installation')
    files = mapping(identity.get('files'), 'protected binary manifest')
    components = {
        '/usr/local/bin/containerd': 'runtime',
        '/usr/local/bin/ctr': 'runtime',
        '/usr/local/bin/containerd-shim-runc-v2': 'runtime',
        '/usr/local/bin/runc': 'runtime',
        '/usr/local/bin/kubeadm': 'kubernetes',
        '/usr/local/bin/kubelet': 'kubernetes',
        '/usr/local/bin/kubectl': 'kubernetes',
        '/opt/cni/bin/bridge': 'cni',
        '/opt/cni/bin/host-local': 'cni',
        '/opt/cni/bin/loopback': 'cni',
        '/opt/cni/bin/portmap': 'cni',
    }
    if files.keys() != components.keys():
        raise ValueError('Protected manifest does not contain exactly the reviewed binary paths')
    results: list[JSONValue] = []
    for path, component in components.items():
        checksum = text(files[path], 'installed checksum')
        if not re.fullmatch('[0-9a-f]{64}', checksum):
            raise ValueError('Protected binary checksum is invalid')
        results.append({
            'item': {
                'dest': path,
                'component': component
            },
            'stat': {
                'checksum': checksum
            }
        })
    return {'files': results}


def flannel_daemon_matches(document: JSONObject, version: str) -> bool:
    """Require the reviewed DaemonSet's main image to use the selected release."""
    pod = mapping(
        mapping(mapping(document.get('spec'), 'daemon spec').get('template'),
                'pod template').get('spec'), 'pod spec')
    containers = pod.get('containers')
    if not isinstance(containers, list):
        raise ValueError('Flannel containers must be an array')
    return any(
        text(mapping(container, 'Flannel container').get('image'), 'Flannel image').endswith(
            ':v' + version) for container in containers)


def flannel_objects(raw_documents: JSONValue, raw_plan: JSONValue) -> JSONObject:
    """Modify only the pod CIDR/ownership in the checksum-verified release manifest."""
    plan = mapping(raw_plan, 'cluster plan')
    settings = mapping(plan.get('settings'), 'settings')
    version = text(mapping(settings.get('network'), 'network').get('version'), 'Flannel version')
    if not isinstance(raw_documents, list):
        raise ValueError('Flannel documents must be an array')
    documents: list[JSONValue] = []
    found_config = False
    found_daemon = False
    for raw_document in raw_documents:
        document = deepcopy(mapping(raw_document, 'Flannel resource'))
        if document.get('kind') not in ('Namespace', 'ServiceAccount', 'ClusterRole',
                                        'ClusterRoleBinding', 'ConfigMap', 'DaemonSet'):
            raise ValueError('Unexpected resource in Flannel release manifest')
        metadata = mapping(document.get('metadata'), 'Flannel metadata')
        if document['kind'] == 'Namespace' and metadata.get('name') != 'kube-flannel':
            raise ValueError('Flannel must use its dedicated kube-flannel namespace')
        if document['kind'] in ('ServiceAccount', 'ConfigMap',
                                'DaemonSet') and metadata.get('namespace') != 'kube-flannel':
            raise ValueError('Unexpected Flannel namespace')
        labels = mapping(metadata.setdefault('labels', {}), 'Flannel labels')
        labels['homelab.io/kube-cluster-uid'] = plan['uid']
        if document['kind'] == 'ConfigMap' and metadata.get('name') == 'kube-flannel-cfg':
            data = mapping(document.get('data'), 'Flannel data')
            config = mapping(json.loads(text(data.get('net-conf.json'), 'Flannel network config')),
                             'Flannel network config')
            config['Network'] = settings['podCIDR']
            data['net-conf.json'] = json.dumps(config, sort_keys=True)
            found_config = True
        if document['kind'] == 'DaemonSet':
            found_daemon = flannel_daemon_matches(document, version)
        documents.append(document)
    if not found_config or not found_daemon:
        raise ValueError('Release manifest does not contain the selected Flannel version/config')
    return {'apiVersion': 'v1', 'kind': 'List', 'items': documents}


def verify_nodes(raw_plan: JSONValue, raw_response: JSONValue) -> JSONObject:
    """Verify actual managed identities, roles, exact versions, Ready and pod subnets."""
    plan = mapping(raw_plan, 'cluster plan')
    settings = mapping(plan.get('settings'), 'settings')
    expected = plan.get('members')
    nodes = mapping(raw_response, 'Kubernetes Node response').get('items')
    if not isinstance(expected, list) or not isinstance(nodes, list):
        raise ValueError('Expected members and actual Nodes must be arrays')
    indexed: dict[str, JSONObject] = {}
    for raw_member in expected:
        member = mapping(raw_member, 'expected member')
        indexed[text(member.get('nodeName'), 'node name')] = member
    if len(nodes) != len(indexed):
        raise ValueError('Actual Kubernetes Node membership differs from the selected Servers')
    subnets: list[IPv4Network] = []
    seen: set[str] = set()
    for raw_node in nodes:
        node = mapping(raw_node, 'Node')
        name = text(mapping(node.get('metadata'), 'Node metadata').get('name'), 'Node name')
        if name in seen or name not in indexed:
            raise ValueError('Unexpected or duplicate Kubernetes Node identity')
        seen.add(name)
        member = mapping(indexed.get(name), 'expected Node')
        validate_node(member, node, settings, plan)
        subnet = ipv4_network(mapping(node.get('spec'), 'Node spec').get('podCIDR'), 'Node podCIDR')
        if not subnet.subnet_of(ipv4_network(settings.get('podCIDR'), 'podCIDR')):
            raise ValueError('Node pod subnet is outside the cluster network')
        if any(subnet.overlaps(previous) for previous in subnets):
            raise ValueError('Node pod subnets overlap')
        subnets.append(subnet)
    return plan


def validate_node(member: JSONObject, node: JSONObject, settings: JSONObject,
                  plan: JSONObject) -> None:
    """Validate one Node separately so the cluster comparison stays readable."""
    metadata = mapping(node.get('metadata'), 'Node metadata')
    labels = mapping(metadata.get('labels'), 'Node labels')
    status = mapping(node.get('status'), 'Node status')
    if labels.get('homelab.io/server-uid') != member.get('uid'):
        raise ValueError('Node Server identity does not match')
    if labels.get('homelab.io/kube-cluster-uid') != plan.get('uid'):
        raise ValueError('Node cluster identity does not match')
    if (member.get('role') == 'control-plane') != ('node-role.kubernetes.io/control-plane'
                                                   in labels):
        raise ValueError('Node control-plane role does not match')
    if mapping(status.get('nodeInfo'), 'Node info').get('kubeletVersion') != 'v' + text(
            settings.get('kubernetesVersion'), 'Kubernetes version'):
        raise ValueError('Node kubelet version does not match')
    if mapping(status.get('nodeInfo'),
               'Node info').get('containerRuntimeVersion') != 'containerd://' + text(
                   settings.get('containerdVersion'), 'containerd version'):
        raise ValueError('Node running runtime version does not match')
    conditions = status.get('conditions')
    if not isinstance(conditions, list):
        raise ValueError('Node conditions must be an array')
    if not any(
            mapping(condition, 'condition').get('type') == 'Ready'
            and mapping(condition, 'condition').get('status') == 'True'
            for condition in conditions):
        raise ValueError('Node is not Ready')


def probe_objects(raw_plan: JSONValue, raw_image: JSONValue) -> JSONObject:
    """Build bounded, unprivileged, token-free pods for cross-node DNS/HTTP probes."""
    plan = mapping(raw_plan, 'cluster plan')
    image = text(raw_image, 'verification image')
    if not re.fullmatch(r'registry\.k8s\.io/e2e-test-images/busybox:[0-9][0-9a-zA-Z_.-]*', image):
        raise ValueError('Verification requires an explicitly pinned official BusyBox test image')
    members = plan.get('members')
    if not isinstance(members, list):
        raise ValueError('Members must be an array')
    items: list[JSONValue] = [{
        'apiVersion': 'v1',
        'kind': 'Namespace',
        'metadata': {
            'name': 'homelab-verify',
            'labels': {
                'homelab.io/kube-cluster-uid': plan['uid']
            }
        }
    }]
    for raw_member in members:
        member = mapping(raw_member, 'member')
        name = 'probe-' + hashlib.sha256(text(member.get('uid'),
                                              'Server UID').encode()).hexdigest()[:12]
        items.append({
            'apiVersion': 'v1',
            'kind': 'Pod',
            'metadata': {
                'name': name,
                'namespace': 'homelab-verify',
                'labels': {
                    'homelab.io/kube-cluster-uid': plan['uid'],
                    'homelab.io/server-uid': member['uid']
                }
            },
            'spec': {
                'nodeName':
                member['nodeName'],
                'automountServiceAccountToken':
                False,
                'terminationGracePeriodSeconds':
                1,
                'securityContext': {
                    'runAsUser': 1000,
                    'runAsNonRoot': True,
                    'fsGroup': 1000,
                    'seccompProfile': {
                        'type': 'RuntimeDefault'
                    }
                },
                'volumes': [{
                    'name': 'work',
                    'emptyDir': {}
                }],
                'containers': [{
                    'name':
                    'probe',
                    'image':
                    image,
                    'command': [
                        'sh', '-ec', 'printf "homelab networking ok" > /work/index.html; '
                        'exec httpd -f -p 8080 -h /work'
                    ],
                    'volumeMounts': [{
                        'name': 'work',
                        'mountPath': '/work'
                    }],
                    'securityContext': {
                        'readOnlyRootFilesystem': True,
                        'allowPrivilegeEscalation': False,
                        'capabilities': {
                            'drop': ['ALL']
                        }
                    },
                    'resources': {
                        'requests': {
                            'cpu': '10m',
                            'memory': '16Mi'
                        },
                        'limits': {
                            'cpu': '100m',
                            'memory': '64Mi'
                        }
                    },
                    'readinessProbe': {
                        'httpGet': {
                            'path': '/',
                            'port': 8080
                        },
                        'periodSeconds': 2
                    }
                }]
            }
        })
    return {'apiVersion': 'v1', 'kind': 'List', 'items': items}


class FilterModule:
    """Expose only pure, typed planning functions to Ansible."""

    def filters(self) -> dict[str, Callable[[JSONValue, JSONValue], JSONObject]]:
        """Ansible discovers these filters without API credentials or side effects."""
        return {
            'kube_cluster_plan': cluster_plan,
            'kube_validate_host_network': validate_host_network,
            'kube_changed_binaries': changed_binaries,
            'kube_installed_artifacts': installed_artifacts,
            'kube_flannel_objects': flannel_objects,
            'kube_verify_nodes': verify_nodes,
            'kube_probe_objects': probe_objects,
        }
