# lock_contention (experiment)

Test-only, synthetic. The lab's lock contenders are far quieter than a busy prod node's (prod: minutes to
program a few Flannel rules; lab: seconds). This starts N loops of read-only `iptables -w -t nat -C` checks from
boot for a fixed time (default 1200 s), each taking the xtables lock and reading the whole NAT table, so the lock
queue resembles a node where many portmap/kubelet commands compete. N is calibrated until the no-script boot
matches the prod profile; then startup-script versions are compared under it. The processes are plain
`iptables` children of bash, like the commands the prod startup script kills.

```sh
ansible-playbook experiments/lock_contention/play.yml -l <node> -e mode=install -e lock_contention_loops=8
ansible-playbook experiments/lock_contention/play.yml -l <node> -e mode=uninstall
```

With `-e lock_contention_decay_target=<ready pod sandboxes expected>` the number of active loops shrinks in
proportion to the pods still pending, so contention dies down as pods come up (as with prod's pod-churn contenders).
