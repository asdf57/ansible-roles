# Ansible roles and external operators

## Code ownership and verification

- `operators/provisioning.py` owns the bounded provisioning state machine.
  `operators/ssh_host_keys.py` owns managed SSH identity/trust reconciliation.
  `operators/common.py` contains shared API, SSH and live Ansible output helpers.
- Managed-node mutations run through `plays/provision_stage.yml` and roles.
  Keep disk-changing commands out of Python operator code and ad hoc SSH.
- Test with `python3 -m unittest discover -s operators/tests` and syntax-check
  changed plays with the project's Ansible runner image. Syntax checks and the
  GRUB VM fixture are not full installation acceptance tests.

## Provisioning procedure

1. Read the Server, Machine binding, capture group, ISO and CA state. Verify the
   desired disk is explicitly approved; other Servers must not be accidentally
   enabled. Use `--preflight` with the ordinary operator credentials to inspect
   without claiming, rebooting or installing.
2. Resolve inventory through Ansible (`resolved_inventory_hosts`), not by
   flattening raw hostvars: storage and other inputs live in inherited group vars.
3. Existing disks require an explicit monotonically increasing
   `spec.provisioning.reprovision` request. Counter 0 accepts only a blank disk.
   An ordinary ISO/CA/spec edit must not authorize replacement.
4. The shared `reconcile-ssh-host-keys-ssh-managed/provision` job polls the
   `ssh-managed` capture group. Triggering the job only polls desired state;
   it does not increment counters. The SSH and provisioning jobs share the
   `server-lifecycle` serial group.
5. Installation must verify the pinned live build, boot ID, stable disk identity,
   unmounted target, protected agent enrollment and managed SSH identity before
   erasure. A completed build or an SSH port opening is not this verification.
6. Success requires a changed installed boot, ext4 root on the approved disk,
   matching protected marker, restored GRUB, strict managed SSH and healthy
   management services. Only then advance `observedReprovision` and release
   maintenance.

## Boot and SSH lessons

- Installed OS boots locally by default. Reprovision uses the one-shot
  `grub-reboot homelab-netboot` entry; do not make network/API availability a
  dependency of every normal boot.
- An older verified Arch USB-live session can refresh into the pinned Arch
  image through guarded Ansible kexec stages. It cannot be accepted directly
  for erasure without the immutable live-build marker.
- Arch HTTP-live boot requires `ip=dhcp net.ifnames=0 BOOTIF=01-<boot-MAC>`.
  In iPXE use `BOOTIF=01-${netX/mac}`; in kexec use the verified MAC with hyphens.
  Without BOOTIF, ip-config can report `SIOCGIFFLAGS: No such device`.
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

For a failed task, use `fly ... hijack -b <buildID> -s reconcile-provisioning --
<read-only-diagnostic-command>`. Inspect only the relevant private log; never
dump task environments, credential files or entire Secret-bearing resources.
