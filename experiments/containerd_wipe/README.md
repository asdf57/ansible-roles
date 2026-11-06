# containerd_wipe (experiment)

Test-only. Reproduces a prod boot sequence on one node:

1. `homelab-containerd-wipe.service` removes `/var/lib/containerd` strictly before
   `containerd.service` starts (all images and sandbox records are gone).
2. containerd starts.
3. `homelab-flannel-prefetch.service` pulls the Flannel images
   (`containerd_wipe_prefetch_images`) and is ordered before `kubelet.service`.
4. kubelet starts.

Nothing in `plays/`, `roles/`, provisioning or any Configuration references it.

```sh
ansible-playbook experiments/containerd_wipe/play.yml -l <server> -e mode=install
ansible-playbook experiments/containerd_wipe/play.yml -l <server> -e mode=uninstall
```

Install only enables the units; they act on the next boot. Every image other than
the prefetched ones is pulled again after each boot.

The wipe unit also removes `/var/lib/cni/networks` (host-local IP reservations), as the prod wipe script does:
after the containerd wipe no sandbox survives, so every reservation is stale.
