#!/usr/bin/env bash
set -euo pipefail
# Do not trace task credentials. Artifacts from this task must be private.
set +x
[[ "${HOMELABD_API_TOKEN:-}" =~ ^[a-f0-9]{64}$ ]] || { echo 'A restricted agent API token is required' >&2; exit 1; }
export HOMELABD_API_TOKEN
# Public desired inputs come from the snapshot; credentials arrive privately.
input="$PWD/image-inputs/${IMAGE_INPUT_PATH:?}/image.yaml"
bundle="$PWD/image-inputs/$IMAGE_INPUT_PATH/ssh-user-ca.pub"
if [[ -f /etc/arch-release ]]; then
    printf '\nDisableSandbox\n' >> /etc/pacman.conf
    pacman-key --init
    pacman-key --populate archlinux
    pacman -Syu --noconfirm
    pacman -S --needed --noconfirm arch-install-scripts archiso curl dosfstools e2fsprogs \
        erofs-utils git grub jq libarchive libisoburn mtools openssh python python-docutils squashfs-tools sudo zsync
else
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl debootstrap dosfstools git grub-pc-bin grub-efi-amd64-bin \
        isolinux jq live-build mtools openssh-client python3 squashfs-tools sudo syslinux tar wget xorriso
fi
[[ $(jq -r .architecture "$input") == amd64 ]] || { echo 'Unsupported architecture' >&2; exit 1; }
[[ $(jq -r .recipeVersion "$input") == 1 ]] || { echo 'Unsupported recipe version' >&2; exit 1; }
expected=$(jq -r .trustBundleDigest "$input")
actual="sha256:$(sha256sum "$bundle" | cut -d ' ' -f 1)"
[[ "$expected" == "$actual" ]] || { echo 'Public trust digest mismatch' >&2; exit 1; }
case "$(jq -r '.distribution + ":" + .version' "$input")" in
    arch:rolling) distro=arch ;;
    debian:trixie) distro=debian_trixie ;;
    *) echo 'Unsupported distribution/release' >&2; exit 1 ;;
esac
mkdir -p image-output/iso image-output/netboot
cp "$input" image-output/image.yaml
python3 - "$input" <<'PY'
import datetime, json, pathlib, subprocess, sys, uuid
configuration = pathlib.Path(sys.argv[1]).read_text()
metadata = json.loads(configuration)
metadata = {
    "isoUid": metadata["isoUid"], "trustBundleDigest": metadata["trustBundleDigest"],
    "inputContent": configuration,
    "inputRevision": subprocess.check_output(["git", "-C", "image-inputs", "rev-parse", "HEAD"], text=True).strip(),
    "sourceRevisions": {name: subprocess.check_output(["git", "-C", directory, "rev-parse", "HEAD"], text=True).strip()
                        for name, directory in [("builder", "builder"), ("homelabd", "homelabd")]},
    "buildId": str(uuid.uuid4()),
    "buildStartedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
pathlib.Path("image-output/build.json").write_text(json.dumps(metadata))
PY
export ISO_VERSION="$(jq -r .buildId image-output/build.json)"
export IMAGE_BOOT_MODE="$(jq -r .bootMode "$input")"
export HOMELABD_BINARY_SOURCE="$PWD/homelabd-bin/homelabd"
export HOMELABD_SERVICE_SOURCE="$PWD/homelabd/setup/systemd/homelabd.service"
export HOMELABD_SSHD_CONFIG_SOURCE="$PWD/homelabd/setup/sshd/10-homelabd.conf"
export HOMELABD_MANAGEMENT_INSTALL_SOURCE="$PWD/homelabd/setup/management/install.sh"
export HOMELAB_API_ENDPOINT="$(jq -r .apiEndpoint "$input")"
for type in iso netboot; do
    bash builder/roles/os/files/build.sh -d "$distro" -o "$PWD/image-output/$type" -c "$bundle" -- -t "$type"
done
