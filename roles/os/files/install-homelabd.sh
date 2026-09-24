#!/usr/bin/env bash

set -euo pipefail

if (($# != 5)); then
  echo "Usage: $0 <root-fs> <homelabd-binary> <service-file> <sshd-config> <api-endpoint>" >&2
  exit 1
fi

root_fs=$1
binary_source=$2
service_source=$3
sshd_config_source=$4
api_endpoint=$5

for source in "$binary_source" "$service_source" "$sshd_config_source"; do
  if [[ ! -f "$source" ]]; then
    echo "Required homelabd input not found: $source" >&2
    exit 1
  fi
done

install -D -m 0755 "$binary_source" "${root_fs}/usr/local/bin/homelabd"
install -D -m 0644 "$service_source" "${root_fs}/etc/systemd/system/homelabd.service"
install -D -m 0644 "$sshd_config_source" "${root_fs}/etc/ssh/sshd_config.d/10-homelabd.conf"

# The checked-in unit is portable; only the deployment endpoint varies by site.
sed -i \
  "s|^Environment=\"API_ENDPOINT=.*\"$|Environment=\"API_ENDPOINT=${api_endpoint}\"|" \
  "${root_fs}/etc/systemd/system/homelabd.service"

install -D -m 0644 /dev/stdin "${root_fs}/usr/lib/sysusers.d/homelabd.conf" <<'EOF'
u homelabd - "Homelab agent" /var/lib/homelab -
m homelabd lldpd
EOF

install -D -m 0644 /dev/stdin "${root_fs}/usr/lib/tmpfiles.d/homelabd.conf" <<'EOF'
d /var/lib/homelab 0755 root root -
d /var/lib/homelab/authorized-keys 0750 homelabd homelabd -
EOF

mkdir -p "${root_fs}/etc/systemd/system/multi-user.target.wants"
ln -sf /etc/systemd/system/homelabd.service \
  "${root_fs}/etc/systemd/system/multi-user.target.wants/homelabd.service"
