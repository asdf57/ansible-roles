# Grafana

Reconciles the `grafana` Helm release in the `grafana` namespace using the bundled
Grafana Community chart 13.5.0. Upstream chart source:
https://github.com/grafana-community/helm-charts/tree/main/charts/grafana

Like node_exporter, run the role once on the unprivileged runner with Helm,
`kubernetes.core` and `KUBECONFIG` pointing to the intended private cluster context.
Use `ansible.builtin.include_role` with `name: grafana` and `tasks_from: install`
or `tasks_from: uninstall`. The supplied plays use local execution without become.

Install is atomic and waits up to five minutes for readiness. Failed initial
installs are removed; failed upgrades roll back. Uninstall waits up to five
minutes and preserves the namespace and resources retained by chart policy.
There is no runtime package installation, repository registration or shared
temporary values file.

`grafana_values` is a dictionary of chart overrides, defaulting to `{}`. The chart
defaults to a ClusterIP Service and disabled persistence: explicitly configure
persistence before relying on stored dashboards. No ingress, LoadBalancer,
datasources or Prometheus installation is assumed.

There is no hardcoded admin password. The chart generates credentials in a
Kubernetes Secret by default; for managed credentials, use `grafana_values.admin`
with `existingSecret`, `userKey` and `passwordKey` referencing an existing Secret.
Never commit credentials or place them in public Configuration values.

Interactive homelabc environments can supply the verified Kubernetes context.
Configuration/operator mode still needs explicit, identity-verified kubeconfig
preparation; a KubeCluster targetRef alone does not provide credentials.
