#!/bin/bash

set -euo pipefail

# This script builds a Debian Trixie live image using live-build.
#
# live-build assembles images from a directory tree named "config":
# - package-lists: extra packages to install into the live system
# - includes.chroot: files copied into the live root filesystem
# - includes.binary: files copied into the bootable media itself
# We generate that tree from scratch for each run so the build is
# reproducible and there is no stale state from a previous attempt.

readonly ISO_NAME="debian-trixie-$(date +%Y.%m.%d)-amd64.iso"
readonly OUTPUT_DIR="${OUTPUT_DIR:-/output}"
readonly SSH_KEY_SOURCE="/root/.ssh/authorized_keys"
readonly BUILD_DIR="/build/live-build"
readonly DISTRIBUTION="trixie"
readonly HELP_MESSAGE="Usage: $0 [-t <type>] [-v] [-h]
  -t  The artifact type to build (iso, netboot)
  -v  Enable verbose mode
  -h  Display this help message"

type="iso"

function print_help() {
  echo "$HELP_MESSAGE"
}

function die() {
  echo "$*" >&2
  exit 1
}

function write_file() {
  local path=$1
  shift

  mkdir -p "$(dirname "$path")"
  cat > "$path"
}

function parse_cli_args() {
  while getopts ":t:hv" opt; do
    case $opt in
      t )
        type=$OPTARG
        ;;
      v )
        set -x
        ;;
      h )
        print_help
        exit 0
        ;;
      \? )
        die "Invalid option: -$OPTARG"$'\n'"$HELP_MESSAGE"
        ;;
      : )
        die "Option -$OPTARG requires an argument."$'\n'"$HELP_MESSAGE"
        ;;
    esac
  done

  shift $((OPTIND - 1))

  if (($# > 0)); then
    die "Unexpected positional arguments: $*"$'\n'"$HELP_MESSAGE"
  fi

  case "$type" in
    iso|netboot )
      ;;
    * )
      die "Invalid build type: $type"
      ;;
  esac
}

function prepare_workspace() {
  [[ -f "$SSH_KEY_SOURCE" ]] || die "Provisioning key not found: $SSH_KEY_SOURCE"

  echo ":: Preparing Debian Trixie live-build workspace"
  rm -rf "$BUILD_DIR"
  mkdir -p "$BUILD_DIR" "$OUTPUT_DIR"
  cd "$BUILD_DIR"

  # Each directory here feeds a different live-build stage:
  # - hooks/live: scripts live-build runs during image creation
  # - includes.chroot: files copied into the live rootfs
  # - package-lists: newline-separated package manifests
  mkdir -p \
    config/hooks/live \
    config/includes.chroot/etc/ssh/sshd_config.d \
    config/includes.chroot/etc/systemd/system/multi-user.target.wants \
    config/includes.chroot/root/.ssh \
    config/includes.chroot/var/lib \
    config/package-lists
}

function configure_live_environment() {
  echo ":: Configuring the Debian Trixie live environment"

  # includes.chroot/root/.ssh becomes /root/.ssh in the booted live system.
  # Preloading authorized_keys gives us passwordless root SSH access.
  cp "$SSH_KEY_SOURCE" config/includes.chroot/root/.ssh/authorized_keys
  chmod 700 config/includes.chroot/root/.ssh
  chmod 600 config/includes.chroot/root/.ssh/authorized_keys
  touch config/includes.chroot/var/lib/is_live_env

  # package-lists/*.list.chroot are merged into the package set installed
  # inside the live filesystem. python-is-python3 keeps `/usr/bin/python`
  # available for tooling that still expects it.
  write_file config/package-lists/base.list.chroot <<'EOF'
openssh-server
python3
python-is-python3
systemd-timesyncd
dosfstools
parted
debootstrap
arch-install-scripts
locales
EOF

  # As with Arch, we prefer a small drop-in file over replacing the full
  # sshd_config shipped by the base system.
  write_file config/includes.chroot/etc/ssh/sshd_config.d/99-live.conf <<'EOF'
PasswordAuthentication no
PermitRootLogin prohibit-password
MaxAuthTries 5
EOF

  # We intentionally generate host keys on first boot so every booted
  # machine gets unique SSH host identity instead of sharing one baked into
  # the image.
  write_file config/includes.chroot/etc/systemd/system/generate-ssh-host-keys.service <<'EOF'
[Unit]
Description=Generate SSH host keys on first boot
Before=ssh.service
ConditionPathExists=!/etc/ssh/ssh_host_ed25519_key

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A

[Install]
WantedBy=multi-user.target
EOF

  # In an image build, "enabled" services are just the symlinks systemd
  # expects under the wants/ directory.
  ln -sf /etc/systemd/system/generate-ssh-host-keys.service \
    config/includes.chroot/etc/systemd/system/multi-user.target.wants/generate-ssh-host-keys.service
  ln -sf /lib/systemd/system/ssh.service \
    config/includes.chroot/etc/systemd/system/multi-user.target.wants/ssh.service

  if [[ "$type" == "netboot" ]]; then
    # includes.binary affects the boot medium contents rather than the live
    # root filesystem. For netboot we provide a tiny GRUB menu that points
    # to the live kernel and initrd that live-build emits.
    write_file config/includes.binary/boot/grub/grub.cfg <<'EOF'
set timeout=5
set default=0

menuentry "Debian Trixie Live" {
    linux /live/vmlinuz boot=live components quiet splash
    initrd /live/initrd.img
}
EOF
  fi
}

function build_image() {
  echo ":: Configuring live-build"
  lb clean --purge >/dev/null 2>&1 || true

  # `--binary-images` selects which artifact family to emit.
  # - iso: a bootable ISO image
  # - netboot: a PXE/TFTP tree under tftpboot/ plus the live squashfs
  #
  # The archive areas include firmware and other packages Debian keeps
  # outside of plain "main", which is often useful on installer/live media.
  lb config \
    --mode debian \
    --distribution "$DISTRIBUTION" \
    --architecture amd64 \
    --binary-images "$type" \
    --archive-areas "main contrib non-free non-free-firmware"

  echo ":: Building Debian Trixie ${type}"
  lb build
}

function move_outputs() {
  case "$type" in
    iso )
      local iso_path
      iso_path=$(find . -maxdepth 1 -type f -name '*.iso' | head -n 1)
      [[ -n "$iso_path" ]] || die "Failed to locate generated ISO artifact"

      mv "$iso_path" "${OUTPUT_DIR}/${ISO_NAME}"
      echo "=> ISO is available at ${OUTPUT_DIR}/${ISO_NAME}"
      ;;
    netboot )
      # For Debian netboot we serve the kernel and initrd directly and the
      # live root filesystem as filesystem.squashfs, which your iPXE config
      # later fetches via `fetch=...`.
      mv tftpboot/live/vmlinuz "${OUTPUT_DIR}/vmlinuz"
      mv tftpboot/live/initrd.img "${OUTPUT_DIR}/initrd.img"
      mv binary/live/filesystem.squashfs "${OUTPUT_DIR}/filesystem.squashfs"
      echo "=> Netboot files are available at ${OUTPUT_DIR}"
      ;;
  esac
}

function main() {
  parse_cli_args "$@"

  cat <<EOF
##################################
Debian Trixie ISO Builder
  Type: $type
  Distribution: $DISTRIBUTION
##################################
EOF

  prepare_workspace
  configure_live_environment
  build_image
  move_outputs

  echo ":: Debian Trixie build completed successfully."
}

main "$@"
