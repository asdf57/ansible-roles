#!/bin/bash

# Busy wait until the cert exists
rety_limit=100

while [ ! -f /etc/nginx/ssl/ryuugu.dev/full.pem ]; do
  echo "Waiting for cert to be generated"
  retry_limit=$((retry_limit-1))
  if [ $retry_limit -eq 0 ]; then
    echo "Retry limit reached"
    exit 1
  fi
  sleep 5
done

exec nginx -g "daemon off;"
