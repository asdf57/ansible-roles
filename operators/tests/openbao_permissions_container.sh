#!/bin/sh
set -eu
test -f /.dockerenv || { echo 'Use a disposable network-isolated container' >&2; exit 1; }
export BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=fixture-only-root-token
bao server -dev -dev-root-token-id="$BAO_TOKEN" >/tmp/bao-fixture.log 2>&1 &
fixture_pid=$!
trap 'kill "$fixture_pid" 2>/dev/null || true' EXIT
attempt=0
until bao status >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  test "$attempt" -lt 50 || exit 1
  sleep 0.2
done
bao secrets enable -path=kv2 kv-v2 >/dev/null
bao auth enable approle >/dev/null
bao policy write ssh-host-operator /source/roles/init/files/openbao/ssh-host-operator-policy.hcl >/dev/null
bao write auth/approle/role/ssh-host-operator \
  token_policies=ssh-host-operator token_no_default_policy=true \
  token_ttl=10m token_max_ttl=10m secret_id_ttl=0 secret_id_num_uses=0 >/dev/null
operator_role_id=$(bao read -field=role_id auth/approle/role/ssh-host-operator/role-id)
operator_secret_id=$(bao write -field=secret_id -f auth/approle/role/ssh-host-operator/secret-id)
bao kv put -mount=kv2 secrets/ssh/hosts/server-uid privateKey=fixture >/dev/null
bao kv put -mount=kv2 secrets/ssh/authorities/ca privateKey=must-not-read >/dev/null
operator_token=$(bao write -field=token auth/approle/login role_id="$operator_role_id" secret_id="$operator_secret_id")
export BAO_TOKEN="$operator_token"
bao kv get -mount=kv2 secrets/ssh/hosts/server-uid >/dev/null
if bao kv get -mount=kv2 secrets/ssh/authorities/ca >/dev/null 2>&1; then
  echo 'Operator could read CA private material' >&2; exit 1
fi
if bao kv put -mount=kv2 secrets/ssh/hosts/server-uid privateKey=changed >/dev/null 2>&1; then
  echo 'Operator could overwrite managed secret material' >&2; exit 1
fi
bao token revoke -self >/dev/null
echo 'PASS: host-only AppRole read, CA denial, write denial and self-revocation'
