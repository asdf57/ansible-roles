## Container logs and etcd storage

Concourse worker logs use Docker's compressed `local` driver with three 10 MiB
files. Changing logging options requires container recreation; existing logs
are not retroactively rotated.

Both bootstrap and full Compose definitions run an unprivileged
`etcd-maintenance` service from the Stigmergy image. Every five minutes it
reclaims already compacted pages when at least 64 MiB and 25% is free. The
one-hour automatic history compaction remains enabled. Only a NOSPACE alarm
is cleared, and only after the database is below 1.5 GiB against the explicit
2 GiB quota. Corruption and insufficient headroom remain errors in its bounded
logs. Online defragmentation briefly blocks the single etcd member's requests.

For an existing deployment, `plays/maintain_platform_storage.yml` accepts
`platform_data_path`, `platform_env_file` and a pushed 40-character
`stigmergy_revision`. It preserves the existing Compose service settings and
credentials, adds storage policy, and recreates only the API, maintenance
service and worker. It does not restart etcd or change resource state.
