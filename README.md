# ansible-roles

Ansible playbooks and reusable tasks for the homelab.

## Container runtime

The standalone [containerd role](roles/containerd/README.md) installs the runtime
on an explicitly selected installed Debian/Arch host. Its entry point is
`plays/containerd.yml`; it is not automatically applied during OS provisioning.
Cluster resource lookup and kubeadm init/join remain planned external-operator work.

## Operator development

```sh
make sync        # uv.lock pins the development tools
make format      # YAPF
make check       # formatting, ty, Pylint policy, local unit tests
make lint-report # full Pylint findings, independently of the focused gate
```

The gate checks function/class/module documentation, naming, unused code,
function size/branching, compound-condition size, subprocess checks and types.
The full report also includes advisory findings such as broad exception handlers;
passing the gate does not mean the full report is empty. Wire JSON has an explicit
recursive union of JSON values, not `Any` or `object`. The client validates resource
envelopes and narrows endpoint response fields before using them; spec/status
contents still require workflow validation. Type checking does not replace runtime
identity checks. Tools cannot establish meaningful names or explanations:
review must still check those, along with fail-closed behavior.

The checks do not connect to managed nodes, reboot or provision them. Real local
Ansible callback/contract fixtures are separate runner tests described in AGENTS.md.
GitHub Actions runs the same gate on pushes and pull requests and also shows the
full advisory Pylint report. It does not deploy anything.

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
| `plays/init_artifacts.yml` | Publish the normal command-runner image. |
| `plays/init_clean.yml` | Remove the Compose project and generated platform data. |

The main entry point runs the bootstrap and platform phases. Artifact
publication is an explicit opt-in. Stigmergy reconciles command pipelines from
`CommandsPipeline` resources during normal operation.

Run `homelabc init`. Add `--artifacts` when the command-runner image should
also be published. ISO builds use public base images and are owned by the
Stigmergy `Pipeline/build-isos` resource.

For targeted reruns, invoke `init_bootstrap.yml`, `init_platform.yml`,
or `init_artifacts.yml` directly. The bootstrap defaults to
`https://github.com/asdf57/homelab-init.git` at `main`; override it with
`GIT_HOMELAB_INIT_REPO` and `GIT_HOMELAB_INIT_REF` when needed.

After the first bootstrap, individual phases can be rerun safely without
repeating the earlier phases.
