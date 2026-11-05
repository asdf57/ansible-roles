# Multus

Reconciles release `multus` in namespace `kube-system` using a local chart built
from the upstream **Multus CNI v4.3.1** thick-plugin manifest:
https://github.com/k8snetworkplumbingwg/multus-cni/blob/v4.3.1/deployments/multus-daemonset-thick.yml

Manifest SHA-256:
`24fb29530b760d28126bba76bd91fac8fd74100b53d9fc65943c223e3a82be30`.

Upstream publishes no Helm chart. The bundled chart keeps the upstream CRD,
RBAC, daemon configuration and DaemonSet with three changes: the mutable
`snapshot-thick` image is pinned to `v4.3.1-thick` by digest, resources and
`cleanupConfigOnExit` are values, and the DaemonSet rolls when its daemon
configuration changes.

```sh
ansible-playbook -i localhost, "$ANSIBLE_PLAYS_PATH/multus.yml" -e mode=install
ansible-playbook -i localhost, "$ANSIBLE_PLAYS_PATH/multus.yml" -e mode=uninstall
```

## Node effects

Multus is a meta-plugin in front of Flannel. Its privileged DaemonSet installs
`/opt/cni/bin/multus-shim` and writes `/etc/cni/net.d/00-multus.conf`, which
sorts before `10-flannel.conflist` and delegates the default network to it.
Every pod sandbox ADD/DEL on a node then goes through the shim to the node's
`kube-multus-ds` daemon. While that daemon is not running (for example early
after a reboot) sandbox creation and teardown fail and retry. The role does not
create any `NetworkAttachmentDefinition`; pods get only the Flannel network.

Install then requires every scheduled `kube-multus-ds` daemon to be ready;
Helm's own DaemonSet wait tolerates `maxUnavailable` crash-looping nodes. The
kube role raises `fs.inotify.max_user_instances`, which the daemon's
`/etc/cni/net.d` watcher otherwise exhausts on busy nodes.

`multus_values` raises the upstream 50Mi daemon limit to 300Mi, which can OOM
under bulk pod churn.

## Uninstall

The upstream daemon leaves `00-multus.conf` behind when it stops, so removing
only the release would leave every node delegating to an absent daemon.
Uninstall first rolls the daemons with `cleanupConfigOnExit: true` and waits,
then removes the release, so each daemon deletes its generated configuration
as it exits. `multus-shim` remains in `/opt/cni/bin` but is unused. Helm never
deletes the `network-attachment-definitions` CRD in `crds/` nor its objects.

`homelab-init/Configuration/configuration-multus-lab.yaml` installs it after
`kube-setup-lab`. Change its command to `mode=uninstall` to remove it.

See [the component role contract](../../docs/kubernetes-component-roles.md).
