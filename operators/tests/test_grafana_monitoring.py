"""Verify declarative Grafana monitoring with local Helm rendering only."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / 'roles' / 'grafana'
DASHBOARD = ROLE / 'helm/dashboards/node-exporter-full.json'


@unittest.skipUnless(shutil.which('helm'), 'Helm is required for chart rendering')
class GrafanaMonitoringTests(unittest.TestCase):
    """Check that role defaults provision working datasource and dashboard files."""

    @classmethod
    def setUpClass(cls):
        """Render the bundled chart without contacting Kubernetes or Grafana."""
        defaults = yaml.safe_load((ROLE / 'defaults/main.yml').read_text(encoding='utf-8'))
        result = subprocess.run([
            'helm', 'template', 'grafana',
            str(ROLE / 'helm'), '--namespace', 'grafana', '--values', '-'
        ], input=yaml.safe_dump(defaults['grafana_values']), text=True, capture_output=True,
                                check=True)
        cls.manifests = [manifest for manifest in yaml.safe_load_all(result.stdout) if manifest]

    def test_prometheus_datasource_is_provisioned(self):
        """Grafana uses the cluster-local Prometheus service with a stable UID."""
        config = next(
            manifest for manifest in self.manifests
            if manifest['kind'] == 'ConfigMap' and manifest['metadata']['name'] == 'grafana')
        datasource = yaml.safe_load(config['data']['datasources.yaml'])['datasources'][0]
        self.assertEqual(datasource['uid'], 'prometheus')
        self.assertEqual(datasource['url'], 'http://prometheus-server.prometheus.svc.cluster.local')
        self.assertEqual(datasource['type'], 'prometheus')
        self.assertEqual(datasource['access'], 'proxy')
        self.assertTrue(datasource['isDefault'])
        self.assertFalse(datasource['editable'])

    def test_bundled_dashboard_matches_the_pinned_upstream_asset(self):
        """Preserve the reviewed revision rather than downloading latest on each run."""
        self.assertEqual(
            hashlib.sha256(DASHBOARD.read_bytes()).hexdigest(),
            '184c6b7409f306da75525d7772f71945b10cea23ad16b5d78c4698ea0ea51986')
        config = next(manifest for manifest in self.manifests if manifest['kind'] == 'ConfigMap'
                      and manifest['metadata']['name'] == 'grafana-dashboards-default')
        dashboard = json.loads(config['data']['node-exporter-full.json'])
        self.assertEqual(dashboard, json.loads(DASHBOARD.read_text(encoding='utf-8')))
        self.assertEqual(dashboard['title'], 'Node Exporter Full')
        variables = {variable['name']: variable for variable in dashboard['templating']['list']}
        self.assertEqual(variables['ds_prometheus']['query'], 'prometheus')
        self.assertEqual(variables['job']['query']['query'], 'label_values(node_uname_info, job)')

    def test_provider_and_mount_are_managed_without_remote_downloads(self):
        """Provision the file in the chart's mounted path and roll out changed content."""
        config = next(
            manifest for manifest in self.manifests
            if manifest['kind'] == 'ConfigMap' and manifest['metadata']['name'] == 'grafana')
        provider = yaml.safe_load(config['data']['dashboardproviders.yaml'])['providers'][0]
        self.assertEqual(provider['folder'], 'Infrastructure')
        self.assertEqual(provider['options']['path'], '/var/lib/grafana/dashboards/default')
        self.assertFalse(provider['allowUiUpdates'])
        self.assertNotIn('curl', config['data']['download_dashboards.sh'])
        deployment = next(manifest for manifest in self.manifests
                          if manifest['kind'] == 'Deployment')
        template = deployment['spec']['template']
        self.assertIn('checksum/dashboards-json-config', template['metadata']['annotations'])
        grafana = next(container for container in template['spec']['containers']
                       if container['name'] == 'grafana')
        self.assertTrue(
            any(mount['mountPath'].endswith('/node-exporter-full.json')
                for mount in grafana['volumeMounts']))


if __name__ == '__main__':
    unittest.main()
