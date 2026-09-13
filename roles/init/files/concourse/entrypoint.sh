#!/bin/sh

# Set up the OpenBao AppRole credentials using Concourse's Vault-compatible
# credential-manager settings.

role_id=$(cat /shared/concourse_role_id)
secret_id=$(cat /shared/concourse_secret_id)

export CONCOURSE_VAULT_AUTH_BACKEND="approle"
export CONCOURSE_VAULT_AUTH_PARAM="role_id:$role_id,secret_id:$secret_id"

exec dumb-init /usr/local/bin/entrypoint.sh "$@"
