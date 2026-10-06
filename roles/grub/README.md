# GRUB role

Installs the stable `homelab-netboot` menu entry in the newly provisioned UEFI system. The normal default remains the installed OS; normal startup does not depend on the API or networking.

The entry chainloads `/boot/ipxe/ipxe.efi`, built with an embedded public API bootstrap script. The operator arms it once with `grub-reboot homelab-netboot` and verifies `next_entry` before rebooting. GRUB consumes that selection; subsequent ordinary boots return to the installed OS.

The role downloads the iPXE artifact and SHA-256 checksum from the configured HTTPS site, generates and checks GRUB configuration, and clears stale one-shot selections. The provisioning role verifies the resulting UEFI boot entry. Secure Boot and legacy BIOS are outside the v1 supported path. Broken-disk/bootloader recovery is manual.
