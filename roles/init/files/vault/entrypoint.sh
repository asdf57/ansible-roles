#!/bin/sh
set -xeuo pipefail

# Start Vault server in the background
echo "Starting Vault server..."
vault server -config=/vault.hcl &
VAULT_PID=$!

# Wait until Vault is responsive using the health endpoint
echo "Waiting for Vault to be ready..."
until curl -s http://127.0.0.1:8200/v1/sys/health | grep -q 'initialized'; do
  sleep 2
done

# If keys have not been generated, initialize Vault
if [ ! -f /stuff/keys ]; then
  echo "Vault not initialized. Initializing now..."
  vault operator init -key-shares=5 -key-threshold=3 -format=json > /stuff/keys
else
  echo "Vault already initialized."
fi

jq -r '.root_token' /stuff/keys > /stuff/root_token

echo "Unsealing Vault..."
for key in $(jq -r '.unseal_keys_b64[]' /stuff/keys | head -n 3); do
  vault operator unseal "$key"
done

echo "Vault is ready and operational."

vault login "$(cat /stuff/root_token)"

if ! vault secrets list | grep -q "^kv2/"; then
  echo "Enabling KV version 2 secrets engine..."
  vault secrets enable -path=kv2 kv || true
else
  echo "KV secrets engine is already enabled."
fi

if ! vault secrets list -format=json | jq -e '."concourse/"' >/dev/null; then
  vault secrets enable -version=2 -path=concourse kv
fi

if vault auth list -format=json | jq -e '."approle/"' > /dev/null; then
  echo "AppRole already enabled, continuing"
else
  vault auth enable approle
fi

if vault policy read concourse >/dev/null 2>&1; then
  vault policy write concourse ./policies/concourse-policy.hcl
else
  vault policy write concourse ./policies/concourse-policy.hcl
fi

if ! vault read auth/approle/role/concourse >/dev/null 2>&1; then
  vault write auth/approle/role/concourse token_policies=concourse period=1h bind_secret_id=true
fi

concourse_role_id=$(vault read -field=role_id auth/approle/role/concourse/role-id | tr -d '\n')
concourse_secret_id=$(vault write -field=secret_id -f auth/approle/role/concourse/secret-id | tr -d '\n')

echo "$concourse_role_id" > /shared/concourse_role_id
echo "$concourse_secret_id" > /shared/concourse_secret_id

# Keep Vault running in the foreground
wait $VAULT_PID
