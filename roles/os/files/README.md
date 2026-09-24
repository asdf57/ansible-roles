# ISO builders

The `build-isos` Concourse pipeline is the supported entry point. It compiles
`homelabd`, builds the Arch and Debian Trixie ISOs in parallel, and publishes
them as OCI artifacts. These scripts contain only the distro-specific image
assembly used by that pipeline.
