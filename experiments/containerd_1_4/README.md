# containerd_1_4 (experiment)

Test-only. Replaces the managed containerd binaries with the upstream containerd
1.4.6 release (to match a prod cluster) and writes a version 2 config with systemd
cgroups. kubelet is left stopped; the cluster install (e.g. `experiments/kube_1_23`)
starts it. `/var/lib/containerd` is removed because 2.x metadata is not readable by 1.4.

- Release: containerd-1.4.6-linux-amd64.tar.gz,
  sha256 8cbd2cb9df387568207628203a7b94dee937a738e01224ef0ebb7d8c29cf6ac4
- runc is not part of that release; the node's existing runc is used.
- containerd 1.4 only serves CRI v1alpha2: use crictl v1.23.x with it.

```sh
ansible-playbook experiments/containerd_1_4/play.yml -l <nodes> -e mode=install
```

Restore: resume the node's managed containerd Configuration; the containerd role
reinstalls its pinned version and config (state under /var/lib/containerd must be
removed again, with workloads stopped).
