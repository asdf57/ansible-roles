#!/bin/bash

# Builds an ISO for the specified linux distribution

set -euo pipefail

# Change to the directory of the script
cd "$(dirname "$0")"

help_message="Usage: $0 [-h] [-o <output_dir>] [-v] <distro> [options]
  Build an ISO for the specified distribution

  Positional arguments:
    <distro>  The distribution to build an ISO for

  Flags:
    -o  The host node's output directory to write the ISO into
    -v  Enable verbose mode
    -h  Display this help message"


SUPPORTED_DISTROS=("arch" "debian")

prov_key="/etc/ssh/provisioning_key"
distro=""
output_dir="out"
distro_flags=""

function parse_cli_args() {
  while getopts "o:p:vh" opt; do
    case ${opt} in
      o )
        output_dir=$OPTARG
        ;;
      v )
        set -x
        ;;
      h )
        echo "$help_message"
        exit 0
        ;;
      p )
        prov_key=$OPTARG
        ;;
      \? )
        echo "Invalid option: -$OPTARG" >&2
        echo "$help_message" >&2
        exit 1
        ;;
      : )
        echo "Option -$OPTARG requires an argument." >&2
        echo "$help_message" >&2
        exit 1
        ;;
    esac
  done

  # Remove the parsed options from the positional parameters
  shift $((OPTIND -1))

  distro="$1"
  if [[ -z "$distro" ]]; then
    echo "Error: distro is required." >&2
    echo "$help_message" >&2
    exit 1
  fi

  if [[ ! " ${SUPPORTED_DISTROS[@]} " =~ " $distro " ]]; then
    echo "Invalid distribution: $distro" >&2
    echo "$help_message" >&2
    exit 1
  fi

  shift

  # The remaining arguments are flags to pass to the distro build script
  distro_flags="$@"

  echo "=> Passing flags to distro build script: $distro_flags"
}

function build() {
  echo ":: Building ISO for $distro"

  # Check if the public SSH key for Ansible is present
  if [[ ! -f "$prov_key.pub" ]]; then
    echo "- Public SSH key for Ansible not found, will recreate!" >&2
    ssh-keygen -t ed25519 -f "$prov_key" -N ""
  fi

  echo "=> SSH key at $prov_key found!"
  echo "=> Creating temporary copy of SSH key to allow Docker to copy it"

  # Copy the SSH key to the current directory if it is not already there
  # This is needed for the Docker build context
  if [[ "$prov_key.pub" != "provisioning_key.pub)" ]]; then
    cp "$prov_key.pub" "provisioning_key.pub"
  else
    echo "=> SSH key already exists in build context!"
  fi

  # Set inheritable environment variables
  export OUTPUT_DIR="$output_dir"

  case $distro in
    arch )
      echo "$distro_flags"
      docker build --no-cache --platform linux/amd64 -t arch-builder -f Dockerfile.arch .
      docker run --rm --platform linux/amd64 --privileged -e PARAMS="$distro_flags" -v ${OUTPUT_DIR}:/output arch-builder
      ;;

    debian )
      docker build --no-cache --platform linux/amd64 -t debian-builder -f Dockerfile.debian .
      docker run --rm --platform linux/amd64 --privileged -e PARAMS="$distro_flags" -v ${OUTPUT_DIR}:/output debian-builder
      ;;
  esac

  echo ":: File built successfully!"
  echo "=> $distro ISO placed in $output_dir"
}

function cleanup() {
  echo ":: Running cleanup hook"

  # If our original SSH key is not in the current directory, remove the temporary copy
  # if [[ "$prov_key.pub" != "provisioning_key.pub" ]]; then
  #   echo "=> Removing temporary SSH key copy"
  #   rm -f "provisioning_key.pub"
  # fi
}

trap cleanup EXIT

parse_cli_args "$@"
build
