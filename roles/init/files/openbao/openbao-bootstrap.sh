#!/bin/sh
set -eu

umask 077

initialized_now=false
status_json="$(bao status -format=json 2>/dev/null || true)"
if echo "$status_json" | grep -q '"initialized"[[:space:]]*:[[:space:]]*false'; then
  init_tmp=/run/openbao-init/init.json.tmp
  bao operator init -key-shares=1 -key-threshold=1 -format=json > "$init_tmp"

  awk '
    /"unseal_keys_b64"/ { in_keys = 1; next }
    in_keys && /"/ {
      value = $0
      sub(/^[[:space:]]*"/, "", value)
      sub(/"[,]?[[:space:]]*$/, "", value)
      print value
      exit
    }
  ' "$init_tmp" > /run/openbao-init/unseal-key
  awk -F '"' '/"root_token"/ { print $4; exit }' "$init_tmp" > /run/openbao-init/root-token
  rm -f "$init_tmp"

  if [ ! -s /run/openbao-init/unseal-key ] || [ ! -s /run/openbao-init/root-token ]; then
    echo "failed to extract OpenBao initialization credentials" >&2
    exit 1
  fi
  initialized_now=true
fi

if [ ! -s /run/openbao-init/unseal-key ] || [ ! -s /run/openbao-init/root-token ]; then
  echo "OpenBao is initialized but its automation credentials are missing" >&2
  exit 1
fi

status_json="$(bao status -format=json 2>/dev/null || true)"
if echo "$status_json" | grep -q '"sealed"[[:space:]]*:[[:space:]]*true'; then
  bao operator unseal "$(cat /run/openbao-init/unseal-key)" >/dev/null
fi

export BAO_TOKEN
BAO_TOKEN="$(cat /run/openbao-init/root-token)"

if ! bao secrets list -format=json | grep -q '"kv2/"'; then
  bao secrets enable -path=kv2 kv-v2
fi
if ! bao auth list -format=json | grep -q '"approle/"'; then
  bao auth enable approle
fi

bao policy write stigmergy-api /openbao/bootstrap/openbao-api-policy.hcl
bao policy write concourse /openbao/bootstrap/concourse-policy.hcl
bao policy write ssh-host-operator /openbao/bootstrap/ssh-host-operator-policy.hcl
bao write auth/approle/role/ssh-host-operator \
  token_policies=ssh-host-operator token_no_default_policy=true \
  token_ttl=10m token_max_ttl=10m secret_id_ttl=0 secret_id_num_uses=0
# Preserve AppRole enrollment credentials across bootstrap reruns.
if ! bao kv get -mount=kv2 secrets/ssh-host-operator-auth >/dev/null 2>&1; then
  operator_role_id="$(bao read -field=role_id auth/approle/role/ssh-host-operator/role-id)"
  operator_secret_id="$(bao write -field=secret_id -f auth/approle/role/ssh-host-operator/secret-id)"
  bao kv put -mount=kv2 secrets/ssh-host-operator-auth roleId="$operator_role_id" secretId="$operator_secret_id" >/dev/null
fi

bao write auth/approle/role/stigmergy-api \
  token_policies=stigmergy-api \
  token_ttl=1h \
  token_max_ttl=4h \
  token_num_uses=0 \
  secret_id_ttl=0 \
  secret_id_num_uses=0

if [ "$initialized_now" = true ] || [ ! -s /run/openbao-bootstrap/role-id ] || [ ! -s /run/openbao-bootstrap/secret-id ]; then
  bao read -field=role_id auth/approle/role/stigmergy-api/role-id > /run/openbao-bootstrap/role-id.tmp
  bao write -field=secret_id -f auth/approle/role/stigmergy-api/secret-id > /run/openbao-bootstrap/secret-id.tmp
  mv /run/openbao-bootstrap/role-id.tmp /run/openbao-bootstrap/role-id
  mv /run/openbao-bootstrap/secret-id.tmp /run/openbao-bootstrap/secret-id
fi

bao write auth/approle/role/concourse \
  token_policies=concourse \
  period=1h \
  bind_secret_id=true

bao read -field=role_id auth/approle/role/concourse/role-id > /shared/concourse_role_id.tmp
bao write -field=secret_id -f auth/approle/role/concourse/secret-id > /shared/concourse_secret_id.tmp
mv /shared/concourse_role_id.tmp /shared/concourse_role_id
mv /shared/concourse_secret_id.tmp /shared/concourse_secret_id

echo "OpenBao is initialized, unsealed, and its AppRole credentials are ready"
