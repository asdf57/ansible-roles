#!/bin/bash
set -euo pipefail

# Constants
ISO_NAME="debian-$(date +%Y.%m.%d)-amd64.iso"
OUTPUT_DIR="/output"
ISO_DIR="live-default"
SSH_KEY_SOURCE="/root/.ssh/authorized_keys"
HELP="Usage: $0 -p <profile> -t <type> [-h]
  -p Profile to build (default, custom)
  -t Build type (iso, iso-hybrid, netboot, tar, hdd)
  -d Distribution to build (buster, bullseye, bookworm, sid)
  -h Show this help message"

# Default values
type="iso"
profile="default"
distribution="bookworm"

# Override arguments with PARAMS environment variable if set
[[ -n "${PARAMS:-}" ]] && set -- $PARAMS

# Parse command-line arguments
while getopts "p:t:d:hv" opt; do
  case $opt in
    p) profile="$OPTARG" ;;
    t) type="$OPTARG" ;;
    d) distribution="$OPTARG" ;;
    h) echo "$HELP"; exit 0 ;;
    v) set -x ;;
    *) echo "$HELP" >&2; exit 1 ;;
  esac
done
shift $((OPTIND -1))

# Display build information
cat <<EOF
##################################
Debian ISO Builder (v0.1.0)
  Profile: $profile
  Type: $type
  Distribution: $distribution
##################################
EOF

# Install necessary packages
echo ":: Installing required packages for building the Debian ISO"
apt-get update && apt-get install -y live-build debootstrap syslinux isolinux

# Create necessary directories in one command
echo ":: Setting up output and configuration directories"

mkdir -p "$ISO_DIR"
cd "$ISO_DIR"

mkdir -p "$OUTPUT_DIR" \
         config/includes.chroot/root/.ssh \
         config/package-lists \
         config/includes.chroot/etc/ssh \
         config/hooks/normal \
         config/includes.binary/boot/grub/

# Copy SSH keys and set permissions
echo ":: Configuring SSH access in the live environment"
cp "$SSH_KEY_SOURCE" config/includes.chroot/root/.ssh/authorized_keys
chmod 600 config/includes.chroot/root/.ssh/authorized_keys
chmod 700 config/includes.chroot/root/.ssh

# Add to the live environment packages
echo "openssh-server" >> config/package-lists/ssh.list.chroot
echo "python3" >> config/package-lists/python.list.chroot
echo "python3-minimal" >> config/package-lists/python.list.chroot
echo "systemd-timesyncd" >> config/package-lists/timesyncd.list.chroot
echo "dosfstools" >> config/package-lists/dosfstools.list.chroot
echo "parted" >> config/package-lists/parted.list.chroot
echo "debootstrap" >> config/package-lists/debootstrap.list.chroot
echo "arch-install-scripts" >> config/package-lists/arch-install-scripts.list.chroot
echo "locales" >> config/package-lists/locales.list.chroot

# Create a hook to ensure Python symlinks are correct
echo ":: Creating hook to set up Python symlinks"
cat << 'EOF' > config/hooks/normal/python-symlink.hook.chroot
#!/usr/bin/env bash
# Ensure /usr/bin/python3 points to the system Python
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 1
update-alternatives --set python3 /usr/bin/python3.13
ln -sf /usr/bin/python3 /usr/bin/python
EOF
chmod +x config/hooks/normal/python-symlink.hook.chroot

# Configure SSH daemon settingshttps://news.ycombinator.com
echo ":: Configuring SSH daemon in the live environment"
cat <<EOF > config/includes.chroot/etc/ssh/sshd_config
PasswordAuthentication no
MaxAuthTries 5
EOF

echo ":: Configuring GRUB for unattended boot"
cat <<EOF > config/includes.binary/boot/grub/grub.cfg
set timeout=5
set default=0
menuentry "Debian Live" {
    linux /live/vmlinuz boot=live components quiet splash
    initrd /live/initrd.img
}
EOF

# Create a hook to generate SSH host keys and enable SSH service
echo ":: Creating hook to generate SSH host keys and enable SSH"
cat << 'EOF' > config/hooks/normal/openssh-setup.hook.chroot
#!/bin/bash
ssh-keygen -A
systemctl enable ssh
EOF
chmod +x config/hooks/normal/openssh-setup.hook.chroot

echo ":: Setting system-wide environment variables"
mkdir -p config/includes.chroot/etc
cat << 'EOF' >> config/includes.chroot/etc/environment
IS_LIVE_ENV=0
EOF

# Configure live-build and build the ISO
echo ":: Configuring live-build and starting the build process"
lb config -b "$type" --distribution "$distribution" --architecture "amd64"

lb build

# Move the built ISO to the output directory
echo ":: Moving the built ISO to the output directory"

if [[ $type == "iso" ]]; then
  ISO_FILE=$(ls *.iso | head -n1)
  mv "$ISO_FILE" "$OUTPUT_DIR/$ISO_NAME"
  echo "=> ISO is available at $OUTPUT_DIR/$ISO_NAME"
elif [[ $type == "iso-hybrid" ]]; then
  echo todo
elif [[ $type == "netboot" ]]; then
  mv "tftpboot/live/vmlinuz" "$OUTPUT_DIR/vmlinuz"
  mv "tftpboot/live/initrd.img" "$OUTPUT_DIR/initrd.img"
  mv "binary/live/filesystem.squashfs" "$OUTPUT_DIR/filesystem.squashfs"
  echo "=> Netboot files are available at $OUTPUT_DIR"
elif [[ $type == "tar" ]]; then
  echo todo
elif [[ $type == "hdd" ]]; then
  echo todo
fi

# Completion message
echo ":: Debian ISO build completed successfully."

