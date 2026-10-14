"""Pure Kubernetes planning tests never install packages or contact managed nodes."""
import copy
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'filter_plugins'))
from kube_plan import JSONObject, JSONValue, cluster_plan, mapping, validate_host_network
from kube_plan import changed_binaries, flannel_objects, installed_artifacts, node_observations, probe_objects, verify_nodes
from kube_plan import registration_action, membership_observation


class KubernetesPlanTests(unittest.TestCase):
    """Cluster selection, identity and network invariants precede all mutations."""

    def setUp(self):
        self.target: JSONObject = {
            'kind': 'KubeCluster',
            'metadata': {
                'name': 'lab',
                'uid': 'lab-uid'
            },
            'spec': {
                'kubernetesVersion': '1.37.1',
                'containerdVersion': '2.4.1',
                'controlPlaneEndpoint': 'kube-api.lab.example.test:6443',
                'podCIDR': '172.28.0.0/16',
                'serviceCIDR': '172.29.0.0/16',
                'network': {
                    'provider': 'flannel',
                    'version': '0.28.10'
                },
            },
        }
        self.nodes: list[JSONObject] = [
            {
                'name': 'zima',
                'uid': 'zima-uid',
                'labels': {
                    'homelab.io/kube-cluster': 'lab',
                    'homelab.io/kube-role': 'control-plane'
                }
            },
            {
                'name': 'beelink',
                'uid': 'beelink-uid',
                'labels': {
                    'homelab.io/kube-cluster': 'lab',
                    'homelab.io/kube-role': 'worker'
                }
            },
            {
                'name': 'unrelated',
                'uid': 'other-uid',
                'labels': {}
            },
        ]

    def plan(self) -> JSONObject:
        """Adapt typed fixture members to the generic Ansible JSON boundary."""
        return cluster_plan(self.target, [node for node in self.nodes])

    def test_selects_labels_without_requiring_copied_uids(self):
        prior = copy.deepcopy(self.nodes)
        result = self.plan()
        self.assertEqual(mapping(result['controlPlane'], 'control plane')['name'], 'zima')
        members = result['members']
        if not isinstance(members, list):
            self.fail('Plan members must be an array')
        self.assertEqual(len(members), 2)
        self.assertEqual(self.nodes, prior)

    def test_requires_exactly_one_control_plane(self):
        mapping(self.nodes[0]['labels'], 'labels')['homelab.io/kube-role'] = 'worker'
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.plan()
        mapping(self.nodes[0]['labels'], 'labels')['homelab.io/kube-role'] = 'control-plane'
        mapping(self.nodes[1]['labels'], 'labels')['homelab.io/kube-role'] = 'control-plane'
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.plan()

    def test_wrong_optional_uid_binding_is_rejected(self):
        self.nodes[0]['annotations'] = {'homelab.io/kube-cluster-uid': 'old-cluster'}
        with self.assertRaisesRegex(ValueError, 'UID annotation'):
            self.plan()

    def test_node_names_are_valid_and_unique(self):
        self.nodes[1]['annotations'] = {'homelab.io/kube-node-name': 'zima'}
        with self.assertRaisesRegex(ValueError, 'valid and unique'):
            self.plan()
        self.nodes[1]['annotations'] = {'homelab.io/kube-node-name': 'INVALID'}
        with self.assertRaisesRegex(ValueError, 'valid and unique'):
            self.plan()

    def test_invalid_member_role_is_rejected(self):
        mapping(self.nodes[0]['labels'], 'labels')['homelab.io/kube-role'] = 'coordinator'
        with self.assertRaisesRegex(ValueError, 'control-plane or worker'):
            self.plan()

    def test_overlapping_or_noncanonical_cluster_ranges_are_rejected(self):
        spec = mapping(self.target['spec'], 'spec')
        spec['serviceCIDR'] = '172.28.1.0/24'
        with self.assertRaisesRegex(ValueError, 'must not overlap'):
            self.plan()
        spec['serviceCIDR'] = '172.29.0.1/16'
        with self.assertRaisesRegex(ValueError, 'host bits'):
            self.plan()

    def test_ipv6_and_unpinned_versions_are_rejected(self):
        spec = mapping(self.target['spec'], 'spec')
        spec['podCIDR'] = 'fd00::/64'
        with self.assertRaisesRegex(ValueError, 'IPv4'):
            self.plan()
        spec['podCIDR'] = '172.28.0.0/16'
        spec['kubernetesVersion'] = 'latest'
        with self.assertRaisesRegex(ValueError, 'exact release'):
            self.plan()

    def test_invalid_endpoint_is_rejected(self):
        spec = mapping(self.target['spec'], 'spec')
        for endpoint in ('https://example.test:6443', 'example.test', 'example.test:99999'):
            spec['controlPlaneEndpoint'] = endpoint
            with self.assertRaisesRegex(ValueError, 'explicit valid port'):
                self.plan()

    def test_host_network_overlap_includes_secondary_addresses(self):
        facts: JSONObject = {
            'interfaces': ['eth0'],
            'eth0': {
                'ipv4': {
                    'address': '10.1.1.28',
                    'netmask': '255.0.0.0'
                },
                'ipv4_secondaries': [{
                    'address': '172.28.1.2',
                    'netmask': '255.255.0.0'
                }],
            }
        }
        with self.assertRaisesRegex(ValueError, 'overlap connected network'):
            validate_host_network(self.plan(), facts)
        mapping(facts['eth0'], 'eth0').pop('ipv4_secondaries')
        self.assertEqual(validate_host_network(self.plan(), facts)['name'], 'lab')

    def test_missing_host_network_facts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'interface facts'):
            validate_host_network(self.plan(), {})

    def test_existing_owned_cni_does_not_block_reconciliation(self):
        facts: JSONObject = {
            'interfaces': ['cni0'],
            'cni0': {
                'ipv4': {
                    'address': '172.28.1.1',
                    'netmask': '255.255.255.0'
                }
            }
        }
        with self.assertRaisesRegex(ValueError, 'overlap'):
            validate_host_network(self.plan(), facts)
        plan = self.plan()
        plan['allowManagedCNI'] = True
        validate_host_network(plan, facts)
        mapping(mapping(facts['cni0'], 'cni0')['ipv4'], 'address')['address'] = '172.29.1.1'
        with self.assertRaisesRegex(ValueError, 'overlap'):
            validate_host_network(plan, facts)

    def test_binary_changes_compare_expected_verified_hashes(self):
        expected: JSONObject = {
            'item': {
                'dest': '/usr/local/bin/containerd'
            },
            'stat': {
                'checksum': 'a' * 64
            }
        }
        self.assertEqual(
            changed_binaries([{
                'item': expected,
                'stat': {
                    'exists': True,
                    'checksum': 'a' * 64
                }
            }])['paths'], [])
        self.assertEqual(
            changed_binaries([{
                'item': expected,
                'stat': {
                    'exists': True,
                    'checksum': 'b' * 64
                }
            }])['paths'], ['/usr/local/bin/containerd'])

    def test_installed_artifacts_reject_unreviewed_paths_and_invalid_hashes(self):
        with self.assertRaisesRegex(ValueError, 'exactly the reviewed'):
            installed_artifacts({'files': {'/etc/private': 'a' * 64}})

    def actual_nodes(self) -> JSONObject:
        """Build public API observations, not a substitute for live cluster acceptance."""
        items: list[JSONValue] = []
        for index, server in enumerate(self.nodes[:2]):
            name = server['name']
            labels: JSONObject = {
                'homelab.io/server-uid': server['uid'],
                'homelab.io/kube-cluster-uid': 'lab-uid'
            }
            if name == 'zima':
                labels['node-role.kubernetes.io/control-plane'] = ''
            items.append({
                'metadata': {
                    'name': name,
                    'uid': 'node-' + str(name),
                    'labels': labels
                },
                'spec': {
                    'podCIDR': f'172.28.{index}.0/24'
                },
                'status': {
                    'nodeInfo': {
                        'kubeletVersion': 'v1.37.1',
                        'osImage': 'Debian GNU/Linux 13',
                        'kernelVersion': '6.12',
                        'containerRuntimeVersion': 'containerd://2.4.1'
                    },
                    'addresses': [{
                        'type': 'InternalIP',
                        'address': f'192.0.2.{index + 1}'
                    }],
                    'conditions': [{
                        'type': 'Ready',
                        'status': 'True'
                    }]
                }
            })
        return {'items': items}

    def test_public_node_observations_come_from_verified_actual_nodes(self):
        observation = node_observations(self.plan(), self.actual_nodes())
        nodes = observation['nodes']
        if not isinstance(nodes, list):
            self.fail('Expected public Node array')
        first = mapping(nodes[0], 'Node observation')
        self.assertEqual(first['nodeUID'], 'node-beelink')
        self.assertEqual(first['serverRef'], {'name': 'beelink', 'uid': 'beelink-uid'})
        self.assertEqual(first['ready'], True)
        self.assertEqual(first['containerdVersion'], '2.4.1')
        self.assertEqual(first['operatingSystem'], 'Debian GNU/Linux 13')

    def test_actual_node_verification_rejects_identity_version_and_readiness_drift(self):
        verify_nodes(self.plan(), self.actual_nodes())
        for field, value in [('kubeletVersion', 'v1.36.0'),
                             ('containerRuntimeVersion', 'containerd://2.3.0')]:
            actual = self.actual_nodes()
            items = actual['items']
            if not isinstance(items, list):
                self.fail('Node observations must be an array')
            status = mapping(mapping(items[0], 'Node')['status'], 'status')
            mapping(status['nodeInfo'], 'node info')[field] = value
            with self.assertRaisesRegex(ValueError, 'version'):
                verify_nodes(self.plan(), actual)
        actual = self.actual_nodes()
        items = actual['items']
        if not isinstance(items, list):
            self.fail('Node observations must be an array')
        mapping(mapping(items[0], 'Node')['status'], 'status')['conditions'] = []
        with self.assertRaisesRegex(ValueError, 'not Ready'):
            verify_nodes(self.plan(), actual)

    def registration(self, actual: JSONObject) -> JSONObject:
        """Plan for the selected worker with synthetic Node data only."""
        return registration_action(
            self.plan(), {
                'serverUID': 'beelink-uid',
                'nodeName': 'beelink',
                'role': 'worker',
                'address': '192.0.2.2',
                'node': actual
            })

    def worker_node(self) -> JSONObject:
        """Return one current, correctly labeled synthetic worker."""
        items = self.actual_nodes()['items']
        if not isinstance(items, list):
            self.fail('Expected Node array')
        worker = mapping(items[1], 'worker')
        mapping(mapping(worker['metadata'], 'metadata')['labels'],
                'labels')['node-role.kubernetes.io/worker'] = ''
        return worker

    def test_missing_registration_is_not_confused_with_initial_join(self):
        self.assertTrue(self.registration({})['missing'])
        self.assertFalse(self.registration(self.worker_node())['missing'])
        self.assertFalse(self.registration(self.worker_node())['labelsChanged'])

    def test_real_endpoint_guard_requires_the_single_declared_cluster_endpoint(self):
        root = Path(__file__).resolve().parents[2]
        tasks = yaml.safe_load(
            (root / 'roles/kube/tasks/membership.yml').read_text(encoding='utf-8'))
        guard = next(task for task in tasks
                     if task['name'] == 'Refuse registration against another API endpoint')
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / 'endpoint.json'
            for endpoint, succeeds in (
                ('https://kube-api.lab.example.test:6443', True),
                ('https://192.0.2.1:6443', False),
                ('https://192.0.2.99:6443', False),
                ('https://192.0.2.1:443', False),
            ):
                play.write_text(
                    json.dumps([{
                        'hosts':
                        'localhost',
                        'connection':
                        'local',
                        'gather_facts':
                        False,
                        'vars': {
                            'ansible_become': False,
                            'kube_cluster_plan': self.plan(),
                            'kube_member_endpoint': {
                                'stdout': endpoint
                            }
                        },
                        'tasks': [{
                            'ansible.builtin.add_host': {
                                'name': 'zima',
                                'ansible_host': '192.0.2.1'
                            }
                        }, guard]
                    }]), encoding='utf-8')
                result = subprocess.run(['ansible-playbook', '-i', 'localhost,',
                                         str(play)], capture_output=True, text=True, timeout=30,
                                        check=False)
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(result.returncode == 0, succeeds,
                                     result.stdout + result.stderr)

    def test_owned_label_drift_is_repaired_without_restart(self):
        worker = self.worker_node()
        labels = mapping(mapping(worker['metadata'], 'metadata')['labels'], 'labels')
        del labels['homelab.io/server-uid']
        labels['node-role.kubernetes.io/control-plane'] = ''
        result = self.registration(worker)
        self.assertFalse(result['missing'])
        self.assertTrue(result['labelsChanged'])
        arguments = result['labelArguments']
        if not isinstance(arguments, list):
            self.fail('Expected managed label arguments')
        self.assertIn('node-role.kubernetes.io/control-plane-', arguments)

    def test_foreign_node_and_wrong_address_are_never_adopted(self):
        worker = self.worker_node()
        labels = mapping(mapping(worker['metadata'], 'metadata')['labels'], 'labels')
        labels['homelab.io/server-uid'] = 'another-server'
        with self.assertRaisesRegex(ValueError, 'Refusing to adopt'):
            self.registration(worker)
        worker = self.worker_node()
        mapping(worker['status'], 'status')['addresses'] = [{
            'type': 'InternalIP',
            'address': '192.0.2.99'
        }]
        with self.assertRaisesRegex(ValueError, 'address'):
            self.registration(worker)

    def test_not_ready_is_reported_but_does_not_authorize_a_restart(self):
        worker = self.worker_node()
        mapping(worker['status'], 'status')['conditions'] = [{'type': 'Ready', 'status': 'False'}]
        self.assertFalse(self.registration(worker)['missing'])
        response = self.actual_nodes()
        response['items'] = [worker]
        observed = membership_observation(self.plan(), response)
        self.assertFalse(observed['ready'])
        self.assertEqual(observed['missingMembers'], ['zima'])
        self.assertEqual(observed['notReadyMembers'], ['beelink'])

    def test_observation_distinguishes_absent_unexpected_and_conflicting_nodes(self):
        worker = self.worker_node()
        mapping(mapping(worker['metadata'], 'metadata')['labels'],
                'labels')['homelab.io/kube-cluster-uid'] = 'other-cluster'
        response: JSONObject = {'items': [worker, {'metadata': {'name': 'unexpected'}}]}
        observed = membership_observation(self.plan(), response)
        self.assertEqual(observed['missingMembers'], ['zima'])
        self.assertEqual(observed['unexpectedNodes'], ['unexpected'])
        self.assertEqual(observed['identityConflicts'], ['beelink'])

    def test_flannel_manifest_preserves_source_and_uses_selected_pod_network(self):
        documents: list[JSONObject] = [{
            'kind': 'ConfigMap',
            'metadata': {
                'name': 'kube-flannel-cfg',
                'namespace': 'kube-flannel'
            },
            'data': {
                'net-conf.json': '{"Network":"10.244.0.0/16","Backend":{"Type":"vxlan"}}'
            },
        }, {
            'kind': 'DaemonSet',
            'metadata': {
                'name': 'kube-flannel-ds',
                'namespace': 'kube-flannel'
            },
            'spec': {
                'template': {
                    'spec': {
                        'containers': [{
                            'image': 'ghcr.io/flannel-io/flannel:v0.28.10'
                        }]
                    }
                }
            }
        }]
        original = copy.deepcopy(documents)
        result = flannel_objects([document for document in documents], self.plan())
        items = result['items']
        if not isinstance(items, list):
            self.fail('Manifest items must be an array')
        config = mapping(mapping(items[0], 'ConfigMap')['data'], 'data')['net-conf.json']
        if not isinstance(config, str):
            self.fail('Network config must be JSON text')
        self.assertEqual(json.loads(config)['Network'], '172.28.0.0/16')
        self.assertEqual(documents, original)

    def test_probe_pods_are_bounded_token_free_and_nonprivileged(self):
        manifest = probe_objects(self.plan(), 'registry.k8s.io/e2e-test-images/busybox:1.36.1-1')
        items = manifest['items']
        if not isinstance(items, list):
            self.fail('Probe items must be an array')
        self.assertEqual(len(items), 3)
        for raw_pod in items[1:]:
            pod = mapping(raw_pod, 'Pod')
            spec = mapping(pod['spec'], 'spec')
            self.assertFalse(spec['automountServiceAccountToken'])
            self.assertTrue(mapping(spec['securityContext'], 'security context')['runAsNonRoot'])
        with self.assertRaisesRegex(ValueError, 'explicitly pinned'):
            probe_objects(self.plan(), 'registry.k8s.io/e2e-test-images/busybox:latest')

    def test_real_identity_assertion_rejects_unowned_and_interrupted_bootstrap(self):
        root = Path(__file__).resolve().parents[2]
        tasks = yaml.safe_load((root / 'roles/kube/tasks/identity.yml').read_text(encoding='utf-8'))
        guard = next(task for task in tasks
                     if task['name'] == 'Refuse unowned, conflicting or incomplete bootstrap state')
        cases = [
            ('fresh', False, '', False, False, True),
            ('unowned', False, '', True, True, False),
            ('prepared-with-foreign-state', True, 'Prepared', True, True, False),
            ('interrupted-init', True, 'Initialising', False, False, False),
            ('verified-existing', True, 'Ready', True, True, True),
        ]
        for label, marker_exists, phase, admin_exists, kubelet_exists, valid in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                identity: JSONObject = {'clusterUID': 'fixture', 'serverUID': 'fixture-server'}
                variables: JSONObject = {
                    'kube_identity_file': {
                        'stat': {
                            'exists': marker_exists
                        }
                    },
                    'kube_expected_identity': identity,
                    'kube_identity': {
                        'identity': identity,
                        'phase': phase
                    },
                    'kube_admin_config_exists': admin_exists,
                    'kube_kubelet_config_exists': kubelet_exists,
                    'kube_node_role': 'control-plane',
                    'kube_existing_installation': {
                        'results': [
                            {
                                'stat': {
                                    'exists': kubelet_exists
                                }
                            },
                            {
                                'stat': {
                                    'exists': admin_exists
                                }
                            },
                            {
                                'stat': {
                                    'exists': admin_exists
                                }
                            },
                            {
                                'stat': {
                                    'exists': marker_exists
                                }
                            },
                            {
                                'stat': {
                                    'exists': False
                                }
                            },
                            {
                                'stat': {
                                    'exists': False
                                }
                            },
                        ]
                    },
                }
                play = Path(directory) / 'guard.json'
                play.write_text(
                    json.dumps([{
                        'hosts': 'localhost',
                        'connection': 'local',
                        'gather_facts': False,
                        'vars': variables,
                        'tasks': [guard]
                    }]), encoding='utf-8')
                result = subprocess.run(['ansible-playbook', '-i', 'localhost,',
                                         str(play)], capture_output=True, text=True, check=False,
                                        timeout=60)
                self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)

    def test_swap_policy_manages_definitions_not_active_device_aliases(self):
        root = Path(__file__).resolve().parents[2]
        tasks = yaml.safe_load((root / 'roles/kube/tasks/swap.yml').read_text(encoding='utf-8'))
        commands = [
            task['ansible.builtin.command']['argv'] for task in tasks
            if 'ansible.builtin.command' in task
        ]
        self.assertTrue(any('list-unit-files' in command for command in commands))
        self.assertFalse(any('list-units' in command for command in commands))
        self.assertEqual(commands.count(['swapoff', '--all']), 1)
        self.assertEqual(commands[-1], ['swapon', '--show', '--noheadings'])
        for task in tasks:
            if 'ansible.builtin.systemd_service' in task:
                self.assertNotIn('state', task['ansible.builtin.systemd_service'])
        self.assertIn('ansible.builtin.assert', tasks[-1])

    def test_empty_swap_definition_list_is_success_but_errors_are_not_hidden(self):
        root = Path(__file__).resolve().parents[2]
        tasks = yaml.safe_load((root / 'roles/kube/tasks/swap.yml').read_text(encoding='utf-8'))
        predicate = next(task['failed_when'] for task in tasks
                         if task.get('register') == 'kube_persistent_swap_units')
        cases = [(0, 'example.swap disabled', '', False), (1, '', '', False),
                 (1, '', 'Failed to connect to bus', True), (2, '', '', True),
                 (1, 'unexpected result', '', True)]
        plays = [{
            'hosts':
            'localhost',
            'connection':
            'local',
            'gather_facts':
            False,
            'vars': {
                'kube_persistent_swap_units': {
                    'rc': code,
                    'stdout': output,
                    'stderr': error
                },
                'expected_failure': failed
            },
            'tasks': [{
                'ansible.builtin.assert': {
                    'quiet': True,
                    'that': [f'({predicate}) == expected_failure']
                }
            }]
        } for code, output, error, failed in cases]
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / 'swap-result.json'
            play.write_text(json.dumps(plays), encoding='utf-8')
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,',
                                     str(play)], capture_output=True, text=True, check=False,
                                    timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_runner_tasks_override_captured_node_escalation(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / 'inventory.json'
            inventory.write_text(
                json.dumps({
                    'all': {
                        'vars': {
                            'ansible_become': True
                        },
                        'hosts': {
                            'localhost': {
                                'ansible_connection': 'local'
                            }
                        }
                    }
                }), encoding='utf-8')
            local_plays = []
            for filename in ('configure_kube.yml', 'inspect_kube.yml'):
                source = yaml.safe_load((root / 'plays' / filename).read_text(encoding='utf-8'))
                for source_play in source:
                    if source_play['hosts'] != 'localhost':
                        continue
                    local_plays.append({
                        'hosts':
                        'localhost',
                        'gather_facts':
                        False,
                        'vars':
                        source_play['vars'],
                        'tasks': [{
                            'ansible.builtin.command': {
                                'argv': ['id', '-u']
                            },
                            'register': 'runner_uid',
                            'changed_when': False
                        }, {
                            'ansible.builtin.assert': {
                                'quiet': True,
                                'that': [f'runner_uid.stdout | int == {os.getuid()}']
                            }
                        }]
                    })
            verification_tasks = yaml.safe_load(
                (root / 'roles/kube/tasks/verify.yml').read_text(encoding='utf-8'))
            result_tasks = [
                task for task in verification_tasks
                if 'ansible.builtin.copy' in task and task.get('delegate_to') == 'localhost'
            ]
            local_plays.append({
                'hosts': 'localhost',
                'gather_facts': False,
                'vars': {
                    'configuration_workspace': directory,
                    'kube_namespace_uid': 'fixture',
                    'kube_cluster_observations': {
                        'clusterNamespaceUID': 'fixture'
                    },
                    'kube_cluster_plan': self.plan()
                },
                'tasks': result_tasks
            })
            play = Path(directory) / 'local.json'
            play.write_text(json.dumps(local_plays), encoding='utf-8')
            result = subprocess.run(
                ['ansible-playbook', '-i', str(inventory),
                 str(play)], capture_output=True, text=True, check=False, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((Path(directory) / 'result.json').stat().st_uid, os.getuid())
            self.assertEqual((Path(directory) / 'target-status.json').stat().st_uid, os.getuid())

    def test_all_role_stages_compile_without_executing_remote_tasks(self):
        root = Path(__file__).resolve().parents[2]
        stages = [('kube', name)
                  for name in ('plan', 'artifacts', 'artifacts_architecture', 'preflight',
                               'identity', 'prepare', 'swap', 'control_plane', 'join', 'membership',
                               'observe', 'verify')]
        stages += [('containerd', 'artifact_preflight'), ('containerd', 'artifacts')]
        tasks = [{
            'name': 'Parse ' + role + '/' + stage,
            'ansible.builtin.import_role': {
                'name': role,
                'tasks_from': stage
            }
        } for role, stage in stages]
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / 'syntax.json'
            play.write_text(
                json.dumps([{
                    'hosts': 'localhost',
                    'gather_facts': False,
                    'tasks': tasks
                }]), encoding='utf-8')
            result = subprocess.run(
                ['ansible-playbook', '-i', 'localhost,',
                 str(play), '--syntax-check'], env={
                     **os.environ, 'ANSIBLE_ROLES_PATH': str(root / 'roles'),
                     'ANSIBLE_FILTER_PLUGINS': str(root / 'filter_plugins')
                 }, capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ansible_groups_members_and_renders_real_kubeadm_template_locally(self):
        root = Path(__file__).resolve().parents[2]
        plays = [{
            'hosts':
            'localhost',
            'connection':
            'local',
            'gather_facts':
            False,
            'vars': {
                'configuration_target': self.target,
                'configuration_nodes': [node for node in self.nodes]
            },
            'tasks': [{
                'name': 'Plan through the real Kubernetes role',
                'ansible.builtin.import_role': {
                    'name': 'kube',
                    'tasks_from': 'plan'
                },
            }, {
                'name': 'Verify planning and render the template without writing node files',
                'ansible.builtin.assert': {
                    'quiet':
                    True,
                    'that': [
                        "groups['configuration_kube_control_plane'] == ['zima']",
                        "groups['configuration_kube_workers'] == ['beelink']",
                        "groups['configuration_kube_members'] | length == 2",
                        "lookup('template', template_path) is search('kubeadm.k8s.io/v1beta4')",
                        "lookup('template', template_path) is search('cgroupDriver: systemd')",
                    ],
                },
                'vars': {
                    'template_path': str(root / 'roles/kube/templates/kubeadm-init.yml.j2'),
                    'kube_node_name': 'zima',
                    'configuration_server_uid': 'zima-uid',
                    'ansible_host': '10.1.1.28',
                    'kube_cluster_plan': '{{ kube_plan }}',
                },
            }],
        }]
        # These host entries are inert inventory fixtures; the only executed play
        # is localhost, with no gather_facts, remote task, package or privileged call.
        inventory = {'all': {'hosts': {'zima': {}, 'beelink': {}, 'unrelated': {}}}}
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / 'planning.json'
            source = Path(directory) / 'inventory.json'
            play.write_text(json.dumps(plays), encoding='utf-8')
            source.write_text(json.dumps(inventory), encoding='utf-8')
            result = subprocess.run(
                ['ansible-playbook', '-i', str(source),
                 str(play)], env={
                     **os.environ, 'ANSIBLE_ROLES_PATH': str(root / 'roles'),
                     'ANSIBLE_FILTER_PLUGINS': str(root / 'filter_plugins')
                 }, check=False, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
