#!/usr/bin/env bash
set -euo pipefail
[[ $# == 5 ]] || { echo "Usage: $0 <root-fs> <binary> <service-file> <sshd-config> <api-endpoint>" >&2; exit 1; }
# The service source belongs to homelabd/setup/systemd; reuse its reviewed installer.
setup=$(cd -- "$(dirname -- "$3")/.." && pwd)
exec /bin/bash "$setup/agent/install.sh" "$1" "$2" "$5"
