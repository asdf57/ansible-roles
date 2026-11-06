# multus_2022 (experiment)

Test-only. Replaces the managed Multus (v4.3.1 Helm release) with the thick plugin
as it was in early March 2022, to match a prod cluster running a `20220315` build.

- Source: k8snetworkplumbingwg/multus-cni commit 45428a53ceedcf6359a7c2f75e8b84d64a10148b
  (2022-03-04, the last commit before 2022-03-16), built with its own `images/Dockerfile.thick`
  and pushed as `registry.ryuugu.dev/multus-cni:20220315-thick`
  (`sha256:416f92469cebea0c90fc8c6776962fdd314e65e94e516b3b8110bb7cfa0efff9`).
- Manifest: that commit's `deployments/multus-daemonset-thick-plugin.yml`, image swapped only.

```sh
ansible-playbook experiments/multus_2022/play.yml -l <nodes> -e mode=install
ansible-playbook experiments/multus_2022/play.yml -l <nodes> -e mode=uninstall
```

Uninstall deletes the 2022 objects, removes the `00-multus.conf` and `multus.d/`
the 2022 daemon leaves behind, and reinstalls the managed release.
