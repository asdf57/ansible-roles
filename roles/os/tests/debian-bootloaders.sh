#!/usr/bin/env bash
set -euo pipefail

builder="$(dirname -- "${BASH_SOURCE[0]}")/../files/debian-trixie/build.sh"
# Exercise the real configuration function without creating a workspace or image.
source <(sed -n '/^function build_image() {/,/^}/p' "$builder")
DISTRIBUTION=trixie
lb() {
  if [[ "$1" == config ]]; then
    shift
    while (($#)); do
      if [[ "$1" == --bootloaders ]]; then selected_bootloader=$2; break; fi
      shift
    done
  fi
}
die() { echo "$*" >&2; exit 1; }
for scenario in 'uefi iso grub-efi' 'uefi netboot syslinux' 'bios iso syslinux' 'bios netboot syslinux'; do
  read -r IMAGE_BOOT_MODE type expected <<< "$scenario"
  selected_bootloader=''
  build_image
  [[ "$selected_bootloader" == "$expected" ]] || die "Wrong bootloader for $IMAGE_BOOT_MODE $type"
done
echo 'Debian ISO and netboot bootloader configuration passed'
