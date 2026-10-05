# Concourse must never read ssh/authorities CA private keys or router credentials.
path "kv2/data/secrets/automation/git-ssh-key" { capabilities = ["read"] }
path "kv2/data/secrets/ssh/clients/ansible-runner" { capabilities = ["read"] }
path "kv2/data/secrets/file-registry" { capabilities = ["read"] }
path "kv2/data/secrets/github/webhook" { capabilities = ["read"] }
path "kv2/data/secrets/stigmergy-runner-token" { capabilities = ["read"] }
path "kv2/data/secrets/stigmergy-agent-token" { capabilities = ["read"] }
path "kv2/data/secrets/ansible-runner-certificate" { capabilities = ["read"] }
path "kv2/data/secrets/ansible-known-hosts" { capabilities = ["read"] }
