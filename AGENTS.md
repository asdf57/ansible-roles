# Ansible roles and external operators

## Code ownership and verification

- Install `requirements-dev.txt` in a virtual environment. Format with
  `yapf -i -r operators`; check with `yapf --diff -r operators` and
  `PYTHONPATH=operators:operators/tests pylint --persistent=n operators`.
  Keep lint warnings visible; do not
  suppress safety findings to manufacture a clean score.
- `operators/provisioning.py` owns the bounded provisioning state machine.
  `operators/ssh_host_keys.py` owns managed SSH identity/trust reconciliation.
  `operators/common.py` contains shared API, SSH and live Ansible output helpers.
- Managed-node mutations run through `plays/provision_stage.yml` and roles.
  Keep disk-changing commands out of Python operator code and ad hoc SSH.
- Test with `python3 -m unittest discover -s operators/tests` and syntax-check
  changed plays with the project's Ansible runner image. Syntax checks and the
  GRUB VM fixture are not full installation acceptance tests.
- Run `python3 operators/tests/provision_contract_ansible.py` in the Ansible
  runner to exercise actual installation contract assertions with local fake
  probe facts. It runs no node connections, disk commands or privileged tasks.

## Provisioning procedure

1. Read the Server, Machine binding, capture group, ISO and CA state. Verify the
   desired disk is explicitly approved; other Servers must not be accidentally
   enabled. Use `--preflight` with the ordinary operator credentials to inspect
   without claiming, rebooting or installing.
2. Resolve inventory through Ansible (`resolved_inventory_hosts`), not by
   flattening raw hostvars: storage and other inputs live in inherited group vars.
3. Every install requires an immutable ProvisioningRun with the reviewed Server
   generation, UID-qualified Server/Machine refs and one discovered disk ID.
   Creation authorizes replacement, including existing data. Enabling a Server
   or changing OS/ISO/CA never authorizes installation.
4. The shared `reconcile-ssh-host-keys-ssh-managed/provision` job polls the
   `ssh-managed` capture group. Triggering the job only polls desired state;
   it does not create runs. The SSH and provisioning jobs share the
   `server-lifecycle` serial group.
5. Installation must verify the pinned live build, boot ID, stable disk identity,
   unmounted target, protected agent enrollment and managed SSH identity before
   erasure. A completed build or an SSH port opening is not this verification.
6. Success requires a changed installed boot, ext4 root on the approved disk,
   matching protected marker, restored GRUB, strict managed SSH and healthy
   management services. Only then complete the run and release maintenance.
   Run status owns checkpoints/snapshot; Server holds activeRunRef, lastRunRef,
   lastSuccessfulRunRef and the maintenance gate.

## Boot and SSH lessons

- iPXE "Permission denied" can be TLS validation, not HTTP auth. Check the
  exact error code and served chain. Embed/trust the packaged roots for both
  ZeroSSL (USERTrust ECC/RSA) and Let's Encrypt (ISRG X1); never bypass TLS.
- `plays/build_ipxe.yml` rebuilds only the public boot artifact. For installed
  boot-only repair use `plays/repair_boot.yml` with exact disk/boot/root/Server
  identity inputs and a scoped inventory; it never reboots or provisions.
- Run `bash operators/tests/ipxe_https_vm.sh` after the builder image exists.
  QEMU user networking must not overlap the actual HTTPS server subnet (the
  site uses 10.0.2.x); this fixture uses 172.30.90.0/24 and serial-only iPXE.
- Live and installed NIC names can differ (`eth0` versus `enp1s0`). Priming
  resolves the attempt's pinned MAC on the node, requires a unique match, and
  rechecks its MAC before ethtool writes; never use a cached API interface name.
- Installed OS boots locally by default. Reprovision uses the one-shot
  `grub-reboot homelab-netboot` entry; do not make network/API availability a
  dependency of every normal boot.
- An older verified Arch USB-live session can refresh into the pinned Arch
  image through guarded Ansible kexec stages. It cannot be accepted directly
  for erasure without the immutable live-build marker.
- Arch HTTP-live boot requires `ip=dhcp net.ifnames=0 BOOTIF=01-<boot-MAC>`.
  In iPXE use `BOOTIF=01-${netX/mac}`; in kexec use the verified MAC with hyphens.
  Without BOOTIF, ip-config can report `SIOCGIFFLAGS: No such device`.
- Debian live-boot must not receive `ip=dhcp`: its static-IP parser turns this
  into `nameserver dhcp`. HTTPS fetch already requests DHCP. Keep BOOTIF, omit
  the static override. For an already-booted affected Debian live session,
  `plays/repair_debian_live_dns.yml` verifies the pinned boot/build/disk, restores
  validated DNS from the default interface's `/run/net-*.conf` DHCP lease and
  installs live cleanup prerequisites. It does not erase, mount or reboot.
- The live rootfs is downloaded into RAM. The current image failed in a 2 GiB
  VM and reached login/OpenSSH with 4 GiB. Verify real hardware capacity.
- Use command modules/Python stdin for JSON probes. Ansible's script/PTY path
  can add terminal escape sequences that break JSON parsing.
- On a verified live SSH handoff, keep both the persisted bootstrap pin and the
  verified managed public key in known_hosts until sshd reload/cleanup finishes;
  then retain only the managed key. Established hosts never silently fall back
  to TOFU. Explicit manual live recovery must be scoped and inspected first.
- Ensure `/etc/homelabd` exists before writing enrollment or live-build files.
  Preserve the agent token and managed SSH key into the installed root.
- Empty Ansible `copy.content` is rejected. Configuration templates that may
  contain no entries need a harmless header or an explicit empty-state action.

## Failure and repair

- For a proven failure before erasure with an intact prior installation, use
  `plays/verify_failed_provision_cleanup.yml` with a Beelink-only inventory and
  the exact run UID, Server UID, live boot/build IDs, disk identity and by-id path.
  It verifies the old root/marker, clears only its GRUB next_entry, flushes and
  unmounts. It never reinstalls or reboots. Only after success report the run as
  Blocked with maintenance=false, then release through `release_run`/Server status.
  A false netbootArmed flag or changed boot ID alone does not prove GRUB cleanup.
- After repartitioning, `grub-install` may retain a same-named Homelab entry
  with the old EFI PARTUUID. Reconcile the exact active loader/partition with
  `reconcile_efi.yml`, not the display name alone. For an existing partial root,
  use guarded configuration-only repair, verify the staged marker, then resume
  installed boot verification with the original pinned operator revision.
- Never automatically replay an interrupted `Installing` attempt or clear its
  maintenance reservation just to make a build green. Inspect disk/checkpoint.
- `repair` is an explicit configuration-only stage for an existing partial
  root. It verifies the original live boot/build/disk, exact root/EFI mount
  sources and chroot bind mounts, then uses `configure_installed.yml`. It must
  never partition, format or bootstrap again. If those prerequisites are absent,
  stop and inspect; do not weaken the assertions.
- After repair, verify the completed staged marker and boot contract before
  reporting `AwaitingInstalled` for that same attempt. Final boot/verification
  uses the pinned original code revision. Record the repair revision separately
  in the checkpoint message; never rewrite the immutable snapshot.

## Concourse pinning and diagnostics

Use an authenticated `fly` target; do not put passwords/tokens in public logs.
Replace example values with the actual pipeline/resource/job/commit.

```sh
fly -t homelab builds --json
fly -t homelab watch -j reconcile-ssh-host-keys-ssh-managed/provision
fly -t homelab pin-resource -r reconcile-ssh-host-keys-ssh-managed/operator-code -v ref:<full-commit>
fly -t homelab trigger-job -j reconcile-ssh-host-keys-ssh-managed/provision
fly -t homelab unpin-resource -r reconcile-ssh-host-keys-ssh-managed/operator-code
fly -t homelab check-resource -r reconcile-ssh-host-keys-ssh-managed/operator-code
```

The version flag uses `ref:<commit>`, NOT `ref=<commit>`. The version must exist
in Concourse's checked resource history. Pinning affects all jobs using that
resource; unpin/check latest after the owned attempt completes.

Standard Ansible task/results/recap output streams live through `run_ansible`.
Credential tasks require `no_log`; do not enable verbose argument display.
Private logs (0700 directory, 0600 file) are retained inside task containers:

- `/tmp/provision-operator-diagnostics/<attemptID>-<stage>.log`
- `/tmp/ssh-host-operator-diagnostics/<serverUID>-<play>.log`

Use a CLI matching the server. A temporary matching binary can be downloaded
without replacing the system installation:

```sh
curl --fail --silent --show-error 'https://ci.ryuugu.dev/api/v1/cli?arch=amd64&platform=linux' -o /tmp/homelab-fly
chmod 0755 /tmp/homelab-fly
/tmp/homelab-fly -t homelab builds --json
```

The shared API client's status writes route by resource kind. Test the real
client URL as well as fake-store workflows: a fake store alone cannot detect
ProvisioningRun checkpoints accidentally sent to the Server collection.

For a failed task, use `fly ... hijack -b <buildID> -s reconcile-provisioning --
<read-only-diagnostic-command>`. Inspect only the relevant private log; never
dump task environments, credential files or entire Secret-bearing resources.
