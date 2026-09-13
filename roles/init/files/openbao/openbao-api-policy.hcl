path "kv2/data/secrets/*" {
  capabilities = ["create", "read", "update", "delete"]
}

path "kv2/metadata/secrets/*" {
  capabilities = ["delete"]
}
