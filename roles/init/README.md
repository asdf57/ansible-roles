# init task library

The `init` role is a task library used by the phase playbooks in `plays/`.

## Lifecycle boundary

The bootstrap phase starts the minimum control plane:

- OpenBao and its Agent
- etcd
- Stigmergy on the direct bootstrap API address
- the OpenBao `SecretStore` and environment-backed bootstrap secrets

The phase then clones and applies the declarative resources from
`homelab-init`. Those resources create the Git repository, SSH key pairs,
inventory capture groups, inventory publications, servers, and other static
desired state. `GIT_HOMELAB_INIT_REPO` and `GIT_HOMELAB_INIT_REF` may
override the default repository and `main` revision.

The platform phase reads the ready `platform` capture group directly from
Stigmergy, renders `group_vars/all.yaml`, and starts
the complete infrastructure Compose stack. Git publication remains an output
for normal operator runs, but it is not a bootstrap dependency.

## DNS records

After the full Compose stack starts and Stigmergy reports ready, the platform
phase creates `A` records for every FQDN nginx exposes: nginx ACME, OpenBao
ACME/API, Concourse, registry, Stigmergy, and Vikunja. These records use
`nginx_ipv4`.

Configure their backing Router in the published group variables:

```yaml
dns_record_backing_store_ref:
  kind: Router
  name: mikrotik-1
dns_record_ttl: 300
```

If `dns_record_backing_store_ref` is omitted, the role uses
`PRIMARY_ROUTER_NAME` with kind `Router`.

Additional records can be declared with:

```yaml
dns_records:
  - resource_name: webhook
    name: webhook.
    zone: homelab.example.net
    value: "{{ webhook_ipv4 }}"
```

`resource_name` is the Stigmergy resource name. A trailing dot makes the DNS
name absolute; `@` selects the zone apex. Optional records default to type `A`
and the shared TTL, and may override `type`, `ttl`, or `backing_store_ref`.
Set `manage_dns_records: false` to disable DNS reconciliation.

The Router may be created after the DNS resources. Stigmergy keeps them pending
and reconciles them when the Router and its credential become ready, avoiding a
bootstrap dependency on managed DNS.

The bootstrap phase creates the Router's `UsernamePasswordCredential` directly
from `PRIMARY_ROUTER_NAME`, `PRIMARY_ROUTER_API_USERNAME`, and
`PRIMARY_ROUTER_API_PASSWORD` in the container environment. Its resource name
is `<PRIMARY_ROUTER_NAME>-credentials`. The Router manifest in `homelab-init`
should reference that name, for example:

```yaml
spec:
  authentication:
    type: UsernamePasswordCredential
    name: mikrotik-1-credentials
```
