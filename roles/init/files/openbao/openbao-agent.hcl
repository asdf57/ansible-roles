vault {
  address = "http://openbao:8200"

  retry {
    num_retries = 0
  }
}

auto_auth {
  method "approle" {
    mount_path = "auth/approle"
    config = {
      role_id_file_path                   = "/run/openbao-bootstrap/role-id"
      secret_id_file_path                 = "/run/openbao-bootstrap/secret-id"
      remove_secret_id_file_after_reading = false
    }
  }

  sink "file" {
    config = {
      path = "/run/openbao/token"
      mode = 0640
    }
  }
}
