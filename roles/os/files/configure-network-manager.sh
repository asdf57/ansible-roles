#!/bin/sh
# Configure an offline image/root; never restart the builder or the live host.
set -eu
[ "$#" = 1 ] || { echo 'Usage: configure-network-manager.sh <target-root>' >&2; exit 1; }
target=$(realpath -e -- "$1")
[ -d "$target/etc" ] || { echo 'Target root must contain /etc' >&2; exit 1; }
systemd="$target/etc/systemd/system"
install -d "$systemd/multi-user.target.wants" "$target/etc/NetworkManager/conf.d"
install -d -m 0700 "$target/etc/NetworkManager/system-connections"

# Stock Arch profiles and Debian package presets must not create another owner.
for unit in systemd-networkd.service systemd-networkd.socket systemd-networkd-wait-online.service \
    dhcpcd.service dhcpcd@.service dhclient.service dhclient@.service networking.service ifup@.service; do
    pattern=$(printf '%s' "$unit" | sed 's/@\./@*./')
    find "$systemd" -type l -name "$pattern" -delete
    ln -sfn /dev/null "$systemd/$unit"
done
ln -sfn /usr/lib/systemd/system/NetworkManager.service "$systemd/multi-user.target.wants/NetworkManager.service"

cat > "$target/etc/NetworkManager/conf.d/90-homelab.conf" <<'EOF'
[main]
plugins=keyfile
dhcp=internal
dns=default
rc-manager=symlink

[device-homelab-wired]
match-device=type:ethernet
keep-configuration=no
EOF

# A persistent profile lets NM replace, rather than assume, initramfs networking.
# Multi-connect permits independent wired NICs; it is not a node identity.
cat > "$target/etc/NetworkManager/system-connections/homelab-wired.nmconnection" <<'EOF'
[connection]
id=homelab-wired
uuid=fb66fd28-803a-4b21-b47a-32df841c2b56
type=ethernet
autoconnect=true
multi-connect=3

[ethernet]

[ipv4]
method=auto
dhcp-client-id=mac

[ipv6]
method=auto
EOF
chmod 0600 "$target/etc/NetworkManager/system-connections/homelab-wired.nmconnection"
ln -sfn /run/NetworkManager/resolv.conf "$target/etc/resolv.conf"
