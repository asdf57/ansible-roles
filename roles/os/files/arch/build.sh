#!/bin/bash

set -euo pipefail

# This script builds a customized Arch Linux ISO or PXE/netboot tree.
#
# Arch ships "profiles" as directories under /usr/share/archiso/configs.
# A profile is the recipe mkarchiso uses to build the live environment:
# package lists, filesystem overlays, boot config, and hooks. We copy one
# of those stock profiles into a temporary workspace and layer our own
# changes on top rather than mutating the system copy in-place.

readonly ISO_NAME="archlinux-$(date +%Y.%m.%d)-x86_64.iso"
readonly OUTPUT_DIR="${OUTPUT_DIR:-/output}"
readonly SSH_KEY_SOURCE="${SSH_KEY_SOURCE:-/root/.ssh/authorized_keys}"
readonly ARCHISO_ROOT="/usr/share/archiso/configs"
readonly HELP_MESSAGE="Usage: $0 [-p <profile>] [-t <type>] [-v] [-h]
  -p  The profile to build (releng, baseline)
  -t  The artifact type to build (iso, netboot)
  -v  Enable verbose mode
  -h  Display this help message"

profile="releng"
type="iso"
work_dir=""
profile_dir=""

function print_help() {
  echo "$HELP_MESSAGE"
}

function die() {
  echo "$*" >&2
  exit 1
}

function contains() {
  local needle=$1
  shift

  local item
  for item in "$@"; do
    if [[ "$item" == "$needle" ]]; then
      return 0
    fi
  done

  return 1
}

function append_if_missing() {
  local line=$1
  local file=$2

  if ! grep -qxF "$line" "$file"; then
    echo "$line" >> "$file"
  fi
}

function cleanup() {
  if [[ -n "$work_dir" && -d "$work_dir" ]]; then
    rm -rf "$work_dir"
  fi
}

function parse_cli_args() {
  while getopts ":p:t:hv" opt; do
    case ${opt} in
      p )
        profile=$OPTARG
        ;;
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

  contains "$profile" releng baseline || die "Invalid profile: $profile"
  contains "$type" iso netboot || die "Invalid build type: $type"
}

function prepare_workspace() {
  local source_profile_dir="${ARCHISO_ROOT}/${profile}"

  [[ -d "$source_profile_dir" ]] || die "Archiso profile not found: $source_profile_dir"
  [[ -f "$SSH_KEY_SOURCE" ]] || die "Provisioning key not found: $SSH_KEY_SOURCE"

  echo ":: Preparing Archiso workspace"
  mkdir -p "$OUTPUT_DIR"

  work_dir=$(mktemp -d)
  profile_dir="${work_dir}/${profile}"

  # The copied profile becomes our writable build recipe for this run.
  # Anything under airootfs is overlaid directly into the live rootfs.
  cp -a "$source_profile_dir" "$profile_dir"
}

function configure_live_environment() {
  local root_fs="${profile_dir}/airootfs"
  local ssh_dir="${root_fs}/root/.ssh"
  local ssh_config_dir="${root_fs}/etc/ssh/sshd_config.d"
  local systemd_dir="${root_fs}/etc/systemd/system"
  local wants_dir="${systemd_dir}/multi-user.target.wants"

  echo ":: Configuring the Arch live environment"

  # packages.x86_64 is the package manifest for the live image. Appending
  # openssh here ensures sshd exists inside the final booted environment.
  append_if_missing "openssh" "${profile_dir}/packages.x86_64"
  # dnsmasq can pull in a virtual dependency on libxtables. Pinning iptables
  # avoids an interactive provider choice between iptables and iptables-legacy.
  append_if_missing "iptables" "${profile_dir}/packages.x86_64"

  # airootfs/root/.ssh becomes /root/.ssh in the live image at boot time.
  # We pre-seed authorized_keys so the live root account accepts our key.
  mkdir -p "$ssh_dir" "$ssh_config_dir" "$wants_dir" "${root_fs}/var/lib"
  cp "$SSH_KEY_SOURCE" "${ssh_dir}/authorized_keys"
  chmod 700 "$ssh_dir"
  chmod 600 "${ssh_dir}/authorized_keys"

  # sshd_config.d is preferred over editing the base sshd_config directly
  # because it keeps our changes small and clearly isolated.
  cat > "${ssh_config_dir}/99-live.conf" <<'EOF'
PasswordAuthentication no
PermitRootLogin prohibit-password
MaxAuthTries 5
EOF

  # Live media should not ship one static host key to every machine.
  # Instead, we install a oneshot service that generates host keys on the
  # first boot before sshd starts.
  cat > "${systemd_dir}/generate-ssh-host-keys.service" <<'EOF'
[Unit]
Description=Generate SSH host keys on first boot
Before=sshd.service
ConditionPathExists=!/etc/ssh/ssh_host_ed25519_key

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A

[Install]
WantedBy=multi-user.target
EOF

  # This marker is consumed elsewhere in your stack to tell a live image
  # apart from an installed system.
  touch "${root_fs}/var/lib/is_live_env"

  # Enabling a service in a systemd image build usually means creating the
  # symlink that would exist under multi-user.target.wants after
  # `systemctl enable`. That is what these links are doing.
  ln -sf /usr/lib/systemd/system/dhcpcd.service "${wants_dir}/dhcpcd.service"
  ln -sf /usr/lib/systemd/system/sshd.service "${wants_dir}/sshd.service"
  ln -sf /etc/systemd/system/generate-ssh-host-keys.service "${wants_dir}/generate-ssh-host-keys.service"
}

function move_outputs() {
  local build_output_dir="${work_dir}/out"

  case "$type" in
    iso )
      local iso_path
      iso_path=$(find "$build_output_dir" -maxdepth 1 -type f -name '*.iso' | head -n 1)
      [[ -n "$iso_path" ]] || die "Failed to locate generated ISO artifact"

      echo ":: Moving the ISO to ${OUTPUT_DIR}"
      mv "$iso_path" "${OUTPUT_DIR}/${ISO_NAME}"
      echo "=> ISO is available at ${OUTPUT_DIR}/${ISO_NAME}"
      ;;
    netboot )
      # Arch netboot wants the kernel/initramfs at the top level and the
      # compressed live filesystem under arch/x86_64 for archiso_http_srv.
      echo ":: Moving netboot artifacts to ${OUTPUT_DIR}"
      mkdir -p "${OUTPUT_DIR}/arch/x86_64"
      mv "${build_output_dir}/arch/boot/x86_64/vmlinuz-linux" "${OUTPUT_DIR}/vmlinuz"
      mv "${build_output_dir}/arch/boot/x86_64/initramfs-linux.img" "${OUTPUT_DIR}/initrd.img"
      mv "${build_output_dir}/arch/x86_64/airootfs.sfs" "${OUTPUT_DIR}/arch/x86_64/airootfs.sfs"
      echo "=> Netboot files are available at ${OUTPUT_DIR}"
      ;;
  esac
}

function build_image() {
  local build_output_dir="${work_dir}/out"
  local work_cache_dir="${work_dir}/work"
  local attempt
  local max_attempts=3

  echo ":: Building Arch Linux ${type}"
  mkdir -p "$build_output_dir"
  mkdir -p "$work_cache_dir"

  # mkarchiso writes lots of scratch state while assembling the image.
  # Keeping a dedicated work directory makes cleanup deterministic and
  # avoids polluting the output directory with transient build artifacts.
  for attempt in $(seq 1 "$max_attempts"); do
    if mkarchiso -v -m "$type" -w "$work_cache_dir" -o "$build_output_dir" "$profile_dir"; then
      move_outputs
      return
    fi

    if [[ "$attempt" -lt "$max_attempts" ]]; then
      echo "=> mkarchiso failed on attempt ${attempt}/${max_attempts}; retrying in 10 seconds"
      sleep 10
    fi
  done

  die "mkarchiso failed after ${max_attempts} attempts"
}

function main() {
  trap cleanup EXIT
  parse_cli_args "$@"

  cat <<EOF
##################################
Arch Linux ISO Builder
  Profile: $profile
  Type: $type
##################################
EOF

  prepare_workspace
  configure_live_environment
  build_image

  echo ":: Arch Linux build completed successfully."
}

main "$@"
