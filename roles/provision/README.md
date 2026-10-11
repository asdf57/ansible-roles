# Provisioning role

The external provisioning operator executes bounded stages through
`plays/provision_stage.yml`. Every destructive installation requires an explicit
immutable ProvisioningRun and a fresh pinned live boot with a changed boot ID.

The run pins per-server DiskPartition specifications and concrete MiB geometry.
Roles consume explicit partition numbers, filesystems and mount points; root
and EFI are required, swap is optional, and ext4 data partitions are supported.
Storage is never inherited from capture-group variables.

Support remains amd64 UEFI with Secure Boot off, Arch rolling or Debian trixie,
and one writable nonremovable SATA/ATA/NVMe/MMC user-area disk selected by stable
by-id identity. MMC hardware boot/RPMB/general-purpose areas are never targets;
the live probe rejects their resolved kernel names even if writable. Discovery
still reports all devices. Existing ordinary GPT partitions are replaceable
only as part of an explicitly approved whole-disk installation.
Installation rechecks disk/session/mount/swap safety before erasure, verifies
the complete created layout, generates UUID-based fstab and installs GRUB using
the declared EFI partition. Final verification attests actual installed storage,
OS, boot contract, managed SSH identity and healthy discovery.
Installed initramfs is regenerated with final mounts/fstab before installing GRUB.

See Stigmergy RFCs 0003 and 0007 and its server-provisioning-rollout guide.

Run `ansible-playbook -i localhost, roles/provision/tests/storage_geometry.yml`
to test the real numbered-GPT creation task on a temporary regular file. It
requires local sgdisk/sfdisk/truncate, never uses a block device, and removes
only its allocated fixture directory. Run the assertion fixtures separately
with `python3 operators/tests/provision_contract_ansible.py` in the runner image.
