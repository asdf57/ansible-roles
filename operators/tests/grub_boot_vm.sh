#!/usr/bin/env bash
set -euo pipefail
# Only fresh regular image files under this private temporary directory are
# formatted. No host devices, credentials, API calls or network are involved.
for tool in grub-mkstandalone grub-editenv grub-reboot grub-script-check mkfs.ext4 mkfs.vfat mmd mcopy qemu-system-x86_64; do
    command -v "$tool" >/dev/null
done
test_root=$(mktemp -d /tmp/homelab-grub-vm.XXXXXX)
firmware_code=${OVMF_CODE:-/usr/share/edk2/x64/OVMF_CODE.4m.fd}
firmware_vars=${OVMF_VARS:-/usr/share/edk2/x64/OVMF_VARS.4m.fd}
mkdir -p "$test_root/root/boot/grub"
cat > "$test_root/root/boot/grub/grub.cfg" <<'GRUB'
serial --unit=0 --speed=115200
terminal_output serial
set timeout=0
set default=0
load_env
if [ "${next_entry}" ]; then
    set default="${next_entry}"
    set next_entry=
    save_env next_entry
fi
menuentry 'Installed OS fixture' --id installed {
    echo BOOT_RESULT=installed
    halt
}
menuentry 'Homelab netboot fixture' --id homelab-netboot {
    echo BOOT_RESULT=netboot
    halt
}
GRUB
cat > "$test_root/bootstrap.cfg" <<'GRUB'
search --label --set=root HLABROOT
set prefix=($root)/boot/grub
configfile ($root)/boot/grub/grub.cfg
GRUB
grub-script-check "$test_root/root/boot/grub/grub.cfg"
grub-editenv "$test_root/root/boot/grub/grubenv" create
grub-reboot --boot-directory="$test_root/root/boot" homelab-netboot
grub-editenv "$test_root/root/boot/grub/grubenv" list | rg -q '^next_entry=homelab-netboot$'
truncate -s 128M "$test_root/root.img"
mkfs.ext4 -q -F -L HLABROOT -d "$test_root/root" "$test_root/root.img"
grub-mkstandalone -O x86_64-efi --modules='configfile normal loadenv serial terminal halt ext2 search search_label test echo' \
    -o "$test_root/BOOTX64.EFI" "boot/grub/grub.cfg=$test_root/bootstrap.cfg"
truncate -s 64M "$test_root/efi.img"
mkfs.vfat "$test_root/efi.img" >/dev/null
mmd -i "$test_root/efi.img" ::/EFI ::/EFI/BOOT
mcopy -i "$test_root/efi.img" "$test_root/BOOTX64.EFI" ::/EFI/BOOT/BOOTX64.EFI
cp "$firmware_vars" "$test_root/vars.fd"
for boot in first second; do
    rc=0
    timeout 20 qemu-system-x86_64 -machine q35,accel=tcg -m 256 -display none -monitor none -nic none \
        -drive "if=pflash,format=raw,readonly=on,file=$firmware_code" \
        -drive "if=pflash,format=raw,file=$test_root/vars.fd" \
        -drive "if=virtio,format=raw,file=$test_root/efi.img" \
        -drive "if=virtio,format=raw,file=$test_root/root.img" \
        -serial "file:$test_root/$boot.log" || rc=$?
    [[ $rc == 0 || $rc == 124 ]]
done
rg -q 'BOOT_RESULT=netboot' "$test_root/first.log"
rg -q 'BOOT_RESULT=installed' "$test_root/second.log"
printf 'PASS: real UEFI GRUB selected netboot once, consumed grubenv, then defaulted to installed OS (network disabled). Logs: %s\n' "$test_root"
