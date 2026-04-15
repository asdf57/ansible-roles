# initrd/kernel and ISO building

Local builds live in this directory.

Usage:

```sh
cd roles/os/files
make help
make build-arch-releng-iso PROV_KEY="$HOME/.ssh/id_ed25519.pub"
make build-arch-baseline-netboot PROV_KEY="$HOME/.ssh/id_ed25519.pub"
make build-debian-trixie-iso PROV_KEY="$HOME/.ssh/id_ed25519.pub"
make build-debian-trixie-netboot PROV_KEY="$HOME/.ssh/id_ed25519.pub"
make clean
```

You can also call the wrapper directly:

```sh
./build.sh -d arch -o out/arch_releng_iso -p "$HOME/.ssh/id_ed25519.pub" -- -p releng -t iso
./build.sh -d debian_trixie -o out/debian_trixie_netboot -p "$HOME/.ssh/id_ed25519.pub" -- -t netboot
```

`--` matters when forwarding distro-specific flags.
