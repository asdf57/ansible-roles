#!/bin/sh

if [ ! -f /acme.sh/account.conf ]; then
  echo "Initializing acme.sh"

  acme.sh --upgrade

  if [[ "${ACME_SERVER}" == "zerossl" ]]; then
    echo "Registering account with zerossl"
      acme.sh --register-account --server zerossl \
        --eab-kid  "${ZEROSSL_EAB_KID}" \
        --eab-hmac-key "${ZEROSSL_EAB_HMAC_KEY}" \
        --accountemail "${ZEROSSL_EMAIL}"
  else
    exit 1
  fi

  echo "Issue cert"
  echo "DNS_API: ${DNS_API}"
  acme.sh --issue --dns "${DNS_API}" -d "${ACME_DOMAIN}" -d "*.${ACME_DOMAIN}" --server "${ACME_SERVER}"
fi

mkdir -p /certs/ryuugu.dev
acme.sh --install-cert -d "${ACME_DOMAIN}" \
  --key-file       /certs/ryuugu.dev/key.pem \
  --fullchain-file /certs/ryuugu.dev/full.pem \
  --cert-file      /certs/ryuugu.dev/cert.pem \
  --ca-file        /certs/ryuugu.dev/ca.pem

# Deploy to nginx
echo "Deploying cert to nginx"
export DEPLOY_DOCKER_CONTAINER_LABEL=sh.acme.autoload.domain=nginx.ryuugu.dev
export DEPLOY_DOCKER_CONTAINER_RELOAD_CMD="nginx -s reload"
acme.sh --deploy -d "${ACME_DOMAIN}" --deploy-hook docker
# export DEPLOY_DOCKER_CONTAINER_LABEL=sh.acme.autoload.domain=nginx.ryuugu.dev
# export DEPLOY_DOCKER_CONTAINER_KEY_FILE=/etc/nginx/ssl/ryuugu.dev/key.pem
# export DEPLOY_DOCKER_CONTAINER_CERT_FILE=/etc/nginx/ssl/ryuugu.dev/cert.pem
# export DEPLOY_DOCKER_CONTAINER_CA_FILE=/etc/nginx/ssl/ryuugu.dev/ca.pem
# export DEPLOY_DOCKER_CONTAINER_FULLCHAIN_FILE=/etc/nginx/ssl/ryuugu.dev/full.pem
# export DEPLOY_DOCKER_CONTAINER_RELOAD_CMD="nginx -s reload"
# acme.sh --deploy -d "${ACME_DOMAIN}" --deploy-hook docker

echo 'Listing certs'
acme.sh --list

touch /done

crond -n -s -m off