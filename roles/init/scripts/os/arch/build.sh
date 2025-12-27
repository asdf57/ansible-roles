#!/bin/bash

set -euo pipefail

readonly ISO_NAME="archlinux-$(date +%Y.%m.%d)-x86_64.iso"
readonly OUTPUT_DIR="/output"
readonly SSH_KEY_SOURCE="/root/.ssh/authorized_keys"
readonly HELP_MESSAGE="Usage: $0 -p <profile> -t <type> [-h]
  -p The profile to build. Valid values are:
        releng, baseline
  -t The type of build to perform. Valid values are:
        iso, netboot
  -h  Display this help message"

type="iso"
profile="releng"

# 1. Extract the parmaters from the command line
function parse_cli_args() {
  # If parameters supplied via environment variable, use those
  if [[ -n "$PARAMS" ]]; then
    set -- $PARAMS
  fi

  while getopts "p:t:hv" opt; do
    case ${opt} in
      p )
        profile=$OPTARG
        ;;
      t )
        type=$OPTARG
        ;;
      h )
        echo "$HELP_MESSAGE"
        exit 0
        ;;
      v )
        set -x
        ;;
      \? )
        echo "Invalid option: $OPTARG" >&2
        echo "$HELP_MESSAGE" >&2
        exit 1
        ;;
      : )
        echo "Option $OPTARG requires an argument." >&2
        echo "$HELP_MESSAGE" >&2
        exit 1
        ;;
    esac
  done

  shift $((OPTIND -1))
}

parse_cli_args

ssh_key_dest="configs/$profile/airootfs/root/.ssh/authorized_keys"

cat <<EOF
##################################
Arch Linux ISO Builder (v0.1.0)
  Profile: $profile
  Type: $type
##################################
EOF

echo ":: Installing required packages for building the Arch Linux ISO"
pacman -Syu --needed --noconfirm archiso arch-install-scripts bash dosfstools e2fsprogs erofs-utils gnupg grub jq libarchive libisoburn mtools openssl python-docutils squashfs-tools zsync

echo ":: Creating output directory for the ISO at ${OUTPUT_DIR}"
mkdir -p ${OUTPUT_DIR}

echo ":: Navigating to base archiso directory"
cd /usr/share/archiso

echo "::Adding custom packages to the live environment"

echo "=> Adding openssh to the packages.x86_64 file (installs it in the live environment)"
echo "openssh" >> configs/$profile/packages.x86_64

echo ":: Creating the .ssh directory in the live environment for the root user"
mkdir -p configs/$profile/airootfs/root/.ssh

echo ":: Copying the provisioning SSH key to the live environment"
cp ${SSH_KEY_SOURCE} ${ssh_key_dest}
chmod 600 ${ssh_key_dest}
chmod 700 configs/$profile/airootfs/root/.ssh

echo ":: Configuring the SSH daemon in the live environment"

echo "=> Disabling SSH password authentication"
echo "PasswordAuthentication no" >> \
   configs/$profile/airootfs/etc/ssh/sshd_config
echo "=> Setting the maximum number of authentication attempts to 90"
echo "MaxAuthTries 90" >> \
    configs/$profile/airootfs/etc/ssh/sshd_config

echo ":: Creating profile.d directory in the live environment"
mkdir -p configs/$profile/airootfs/etc/profile.d

echo "=> Creating a profile.d script to start the SSH daemon on login"
cat << 'EOF' > \
    configs/$profile/airootfs/etc/profile.d/start_sshd.sh
#!/bin/bash
if ! systemctl is-active --quiet sshd; then
    systemctl start sshd
fi
EOF

echo "=> Making the start_sshd.sh login script executable"
chmod +x configs/$profile/airootfs/etc/profile.d/start_sshd.sh

echo ":: Set /var/lib/is_live_env file to indicate live environment"
cat << 'EOF' > configs/$profile/airootfs/etc/profile.d/set_live_env.sh
#!/bin/bash
touch /var/lib/is_live_env
EOF
chmod +x configs/$profile/airootfs/etc/profile.d/set_live_env.sh

echo ":: Enabling the dhcpcd service in the live environment"
ln -sf /usr/lib/systemd/system/dhcpcd.service \
    configs/$profile/airootfs/etc/systemd/system/multi-user.target.wants/dhcpcd.service


# Build the Arch ISO
echo ":: Building the Arch Linux ISO"
mkdir out
mkarchiso -v -m "$type" -o out configs/"$profile"

if [[ $type == "iso" ]]; then
  echo ":: Moving the ISO to the output directory"
  mv out/*.iso ${OUTPUT_DIR}/${ISO_NAME}
  echo "=> ISO is available at ${OUTPUT_DIR}/${ISO_NAME}"
elif [[ $type == "netboot" ]]; then
  echo ":: Moving the netboot files to the output directory"
  mv out/arch/boot/x86_64/initramfs-linux.img "${OUTPUT_DIR}/initrd.img"
  mv out/arch/boot/x86_64/vmlinuz-linux "${OUTPUT_DIR}/vmlinuz"
  
  # Create proper directory structure on output
  mkdir -p "${OUTPUT_DIR}/arch/x86_64"
  
  # Copy the squashfs image
  mv out/arch/x86_64/airootfs.sfs "${OUTPUT_DIR}/arch/x86_64/"
  
  echo "=> Netboot files are available at ${OUTPUT_DIR}"
fi

echo ":: Arch Linux build completed successfully."
