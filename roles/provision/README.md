# Provisioning role

Executed by `operators/provisioning.py` through `plays/provision_stage.yml`; do not invoke installation directly with ordinary inventory variables.

The operator pins the Server UID, request counter, immutable live ISO build, desired OS, stable disk identity and installation inputs before reserving maintenance. The install stage rechecks the live boot ID and physical disk immediately before erasure. Existing partitions require an explicit replacement request; counter zero only accepts a blank disk.

V1 supports amd64 UEFI with Secure Boot disabled, Arch rolling or Debian trixie, and an EFI/swap/ext4 layout on a nonremovable SATA/NVMe disk selected by `/dev/disk/by-id`. It preserves the management account, SSH identity, CA trust and protected homelabd enrollment. Installation writes an attempt marker and restores GRUB before the operator reboots and verifies the installed OS.

An interrupted Installing checkpoint requires manual investigation and a new authorized request; it never automatically repeats erasure. See `stigmergy/docs/server-provisioning-rollout.md` in the workspace for rollout and recovery steps.
