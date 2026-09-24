#!/usr/bin/env bash

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage: build.sh -d <arch|debian_trixie> -o <output-dir> -p <public-key> [-- builder-options]
EOF
}

die() {
  echo "$*" >&2
  exit 1
}

distro=""
output_dir=""
public_key=""

while (($# > 0)); do
  case "$1" in
    -d) distro=${2:-}; shift 2 ;;
    -o) output_dir=${2:-}; shift 2 ;;
    -p) public_key=${2:-}; shift 2 ;;
    -h) usage; exit 0 ;;
    --) shift; break ;;
    *) die "Unknown option: $1" ;;
  esac
done

builder_args=("$@")

[[ -n "$distro" ]] || die "-d is required"
[[ -n "$output_dir" ]] || die "-o is required"
[[ -f "$public_key" ]] || die "Provisioning key not found: $public_key"

case "$distro" in
  arch) builder="$SCRIPT_DIR/arch/build.sh" ;;
  debian_trixie) builder="$SCRIPT_DIR/debian-trixie/build.sh" ;;
  *) die "Unsupported distribution: $distro" ;;
esac

for input_name in HOMELABD_BINARY_SOURCE HOMELABD_SERVICE_SOURCE HOMELABD_SSHD_CONFIG_SOURCE; do
  [[ -f "${!input_name:-}" ]] || die "$input_name must name an existing file"
done

mkdir -p "$output_dir"
output_dir="$(realpath "$output_dir")"
public_key="$(realpath "$public_key")"

if [[ "$(id -u)" -eq 0 ]]; then
  privilege=()
else
  privilege=(sudo)
fi

"${privilege[@]}" env \
  OUTPUT_DIR="$output_dir" \
  SSH_KEY_SOURCE="$public_key" \
  HOMELABD_BINARY_SOURCE="$(realpath "$HOMELABD_BINARY_SOURCE")" \
  HOMELABD_SERVICE_SOURCE="$(realpath "$HOMELABD_SERVICE_SOURCE")" \
  HOMELABD_SSHD_CONFIG_SOURCE="$(realpath "$HOMELABD_SSHD_CONFIG_SOURCE")" \
  HOMELAB_API_ENDPOINT="${HOMELAB_API_ENDPOINT:-https://stigmergy.ryuugu.dev}" \
  ISO_VERSION="${ISO_VERSION:-$(date +%Y.%m.%d)}" \
  bash "$builder" "${builder_args[@]}"
