# node_drain (experiment)

Test-only. Matches a prod node that drains itself on shutdown and uncordons on boot:

- `homelab-node-drain.service` is active while the node runs; at shutdown its
  `ExecStop` runs `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
  --grace-period=0` before kubelet stops (it is ordered after kubelet).
- `homelab-node-uncordon.service` runs `kubectl uncordon <node>` after kubelet starts.

Both use a dedicated ServiceAccount (`kube-system/homelab-node-drain`) limited to
draining, with its token in a root-only kubeconfig on the node. Uninstall removes
the kubeconfig first (the drain unit then exits without draining), then the units
and the identity, so uninstalling never drains the node.

```sh
ansible-playbook experiments/node_drain/play.yml -l <server> -e mode=install
ansible-playbook experiments/node_drain/play.yml -l <server> -e mode=uninstall
```
