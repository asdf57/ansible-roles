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
| `plays/init_pipelines.yml` | Configure one Concourse pipeline per inventory publication. |
| `plays/init_clean.yml` | Remove the Compose project and generated platform data. |

The main entry point runs the bootstrap and platform phases. Artifact
publication and pipeline configuration are explicit opt-ins.

Run `homelabc init`. Add `--artifacts` and/or `--pipelines` when those optional
phases are wanted.

For targeted reruns, invoke `init_bootstrap.yml`, `init_platform.yml`,
`init_artifacts.yml`, or `init_pipelines.yml` directly. The bootstrap defaults
to `https://github.com/asdf57/homelab-init.git` at `main`; override it with
`GIT_HOMELAB_INIT_REPO` and `GIT_HOMELAB_INIT_REF` when needed.

After the first bootstrap, individual phases can be rerun safely without
repeating the earlier phases.
