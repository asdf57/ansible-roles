# ISO builders

Create an `ISO` resource rather than the obsolete `build-isos` Pipeline.
ISOController publishes public build inputs and creates an owned Concourse
pipeline. Versioned `ci/build-image.sh` builds the image and netboot tree;
`ci/publish.py` uploads immutable artifacts and publishes the manifest last.

Local builds use `build.sh -d arch -o /absolute/output -c /absolute/ssh-user-ca.pub`
(or `-d debian_trixie`). Supply `HOMELABD_BINARY_SOURCE`,
`HOMELABD_SERVICE_SOURCE`, `HOMELABD_SSHD_CONFIG_SOURCE`, and
`HOMELABD_MANAGEMENT_INSTALL_SOURCE` from the same homelabd source checkout.
`IMAGE_BOOT_MODE` is `uefi` or `bios`. No private SSH key or live API lookup is
needed. The former raw-key `-p` option is intentionally unsupported.
