#!/usr/bin/env bash
# Synthetic filesystems only; no node access or enrollment credentials.
set -euo pipefail
[[ -f /.dockerenv && $EUID == 0 ]] || exit 1
apt-get update -qq
apt-get install -y --no-install-recommends squashfs-tools xorriso
fixture=$(mktemp -d)
mkdir -p "$fixture/root/usr/share/homelabd/setup" "$fixture/netboot" "$fixture/iso" "$fixture/media/live"
for directory in agent management systemd sshd; do
    cp -r "/assets/$directory" "$fixture/root/usr/share/homelabd/setup/"
done
mksquashfs "$fixture/root" "$fixture/netboot/filesystem.squashfs" -noappend -processors 1 >/dev/null
bash /source/roles/os/files/ci/verify-setup-bundle.sh netboot "$fixture/netboot" /assets
cp "$fixture/netboot/filesystem.squashfs" "$fixture/media/live/"
xorriso -as mkisofs -o "$fixture/iso/test.iso" "$fixture/media" >/dev/null 2>&1
bash /source/roles/os/files/ci/verify-setup-bundle.sh iso "$fixture/iso" /assets
rm "$fixture/root/usr/share/homelabd/setup/agent/configure-debian-lldp.sh"
mksquashfs "$fixture/root" "$fixture/netboot/filesystem.squashfs" -noappend -processors 1 >/dev/null
if bash /source/roles/os/files/ci/verify-setup-bundle.sh netboot "$fixture/netboot" /assets; then
    echo 'Incomplete image was accepted' >&2; exit 1
fi
echo 'PASS: actual ISO/PXE bundle checks accept complete and reject incomplete images'
