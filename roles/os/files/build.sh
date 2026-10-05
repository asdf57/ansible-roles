#!/usr/bin/env bash

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage: build.sh -d <arch|debian_trixie> -o <output-dir> -c <public-ca-bundle> [-- builder-options]
EOF
}

die() {
  echo "$*" >&2
  exit 1
}

distro=""
output_dir=""
ca_bundle=""

while (($# > 0)); do
  case "$1" in
    -d) distro=${2:-}; shift 2 ;;
    -o) output_dir=${2:-}; shift 2 ;;
    -c) ca_bundle=${2:-}; shift 2 ;;
    -h) usage; exit 0 ;;
    --) shift; break ;;
    *) die "Unknown option: $1" ;;
  esac
done

builder_args=("$@")

[[ -n "$distro" ]] || die "-d is required"
[[ -n "$output_dir" ]] || die "-o is required"
[[ -f "$ca_bundle" ]] || die "Public CA bundle not found: $ca_bundle"

case "$distro" in
  arch) builder="$SCRIPT_DIR/arch/build.sh" ;;
  debian_trixie) builder="$SCRIPT_DIR/debian-trixie/build.sh" ;;
  *) die "Unsupported distribution: $distro" ;;
esac

for input_name in HOMELABD_BINARY_SOURCE HOMELABD_SERVICE_SOURCE HOMELABD_SSHD_CONFIG_SOURCE HOMELABD_MANAGEMENT_INSTALL_SOURCE; do
  [[ -f "${!input_name:-}" ]] || die "$input_name must name an existing file"
done

mkdir -p "$output_dir"
output_dir="$(realpath "$output_dir")"
ca_bundle="$(realpath "$ca_bundle")"

if [[ "$(id -u)" -eq 0 ]]; then
  privilege=()
else
  privilege=(sudo)
fi

"${privilege[@]}" env \
  OUTPUT_DIR="$output_dir" \
  SSH_CA_BUNDLE_SOURCE="$ca_bundle" \
  HOMELABD_MANAGEMENT_INSTALL_SOURCE="$(realpath "$HOMELABD_MANAGEMENT_INSTALL_SOURCE")" \
  IMAGE_BOOT_MODE="${IMAGE_BOOT_MODE:-uefi}" \
  HOMELABD_BINARY_SOURCE="$(realpath "$HOMELABD_BINARY_SOURCE")" \
  HOMELABD_SERVICE_SOURCE="$(realpath "$HOMELABD_SERVICE_SOURCE")" \
  HOMELABD_SSHD_CONFIG_SOURCE="$(realpath "$HOMELABD_SSHD_CONFIG_SOURCE")" \
  HOMELAB_API_ENDPOINT="${HOMELAB_API_ENDPOINT:-https://stigmergy.ryuugu.dev}" \
  ISO_VERSION="${ISO_VERSION:-$(date +%Y.%m.%d)}" \
  bash "$builder" "${builder_args[@]}"
