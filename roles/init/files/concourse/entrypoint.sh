#!/bin/sh

# Set up the Vault credentials
# export CONCOURSE_VAULT_CLIENT_TOKEN="$(cat /stuff/root_token)"

role_id=$(cat /shared/concourse_role_id)
secret_id=$(cat /shared/concourse_secret_id)

cat -A /shared/concourse_role_id
cat -A /shared/concourse_secret_id

export CONCOURSE_VAULT_AUTH_BACKEND="approle"
export CONCOURSE_VAULT_AUTH_PARAM="role_id:$role_id,secret_id:$secret_id"

echo "$CONCOURSE_VAULT_AUTH_PARAM"

exec dumb-init /usr/local/bin/entrypoint.sh "$@"
