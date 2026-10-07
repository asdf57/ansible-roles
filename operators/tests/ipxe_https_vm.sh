#!/usr/bin/env bash
set -euo pipefail
# Network-only smoke test. No managed-node actions or host disks/credentials.
fixture_dir=$(cd "$(dirname "$0")/fixtures" && pwd)
test_root=$(mktemp -d /tmp/homelab-ipxe-https.XXXXXX)
mkdir -p "$test_root/EFI/BOOT"
docker run --rm --volume "$fixture_dir:/fixtures:ro" --volume "$test_root:/output" \
    --entrypoint /bin/sh homelab-ipxe-builder -ec '
    cp /fixtures/ipxe-console.h /build/src/config/local/console.h
    make -C /build/src -j2 bin-x86_64-efi/ipxe.efi EMBED=/fixtures/ipxe-https.ipxe \
      TRUST=/etc/ssl/certs/ISRG_Root_X1.pem,/etc/ssl/certs/USERTrust_ECC_Certification_Authority.pem,/etc/ssl/certs/USERTrust_RSA_Certification_Authority.pem \
      CERT=/etc/ssl/certs/ISRG_Root_X1.pem,/etc/ssl/certs/USERTrust_ECC_Certification_Authority.pem,/etc/ssl/certs/USERTrust_RSA_Certification_Authority.pem
    install -m 0644 /build/src/bin-x86_64-efi/ipxe.efi /output/EFI/BOOT/BOOTX64.EFI
    '
rc=0
timeout 90 qemu-system-x86_64 -machine q35,accel=tcg -m 256 -display none -monitor none \
    -bios "${OVMF_CODE:-/usr/share/edk2/x64/OVMF.4m.fd}" \
    -drive "format=raw,file=fat:rw:$test_root" \
    -netdev user,id=net,net=172.30.90.0/24 -device e1000,netdev=net \
    -serial "file:$test_root/serial.log" || rc=$?
[[ $rc == 0 || $rc == 124 ]]
rg -q 'IPXE_HTTPS_RESULT=passed' "$test_root/serial.log"
printf 'PASS: UEFI iPXE validated actual API and artifact-server HTTPS chains. Log: %s\n' "$test_root/serial.log"
