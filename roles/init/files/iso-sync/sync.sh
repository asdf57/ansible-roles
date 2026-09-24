#!/bin/sh

set -eu

sync_artifact() {
  repository=$1
  destination=$2
  reference="registry:5000/homelab/${repository}:latest"
  target="/www/data/${destination}"

  digest="$(oras resolve --plain-http "$reference" 2>/dev/null || true)"
  if [ -z "$digest" ]; then
    echo "Waiting for ${reference}"
    return
  fi
  if [ -f "${target}/.digest" ] && [ "$(sed -n '1p' "${target}/.digest")" = "$digest" ]; then
    return
  fi

  staging="$(mktemp -d "/www/data/.${destination}.XXXXXX")"
  if ! oras pull --plain-http --output "$staging" "$reference"; then
    rm -rf "$staging"
    return
  fi
  printf '%s\n' "$digest" > "${staging}/.digest"

  previous="/www/data/.${destination}.previous"
  rm -rf "$previous"
  if [ -d "$target" ]; then
    mv "$target" "$previous"
  fi
  mv "$staging" "$target"
  rm -rf "$previous"
  echo "Updated ${destination} to ${digest}"
}

while true; do
  sync_artifact arch-netboot arch_netboot
  sync_artifact debian-trixie-netboot debian_trixie_netboot
  sleep 60
done
