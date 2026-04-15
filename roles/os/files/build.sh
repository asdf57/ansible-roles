#!/bin/bash

# Builds an ISO for the specified Linux distribution

set -euo pipefail

# Change to the directory of the script
cd "$(dirname "$0")"

help_message="Usage: $0 -d <distro> -o <output_dir> -p <public_key_path> [-v] [-- distro options...]
  Build an ISO for the specified distribution

  Flags:
    -d  The distribution to build an ISO for
    -o  The output directory to write the ISO into
    -p  The path to the public SSH key to use for provisioning
    -v  Enable verbose mode
    -h  Display this help message

  Notes:
    Use -- before distro-specific flags so they are forwarded to the distro builder"


SUPPORTED_DISTROS=("arch" "debian_trixie")

PROV_KEY=""
DISTRO=""
OUTPUT_DIR=""
DISTRO_ARGS=()
PROVISIONING_KEY_BACKUP=""
PROVISIONING_KEY_COPIED=0

function print_help() {
  echo "$help_message"
}

function require_supported_distro() {
  local supported_distro

  for supported_distro in "${SUPPORTED_DISTROS[@]}"; do
    if [[ "$DISTRO" == "$supported_distro" ]]; then
      return
    fi
  done

  echo "Invalid distribution: $DISTRO" >&2
  print_help >&2
  exit 1
}

function prepare_provisioning_key() {
  local build_context_key="provisioning_key.pub"

  if [[ -e "$build_context_key" ]]; then
    if [[ "$(realpath "$build_context_key")" == "$PROV_KEY" ]]; then
      echo "=> Provisioning key already exists in build context"
      return
    fi

    PROVISIONING_KEY_BACKUP=$(mktemp)
    cp "$build_context_key" "$PROVISIONING_KEY_BACKUP"
  fi

  echo "=> Creating temporary copy of SSH key to allow Docker to copy it"
  cp "$PROV_KEY" "$build_context_key"
  PROVISIONING_KEY_COPIED=1
}

function parse_cli_args() {
  while (($# > 0)); do
    case $1 in
      -d)
        if (($# < 2)); then
          echo "Option -d requires an argument." >&2
          print_help >&2
          exit 1
        fi
        DISTRO=$2
        shift 2
        ;;
      -o)
        if (($# < 2)); then
          echo "Option -o requires an argument." >&2
          print_help >&2
          exit 1
        fi
        OUTPUT_DIR=$2
        shift 2
        ;;
      -p)
        if (($# < 2)); then
          echo "Option -p requires an argument." >&2
          print_help >&2
          exit 1
        fi
        PROV_KEY=$2
        shift 2
        ;;
      -v)
        set -x
        shift
        ;;
      -h)
        print_help
        exit 0
        ;;
      --)
        shift
        break
        ;;
      *)
        break
        ;;
    esac
  done

  DISTRO_ARGS=("$@")

  if [[ -z "$DISTRO" ]] || [[ -z "$OUTPUT_DIR" ]] || [[ -z "$PROV_KEY" ]]; then
    echo "Missing required arguments" >&2
    print_help >&2
    exit 1
  fi

  if [[ ! -f "$PROV_KEY" ]]; then
    echo "Public SSH key for provisioning not found: $PROV_KEY" >&2
    exit 1
  fi

  require_supported_distro

  PROV_KEY=$(realpath "$PROV_KEY")

  if [[ ! -d "$OUTPUT_DIR" ]]; then
    echo "=> Output directory $OUTPUT_DIR does not exist, creating it!"
    mkdir -p "$OUTPUT_DIR"
  fi
  OUTPUT_DIR=$(realpath "$OUTPUT_DIR")

  if (( ${#DISTRO_ARGS[@]} > 0 )); then
    printf '=> Passing flags to distro build script:'
    printf ' %q' "${DISTRO_ARGS[@]}"
    printf '\n'
  fi
}

function build() {
  echo ":: Building ISO for $DISTRO"

  prepare_provisioning_key

  echo "=> Set output directory to $OUTPUT_DIR"

  case $DISTRO in
    arch )
      docker build --no-cache --platform linux/amd64 -t arch-builder -f arch/Dockerfile.arch .
      docker run --rm --platform linux/amd64 --privileged -v "${OUTPUT_DIR}:/output" arch-builder "${DISTRO_ARGS[@]}"
      ;;

    debian_trixie )
      docker build --no-cache --platform linux/amd64 -t debian-builder -f debian-trixie/Dockerfile.debian .
      docker run --rm --platform linux/amd64 --privileged -v "${OUTPUT_DIR}:/output" debian-builder "${DISTRO_ARGS[@]}"
      ;;
  esac

  echo ":: File built successfully!"
  echo "=> $DISTRO ISO placed in $OUTPUT_DIR"
}

function cleanup() {
  echo ":: Running cleanup hook"

  if [[ -n "$PROVISIONING_KEY_BACKUP" ]]; then
    echo "=> Restoring original provisioning key from backup"
    mv "$PROVISIONING_KEY_BACKUP" "provisioning_key.pub"
  elif (( PROVISIONING_KEY_COPIED == 1 )); then
    echo "=> Removing temporary SSH key copy"
    rm -f "provisioning_key.pub"
  fi
}

parse_cli_args "$@"
trap cleanup EXIT
build
