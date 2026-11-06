# api_endpoint_env (experiment)

Test-only. Flannel and the thick Multus daemon use client-go's in-cluster config, i.e. the kubelet-injected
`KUBERNETES_SERVICE_HOST/PORT` = the `kubernetes` Service ClusterIP, which only works once kube-proxy has programmed it.
This sets both variables on every container and init container of the two DaemonSets to the API endpoint (the
host must be in the API server certificate, e.g. kubeadm's controlPlaneEndpoint), so neither depends on kube-proxy
at boot; uninstall removes them.

```sh
KUBECTL=... ansible-playbook experiments/api_endpoint_env/play.yml -e mode=install -e api_endpoint_host=kube-api.example
ansible-playbook experiments/api_endpoint_env/play.yml -e mode=uninstall
```
