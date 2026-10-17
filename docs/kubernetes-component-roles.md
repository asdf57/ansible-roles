# Kubernetes component role contract

Use this structure for Helm-backed components such as Grafana, node_exporter,
Goldpinger, Prometheus and MetalLB:

```text
plays/<component>.yml
roles/<component>/
  tasks/install.yml
  tasks/uninstall.yml
  defaults/main.yml
  helm/                    # pinned upstream chart, including dependencies
  README.md
homelab-init/Configuration/configuration-<component>-<cluster>.yaml
```

`homelab-init` is a separate repository. A component is not complete without
its matching Configuration, unless the user explicitly wants to author that
manifest; a learning scaffold must be suspended rather than deployed implicitly.

## Playbook

Run once on `localhost` with `connection: local`, `gather_facts: false` and
`become: false`. Require `mode` to be `install` or `uninstall` using a quiet
assertion. Conditionally import the matching role task file with `tasks_from`.
Separate every sibling task with a blank line. Do not hide installation in
`tasks/main.yml`, nested role-local plays or shell wrapper scripts.

Use `plays/metallb.yml` as a concise reference. The command is:

```sh
ansible-playbook -i localhost, "$ANSIBLE_PLAYS_PATH/<component>.yml" -e mode=install
```

## Helm tasks and values

Use `kubernetes.core.helm` with the local `{{ role_path }}/helm` chart, explicit
release name/namespace, and `kubeconfig` read from the `KUBECONFIG` environment.
The runner supplies tools and the verified context; roles do not install packages,
register remote repositories or fetch credentials. Never use shared `/tmp` values
files or shell `kubectl apply` wrappers.

Install uses `state: present`, `create_namespace: true`, `atomic: true`,
`wait: true`, and `timeout: 5m`. Uninstall uses the same release and namespace,
`state: absent`, `wait: true`, and `timeout: 5m`. Do not recursively delete a
namespace or unrelated resources. Document retained/deleted data and CRDs;
atomic Helm behavior does not make CRDs or external effects transactional.

Keep chart overrides in `<component>_values` under `defaults/main.yml` and pass
them through `values`. Record upstream chart/app versions and source in README.
Keep service addresses, storage assumptions and any network permissions explicit.
Bundle pinned dashboard/manifests when needed; do not download mutable latest
assets during startup. Keep secrets out of defaults and public manifests.

## Configuration

The site Configuration declares `targetRef: {kind: KubeCluster, name: <cluster>}`,
`inventoryCaptureGroupRef`, `source.repositoryRef`, and literal
`command.argv: [ansible-playbook, plays/<component>.yml, -e, mode=install]`.
Request `context.kubeClusterRef` explicitly and list setup/prerequisite
Configurations in `dependsOn`. Do not add component-specific pipelines.

The operator resolves source, prepares the private Kubernetes context, orders
dependencies and reports generic Configuration status. The role verifies its
component through Helm readiness. Configuration values are public namespaced
context, not automatically injected Ansible variables; arguments must explicitly
load any additional input file they need.

Publish role source before applying executable desired state. Suspension stops
reconciliation without uninstalling; deletion does not uninstall either. To
uninstall, explicitly change the desired command to `mode=uninstall` (and review
dependents) or suspend installation before a deliberate manual uninstall.

## Verification

Syntax-check both modes, render/lint the chart with role defaults, and test release
identity, readiness flags and intended resources without contacting the cluster.
For deployment acceptance, inspect current Configuration generation/source status
and actual workload behavior; a green pipeline alone is insufficient.
