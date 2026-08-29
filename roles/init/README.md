# init

Bootstraps the homelab infrastructure Compose stack, including the provisioning
API, Vault, nginx, Concourse, and Stigmergy.

Stigmergy replaces the deprecated `prov-controller-test` service. The role
clones the Stigmergy source, starts its API with the required etcd and OpenBao
services, exposes it through nginx, and creates the `servers`
`InventoryCaptureGroup` using Stigmergy's `/api/v1alpha1` API.

## Required Stigmergy variables

Provide these variables in the rendered group vars template:

```yaml
stigmergy_repo: https://github.com/asdf57/stigmergy.git
stigmergy_version: main
stigmergy_ipv4: 10.0.0.10
stigmergy_fqdn: stigmergy.example.net
stigmergy_api_url: http://10.0.0.10:8080
```

`stigmergy_api_url` must be reachable by the Ansible control host while the
role runs. The proxied public endpoint uses `stigmergy_fqdn` over HTTPS.
