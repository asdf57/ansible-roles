#!/usr/bin/env bash
# Check actual ISO and PXE filesystems without reading enrollment credentials.
set -euo pipefail
[[ $# == 3 ]] || { echo 'Usage: verify-setup-bundle.sh <iso|netboot> <output> <setup-source>' >&2; exit 1; }
type=$1
output=$(realpath -e -- "$2")
setup=$(realpath -e -- "$3")
check_dir=$(mktemp -d)
trap 'rm -f -- "$check_dir/filesystem.squashfs"; rmdir "$check_dir"' EXIT
case "$type" in
    iso)
        mapfile -t images < <(find "$output" -maxdepth 1 -type f -name '*.iso')
        [[ ${#images[@]} == 1 ]] || { echo 'Expected one ISO artifact' >&2; exit 1; }
        # Debian uses /live, Arch uses /arch/x86_64.
        if ! xorriso -osirrox on -indev "${images[0]}" -extract /live/filesystem.squashfs "$check_dir/filesystem.squashfs" >/dev/null 2>&1; then
            xorriso -osirrox on -indev "${images[0]}" -extract /arch/x86_64/airootfs.sfs "$check_dir/filesystem.squashfs" >/dev/null 2>&1
        fi
        filesystem="$check_dir/filesystem.squashfs"
        ;;
    netboot)
        filesystem="$output/filesystem.squashfs"
        [[ -f "$filesystem" ]] || filesystem="$output/arch/x86_64/airootfs.sfs"
        ;;
    *) echo 'Unsupported artifact type' >&2; exit 1 ;;
esac
while IFS= read -r -d '' source; do
    relative=${source#"$setup/"}
    unsquashfs -cat "$filesystem" "usr/share/homelabd/setup/$relative" | cmp - "$source"
done < <(find "$setup/agent" "$setup/management" "$setup/systemd" "$setup/sshd" -type f -print0)
echo "Verified complete retained setup bundle in $type filesystem"
unsquashfs -cat "$filesystem" etc/NetworkManager/conf.d/90-homelab.conf | grep -qx 'dhcp=internal'
unsquashfs -cat "$filesystem" etc/NetworkManager/conf.d/90-homelab.conf | grep -qx 'keep-configuration=no'
unsquashfs -cat "$filesystem" etc/NetworkManager/system-connections/homelab-wired.nmconnection | grep -qx 'dhcp-client-id=mac'
unsquashfs -ll "$filesystem" etc/NetworkManager/system-connections/homelab-wired.nmconnection | grep -q '^-rw------- '
for unit in systemd-networkd.service systemd-networkd.socket systemd-networkd-wait-online.service \
    dhcpcd.service dhcpcd@.service dhclient.service dhclient@.service networking.service ifup@.service; do
    unsquashfs -ll "$filesystem" "etc/systemd/system/$unit" | grep -Fq -- ' -> /dev/null'
done
unsquashfs -ll "$filesystem" etc/systemd/system/multi-user.target.wants/NetworkManager.service | grep -Fq -- ' -> /usr/lib/systemd/system/NetworkManager.service'
echo "Verified NetworkManager-only networking in $type filesystem"
