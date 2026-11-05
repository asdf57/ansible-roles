# lock_sampler (experiment)

Test-only. `homelab-lock-sampler.service` starts before containerd and, for 10
minutes after boot, records every 100 ms which process holds the legacy
`/run/xtables.lock` (attributed to kube-proxy, multus-daemon, flanneld, kubelet,
containerd or other by walking parent processes) and how many iptables processes
are queued, to `/var/log/homelab-lock-samples.log` (`<uptime> <owner>/<comm> <queued>`).

```sh
ansible-playbook experiments/lock_sampler/play.yml -l <server> -e mode=install
ansible-playbook experiments/lock_sampler/play.yml -l <server> -e mode=uninstall
```
