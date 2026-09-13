# init

Bootstraps the homelab infrastructure Compose stack, including OpenBao, nginx,
Concourse, etcd, and Stigmergy.

The role clones the Stigmergy source, starts it as the only API server with the
required etcd and OpenBao services, exposes it through nginx, and creates the `servers`
`InventoryCaptureGroup` using Stigmergy's `/api/v1alpha1` API.

## Required Stigmergy variables

Provide these variables in the active inventory's `group_vars/all.yaml`:

```yaml
git_stigmergy_repo: https://github.com/asdf57/stigmergy.git
git_stigmergy_branch: main
stigmergy_ipv4: 10.0.0.10
stigmergy_fqdn: stigmergy.example.net
```

The proxied public endpoint uses `stigmergy_fqdn` over HTTPS.

## Bootstrap secrets

The Stigmergy API address and secret values are supplied to the provisioning
container as environment variables. Other referenced configuration belongs in
the active inventory's `group_vars/all.yaml`.

`MOUNT_DATA_PATH` is the container-visible mount of `host_data_path`. The role
writes generated infrastructure files there and uses it as the Docker Compose
project directory.

The role starts the complete Compose stack first, including OpenBao, its Agent,
etcd, and Stigmergy. After Stigmergy reports ready, the role creates the
`openbao` `SecretStore` and creates or replaces these Secret resources:

- `primary-router-api-password`
- `github-webhook-secret`
- `concourse-password`
- `concourse-oauth-client-secret`
- `cloudflare-api-key`
- `zerossl-eab-kid`
- `zerossl-eab-hmac-key`
- `postgres-password`

`STIGMERGY_API_URL` selects the API server. The corresponding secret values
come from `PRIMARY_ROUTER_API_PASSWORD`,
`GITHUB_WEBHOOK_SECRET`, `CONCOURSE_PASSWORD`,
`CONCOURSE_OAUTH_CLIENT_SECRET`, `CLOUDFLARE_API_KEY`, `ZEROSSL_EAB_KID`,
`ZEROSSL_EAB_HMAC_KEY`, and `POSTGRES_PASSWORD`. OpenBao initialization and
AppRole credentials remain isolated in Docker volumes and are not uploaded as
Secret resources.

Concourse uses its Vault-compatible credential manager against the same `kv2`
mount and reads the Stigmergy-managed secrets beneath the `secrets` prefix.
