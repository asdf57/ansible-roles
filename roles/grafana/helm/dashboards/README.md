# Bundled dashboard

Node Exporter Full by rfmoz, Grafana dashboard **1860**, revision **45**.

Source: https://grafana.com/api/dashboards/1860/revisions/45/download
Project: https://github.com/rfmoz/grafana-dashboards
License: Apache-2.0; see LICENSE alongside the dashboard.
SHA-256: `184c6b7409f306da75525d7772f71945b10cea23ad16b5d78c4698ea0ea51986`.

The JSON is vendored unchanged. Its datasource variable discovers Prometheus
datasources, and its job selector queries node_uname_info rather than requiring
a hardcoded job name. No dashboard download is needed at reconciliation or startup.
