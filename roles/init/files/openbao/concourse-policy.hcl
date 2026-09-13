path "kv2/data/secrets/*" {
  capabilities = ["read"]
}

path "kv2/metadata/secrets/*" {
  capabilities = ["read", "list"]
}
