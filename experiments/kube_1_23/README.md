# kube_1_23 (experiment)

Test-only. Replaces the lab cluster with Kubernetes 1.23.17 (zima control plane,
beelink worker) to match an older prod cluster. **Destructive**: `reset` runs
`kubeadm reset` and removes all cluster state on the selected nodes.

1. Suspend every Configuration targeting the cluster (the platform would otherwise
   refuse or fight the replaced cluster).
2. `ansible-playbook experiments/kube_1_23/play.yml -l zima,beelink -e mode=reset`
3. Reboot both nodes through the reviewed helper (clears iptables, links, containers).
4. `ansible-playbook experiments/kube_1_23/play.yml -l zima,beelink -e mode=install`
   (binaries are checked against dl.k8s.io/GitHub SHA-256; the saved Flannel 0.28.9
   manifest is applied; the admin kubeconfig goes to `~/.kube/lab-1.23.conf` on the runner).

The platform's protected marker is kept as `/var/lib/homelab/kube-cluster.json.before-1.23-*`.

## Back to the managed 1.37 cluster
Run `reset` again and reboot. The kube role then sees uninitialized nodes, but the
lab `kube-setup-lab` Configuration still records the old cluster's namespace UID and
refuses to silently create a new cluster under the same identity. That one-time
decision is the owner's: clear the recorded cluster outputs (or recreate the
KubeCluster/Configuration) before resuming the Configurations.
