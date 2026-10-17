# Grafana

Reconciles the `grafana` Helm release in the `grafana` namespace using the bundled
Grafana Community chart 13.5.0. Upstream chart source:
https://github.com/grafana-community/helm-charts/tree/main/charts/grafana

Like node_exporter, run the role once on the unprivileged runner with Helm,
`kubernetes.core` and `KUBECONFIG` pointing to the intended private cluster context.
Use `ansible.builtin.include_role` with `name: grafana` and `tasks_from: install`
or `tasks_from: uninstall`. The supplied plays use local execution without become.

The top-level playbook is `plays/grafana.yml`, matching `plays/node_exporter.yml`.
Pass `mode=install` or `mode=uninstall`. It requires the verified kubeconfig to
be prepared in the execution environment first; it does not fetch credentials.

From an initialized interactive homelabc shell, run:

```sh
ansible-playbook -i localhost, "$ANSIBLE_PLAYS_PATH/grafana.yml" -e mode=install
ansible-playbook -i localhost, "$ANSIBLE_PLAYS_PATH/grafana.yml" -e mode=uninstall
```

Install is atomic and waits up to five minutes for readiness. Failed initial
installs are removed; failed upgrades roll back. Uninstall waits up to five
minutes and preserves the namespace and resources retained by chart policy.
There is no runtime package installation, repository registration or shared
temporary values file.

`grafana_values` supplies chart overrides. Defaults provision the default
Prometheus datasource (UID `prometheus`) at
`http://prometheus-server.prometheus.svc.cluster.local` and Node Exporter Full
(dashboard 1860, pinned revision 45) in the **Infrastructure** folder. Both are
file-provisioned and managed in Git, not edited through Grafana. The upstream
dashboard JSON is bundled in the chart; startup does not fetch it from grafana.com.
Its job selector discovers node_exporter metrics, including annotation-discovered
targets with the `kubernetes-service-endpoints` job name.

The matching `lab-grafana` Configuration depends on `kube-setup-lab`,
`lab-prometheus` and `lab-node-exporter`. Reconciliation provisions the datasource
and dashboard through Helm; no manual Grafana setup is required. This does not
install Prometheus or exporters inside the Grafana release.

The chart defaults to a ClusterIP Service and disabled persistence. Provisioned
datasources/dashboards are recreated from Git after pod replacement; other UI
changes are not durable without persistence. No ingress or LoadBalancer is added.
For another Prometheus location, override the datasource URL in `grafana_values`.

There is no hardcoded admin password. The chart generates credentials in a
Kubernetes Secret by default; for managed credentials, use `grafana_values.admin`
with `existingSecret`, `userKey` and `passwordKey` referencing an existing Secret.
Never commit credentials or place them in public Configuration values.

Interactive homelabc environments can supply the verified Kubernetes context.
Configurations opt in with `context.kubeClusterRef: {name: lab}`; the executor
prepares a private, identity-verified kubeconfig before running command.argv.
A KubeCluster targetRef alone does not provide credentials.
