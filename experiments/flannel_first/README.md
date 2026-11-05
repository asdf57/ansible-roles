# flannel_first (experiment)

Not part of provisioning or cluster setup: no Configuration, play or role in
`plays/`/`roles/` references it. It exists to compare Flannel startup times on
a test node and is installed and removed by hand.

Boot-time experiment for nodes where kube-proxy's first full iptables restore
delays Flannel. `flannel-first.service` starts after kubelet, freezes the
kube-proxy container's cgroup (v1 freezer or v2 `cgroup.freeze`) until
`/run/flannel/subnet.env` exists, then thaws it. It never kills processes. It
backs off if kube-proxy holds `/run/xtables.lock`, releases kube-proxy after
`FLANNEL_FIRST_TIMEOUT` (180 s), and thaws from `ExecStopPost` if it is stopped
or killed. It logs `flannel ready after Ns` to the journal.

Flannel must reach the API without kube-proxy (the kube role injects the direct
endpoint); otherwise the gate only waits out its timeout.

```sh
ansible-playbook experiments/flannel_first/play.yml -l <server> -e mode=install
ansible-playbook experiments/flannel_first/play.yml -l <server> -e mode=uninstall
```

Install only enables the unit; it takes effect on the next boot. Choose the
script variant with `-e flannel_first_script=<file>` (see defaults/main.yml).
