# MetalLB

Reconciles release `metallb` in namespace `metallb-system` using the bundled
upstream chart **0.16.1** (MetalLB **v0.16.1**). Chart source:
https://github.com/metallb/metallb/tree/metallb-chart-0.16.1/charts/metallb

Archive SHA-256:
`fb06bb584fcb7856f15733b2a6a2aff5b61b5c350687e341c163ae24a5938adc`.

Run locally in the unprivileged management container with Helm,
`kubernetes.core` and an already verified private `KUBECONFIG`:

```sh
ansible-playbook -i localhost, "$ANSIBLE_PLAYS_PATH/metallb.yml" -e mode=install
ansible-playbook -i localhost, "$ANSIBLE_PLAYS_PATH/metallb.yml" -e mode=uninstall
```

Install uses `atomic: true`, waits for readiness and has a five-minute timeout.
Uninstall uses the same release/namespace and waits for removal. These tasks do
not install host packages, register Helm repositories, retrieve credentials,
escalate the runner, or write shared temporary manifests.

`metallb_values` supplies chart overrides. The default disables the FRR-K8s BGP
backend because the intended first exercise is L2 advertisement. Controller,
speaker and CRDs remain enabled. Speaker pods necessarily use node networking
and the upstream chart's network permissions; an unprivileged runner does not
mean every installed Kubernetes workload is unprivileged.

## Your network configuration

This role installs MetalLB only. It does **not** select addresses, create an
`IPAddressPool` or `L2Advertisement`, change Services, configure DNS/TLS or open
router ports. The old hardcoded pool and shell-based application path are removed.

The next exercise is to choose unused LAN addresses reserved outside DHCP, write
an `IPAddressPool` and an `L2Advertisement` referencing it, and reconcile those
through reviewed Ansible tasks after Helm is ready. Start from your actual LAN
and DHCP allocations; do not assume an address is free from a failed ping.
Then a Service of type `LoadBalancer` can request an address from that pool.

`homelab-init/Configuration/configuration-metallb-lab.yaml` is a **suspended**
scaffold with explicit `lab` context and cluster-setup dependency. Nothing has
been applied. Review your network setup before enabling it. Do not make Grafana
depend on the suspended scaffold yet.

Uninstalling MetalLB can interrupt all Services using its advertised addresses.
The bundled chart manages CRDs through its CRD subchart: removal can also delete
those CRDs and their custom resources. Suspend/change any install Configuration
before uninstalling; do not delete the namespace or cluster resources as a cleanup
shortcut. Helm atomic mode is not a transaction over all cluster-side effects.

See [the component role contract](../../docs/kubernetes-component-roles.md).
