# Host-key installation does not grant user-CA signing or unrelated secret access.
path "kv2/data/secrets/ssh/hosts/*" { capabilities = ["read"] }
path "auth/token/revoke-self" { capabilities = ["update"] }
