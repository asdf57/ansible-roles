# containerd

Installs distro-packaged containerd and runc on installed Debian and Arch Linux
systemd hosts. Owns `/etc/containerd/config.toml`, enables CRI, selects native
containerd 1.x/2.x configuration and uses runc with systemd cgroups. Validates
configuration with the installed containerd before replacing it, restarts only
when packages/configuration change, enables the service and verifies CRI plugins.
There are no runtime downloads or external package repositories.

This is the runtime role, not cluster installation. The separate `kube` role is
intended to own kubeadm/kubelet installation and init/join. This role does not
install Kubernetes or CNI, disable swap, change sysctls, join a cluster or reboot.
CRI plugin readiness is not Kubernetes node or pod-network readiness.

From an initialized management runner, after selecting an installed Server:

```sh
ansible-playbook "$ANSIBLE_PLAYS_PATH/containerd.yml" \
  -e containerd_target=beelink
```

The play requires exactly that inventory host. A future cluster operator must
also verify Server/Machine identity, provisioning ownership and acquire the
existing Server operation claim before calling the role. This standalone play
does not perform API locking: do not run alongside node provisioning or other
runtime mutations. Nothing installs this role automatically during provisioning.

Variables:

- `containerd_package_reference`: package-manager-specific package reference;
  defaults to `containerd`. A future cluster operator must resolve its explicit
  containerd pin to an exact distro package or reviewed pinned artifact/repository,
  not silently fall back to the current repository version.
- `containerd_expected_version`: optional exact upstream version, such as
  `2.1.5`. When supplied, a mismatch fails before configuration/service changes.
  A mismatch can occur after package installation; this is not a package rollback.
  Cluster reconciliation must supply both the package reference and expected version.
- `containerd_sandbox_image`: optional image reference, empty by default to retain
  runtime defaults. Cluster reconciliation should supply the pause image required
  by its pinned kubeadm version.
- `containerd_allow_active_workloads`: false by default; refuses active Docker
  or kubelet before changing packages/configuration. Set true only after explicitly
  approving runtime maintenance. Restarting containerd can disrupt workloads.

The role replaces configuration rather than merging arbitrary local customizations;
Ansible retains a backup. It never deletes runtime state or workloads. Package
installation uses `state: present`, not automatic runtime upgrades; refresh distro
package indexes through normal host maintenance. Kubernetes/runtime compatibility
must be checked against the chosen cluster version before bootstrap. Check mode
requires an already-installed runtime and does not verify live plugin readiness.

Sources: [containerd CRI configuration](https://github.com/containerd/containerd/blob/main/docs/cri/config.md)
and [Kubernetes runtime setup](https://kubernetes.io/docs/setup/production-environment/container-runtimes/).

## Local verification

Inside the runner, this renders four configurations, validates them with the
installed containerd binary and checks native CRI settings without node access:

```sh
test_directory=$(mktemp -d)
ansible-playbook -i localhost, \
  "$ANSIBLE_ROLES_PATH/containerd/tests/render.yml" \
  -e "containerd_test_directory=$test_directory"
```

The initial fixture passed with containerd 2.3.6, including parsing its supported
1.x configuration format. This does not prove package installation, systemd
service convergence or workload execution on either managed distro; those need
an explicitly approved installed test node.
