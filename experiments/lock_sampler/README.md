# lock_sampler (experiment)

Test-only. `homelab-lock-sampler.service` starts before containerd and, for 60
minutes after boot, records every 100 ms which process holds the legacy
`/run/xtables.lock` (attributed to kube-proxy, multus-daemon, flanneld, kubelet,
containerd or other by walking parent processes) and how many iptables processes
are queued, to `/var/log/homelab-runs/<boot_id>/lock-samples.log` (`<uptime> <owner>/<comm> <queued>`;
`/var/log/homelab-lock-samples.log` links to the current boot). Every 30 s it also copies the
Flannel, kube-proxy and Multus pod logs and `crictl ps -a` into that per-boot directory.

```sh
ansible-playbook experiments/lock_sampler/play.yml -l <server> -e mode=install
ansible-playbook experiments/lock_sampler/play.yml -l <server> -e mode=uninstall
```
