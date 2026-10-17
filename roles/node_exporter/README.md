# Node Exporter

Installs the bundled Prometheus Node Exporter Helm chart as a Kubernetes
DaemonSet. This is a cluster-level Helm release, not a host systemd installation.
Run this role once on the runner, not once on every inventory host.

Requires Helm, the `kubernetes.core` Ansible collection and `KUBECONFIG` pointing
to a private kubeconfig file readable by the runner. Keep kubeconfig credentials
out of Configuration values and Git; pass a runner-managed private file path.
The chart path is resolved from this role, independently of the working directory.

```yaml
- name: Reconcile cluster metrics exporters
  hosts: localhost
  connection: local
  gather_facts: false
  become: false
  vars:
    # Captured inventory may enable become/SSH for managed nodes.
    ansible_become: false
    ansible_connection: local
  tasks:
    - name: Install Node Exporter
      ansible.builtin.include_role:
        name: node_exporter
        tasks_from: install
```

The release is named `node-exporter` in the `default` namespace. Installation
waits up to five minutes for readiness. Repeated runs reconcile the same release.
Helm atomic mode removes a failed initial installation or rolls back a failed
upgrade to the previous release.
ServiceMonitor is not enabled by default; installing the exporter does not
install Prometheus or configure scraping.

To uninstall, call the same role with `tasks_from: uninstall`. It removes the
`node-exporter` release from `default` and waits up to five minutes for removal.
Repeated uninstall runs are safe when the release is already absent. It does not
delete the namespace or other Helm releases.

Interactive `homelabc run` can provide the inventory group's verified Kubernetes
context. Configurations opt in with `context.kubeClusterRef: {name: lab}`; the
executor prepares a private, identity-verified kubeconfig before command.argv.
Ordinary Command/operator startup does not fetch it. Merely setting targetRef
to a KubeCluster does not supply credentials.
