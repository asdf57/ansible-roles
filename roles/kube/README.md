# kubeadm platform reconciliation

`plays/configure_kube.yml` is the ordered platform playbook executed by the generic
Configuration operator. `kube` owns cluster planning, verified upstream artifacts,
host prerequisites, kubeadm init/join, Flannel and actual cluster verification.
`containerd` owns the runtime configuration/service and its CRI verification.
Neither role queries Stigmergy or creates a separate reconciliation controller.

## Execution

1. Resolve cluster context and captured Server labels; select one control plane
   and zero or more workers. Validate names, exact versions and IPv4 CIDRs.
2. Preflight every member: installed OS, reciprocal operator-attested identity,
   connected networks, endpoint DNS, protected cluster marker and interrupted work.
3. Resolve checksum-verified artifacts for each participating architecture on the
   unprivileged runner. Use native upstream binaries, not distro-version fallbacks.
4. Preflight all binary/configuration changes. Refuse Docker conflicts or changes
   affecting running kubelet/runtime workloads. There is no automatic upgrade.
5. Reconcile containerd, CNI binaries, kernel/sysctl prerequisites, NoSwap,
   NetworkManager CNI exclusions and the managed kubelet service.
6. Initialize only an owned, uninitialized control plane; otherwise verify its
   actual configuration, API identity and namespace UID. Apply changed pinned
   Flannel resources with the selected pod CIDR.
7. Join only new owned workers using a ten-minute token and pinned CA discovery.
   Always remove join files and revoke the token created by this pass.
   Then reconcile actual registration for every initialized member: authenticate
   its existing kubelet identity against the intended endpoint, check installed
   ownership and actual Node identity/address, and restore managed labels with a
   atomic Node-UID and label-state preconditions (not a whole-Node heartbeat RV).
   Every kubelet config is standardized on exactly
   KubeCluster.spec.controlPlaneEndpoint. A corrected endpoint or missing Node
   permits one kubelet restart to apply config/re-register itself. A healthy,
   already canonical or merely NotReady member is not restarted;
   no bootstrap replay, reset, certificate deletion or manual Node fabrication.
   Re-registration can allocate a new pod subnet. The managed Flannel bridge
   delegate uses CNI `forceAddress: true` to reconcile its gateway address.
   A ConfigMap checksum on the DaemonSet pod template rolls the init container
   whenever CNI configuration changes, installing the updated host CNI file.
8. Verify versions, managed Node identities/roles/addresses/pod CIDRs, Ready,
   Flannel and CoreDNS. Run unprivileged temporary pods on each node to check
   Kubernetes service DNS and HTTP between every pair; clean up only owned probes.
9. Persist protected per-node Ready markers and publish public cluster observations
   through the operator's result.json contract. No credential appears in status.

The operator performs installed-host guards before/after the playbook, holds
Server operation claims and reports Configuration Ready only for unchanged
resource identities and desired inputs. Timeouts retain claims for inspection.

`KubeCluster.status.observation` records current timestamped membership separately
from the last successful verification: missing/not-ready members, unexpected
Nodes and managed identity/label conflicts. The role writes public
`target-observation.json` before repair and after verification, including failed
verification. The generic executor can publish that observation after a completed
failed play while claims and inputs still match; it never publishes the success
file on failure or reports after uncertain remote work. Each domain role supplies
its own status payload and observed generation; the generic executor adds no
domain fields. The UI marks old or changed-input observations stale.

## Inputs and safety

KubeCluster selects Kubernetes/containerd/Flannel versions, API endpoint and CIDRs.
Optional runcVersion and cniPluginsVersion pins default to the reviewed role
versions (1.5.2 and 1.9.1); the site manifest pins both explicitly.
Server labels homelab.io/kube-cluster and homelab.io/kube-role select members.
An optional kube-node-name annotation overrides the Server name. A supplied
kube-cluster-uid annotation must match, but copying a UID is not required.

Configuration supplies context, captured inventory, protected Server/run identities,
the execution workspace and actual Git commit. Do not bypass the operator by
running this playbook against broad unverified inventory. `inspect_kube.yml` is
a separate read-only planning/host preflight, not platform readiness.

The endpoint must resolve directly to the selected control plane's stable IPv4
address. Kubeadm terminates API TLS using the cluster CA; no HTTP reverse proxy
or TLS bypass is involved. V1 has one control plane, IPv4, Flannel VXLAN and
Linux amd64/arm64 Debian/Arch hosts. Underlay firewalls must allow the normal
Kubernetes ports and Flannel UDP 8472 between members.

`/var/lib/homelab/kube-cluster.json` is a root-owned 0600 marker containing cluster
and Server identity, desired settings, phase, verified file hashes, applied Git
commit and installed namespace UID. Same-commit unchanged installations reuse
verified bytes rather than redownloading artifacts every five minutes. File drift
or new code resolves releases again and still passes the workload-change guard.

Existing conflicting state, interrupted init/join without completed config, lost
control-plane identity or desired version/role changes fail visibly. No kubeadm
reset, etcd deletion, reinitialization of a lost cluster, disk wipe, reboot or
automatic erasure/upgrade retry exists. Inspect/restore or use a reviewed explicit
maintenance workflow. Removing labels/resources does not uninstall Kubernetes.

## Verification

`make check` covers pure planning, ownership/version/network checks and localhost
Ansible grouping/template/guard fixtures. Syntax-check configure_kube.yml with
the runner roles/filter paths. These tests never mutate managed nodes and are not
live cluster acceptance; initial rollout must verify actual Nodes and workload
networking on the approved cluster.
