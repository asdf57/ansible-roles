# ansible-roles

Ansible playbooks and reusable tasks for the homelab.

## Initialize the platform

The core initialization entry point is:

```sh
ansible-playbook /homelab/plays/init.yml
```

Initialization is intentionally divided into independently runnable phases:

| Playbook | Responsibility |
| --- | --- |
| `plays/init_bootstrap.yml` | Start OpenBao, etcd, and Stigmergy; initialize the secret store and bootstrap secrets. |
| `plays/init_platform.yml` | Read bootstrap inventory from Stigmergy, start the full Compose stack, and reconcile DNS. |
| `plays/init_artifacts.yml` | Build and publish the Arch provisioner and ISO task images. |
| `plays/init_clean.yml` | Remove the Compose project and generated platform data. |

The main entry point runs the bootstrap and platform phases. Artifact
publication is an explicit opt-in. Stigmergy reconciles command pipelines from
`CommandsPipeline` resources during normal operation.

Run `homelabc init`. Add `--artifacts` when provisioning images should also be
built and published.

For targeted reruns, invoke `init_bootstrap.yml`, `init_platform.yml`,
or `init_artifacts.yml` directly. The bootstrap defaults to
`https://github.com/asdf57/homelab-init.git` at `main`; override it with
`GIT_HOMELAB_INIT_REPO` and `GIT_HOMELAB_INIT_REF` when needed.

After the first bootstrap, individual phases can be rerun safely without
repeating the earlier phases.
