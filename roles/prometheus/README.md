# Prometheus

Installs the standalone Prometheus Community chart **29.37.0** (Prometheus
**v3.15.0**), bundled under `helm/` with its packaged dependencies. Upstream:
https://github.com/prometheus-community/helm-charts/tree/prometheus-29.37.0/charts/prometheus

The upstream `prometheus-29.37.0.tgz` archive SHA-256 is
`df07dce99f49ea300c5bb8dadb167a09141bb7dbdc6609727157677434e4fbe5`.

Like Grafana and node_exporter, this runs locally in the unprivileged management
runner with Helm, `kubernetes.core`, and a verified private `KUBECONFIG`. It does
not install tools, retrieve credentials, or connect to managed nodes via SSH.

```sh
ansible-playbook -i localhost, "$ANSIBLE_PLAYS_PATH/prometheus.yml" -e mode=install
ansible-playbook -i localhost, "$ANSIBLE_PLAYS_PATH/prometheus.yml" -e mode=uninstall
```

The release and namespace are both `prometheus`. Install is atomic and waits up
to five minutes; failed initial installs are removed and failed upgrades roll
back. Uninstall waits for removal and preserves the namespace and any resources
retained by chart policy. Uninstall does not override a desired install
Configuration: suspend or change that Configuration first.

`prometheus_values` supplies chart overrides. Defaults disable Alertmanager,
kube-state-metrics, node_exporter and Pushgateway: these are separately managed
components, not implicit installations. This is not kube-prometheus-stack and
does not install Prometheus Operator or ServiceMonitor CRDs.

Storage defaults to `emptyDir` so installation does not assume a StorageClass.
Metrics are lost when the server pod is replaced. Configure
`prometheus_values.server.persistentVolume` before relying on durable history.
The Service is ClusterIP; there is no public ingress or authentication configured.
The default server URL inside this cluster is
`http://prometheus-server.prometheus.svc.cluster.local`.

Upstream Kubernetes discovery and annotation-based scraping remain enabled.
Existing exporters without scrape annotations need annotations or explicit scrape
jobs supplied through chart values; installing Prometheus alone does not wire
every existing exporter or configure Grafana's datasource. ServiceMonitor objects
are not consumed by this standalone server.

The matching site resource is
`homelab-init/Configuration/configuration-prometheus-lab.yaml`. It runs
`ansible-playbook plays/prometheus.yml -e mode=install`, requests the verified
`lab` Kubernetes context and depends on `kube-setup-lab`. No credentials belong
in the manifest. Publish the role source before applying the Configuration.
