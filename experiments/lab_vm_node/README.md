# lab_vm_node (experiment)

Test-only. Prepares a throwaway lab VM (Debian 12 generic cloud image, outside the
Stigmergy Server inventory) as an extra Kubernetes 1.23 node, the way the managed roles
prepare zima and beelink: prerequisites, kernel modules, sysctls, iptables-legacy,
runc v1.5.2 and CNI plugins v1.9.1 (both matching beelink, checksum-verified), and the
managed containerd unit. Then install containerd and join:

```sh
I="-i /homelab/inventory/inventory.yaml -i <vm-inventory.yaml>"
ansible-playbook $I experiments/lab_vm_node/play.yml -l <vm> -e mode=install
ansible-playbook $I experiments/containerd_1_4/play.yml -l <vm> -e mode=install
ansible-playbook $I experiments/kube_1_23/play.yml -l <control-plane>,<vm> -e mode=install
```

Remove: `kubectl delete node <vm>`, then delete the VM and its disk.
