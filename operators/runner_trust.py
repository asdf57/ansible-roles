"""Derive strict Server host trust before any ordinary Ansible command."""
import json
import os
import shlex
import sys

from common import API, alias, capture_inventory, fingerprint, inventory_hosts, public_key, write_private


def prepare(api, inventory):
    entries = []
    for name in inventory_hosts(inventory):
        server = api.get("servers", name)
        if server["metadata"].get("deletionTimestamp"):
            raise RuntimeError("Server is terminating")
        provisioning = server.get("status", {}).get("provisioning", {})
        if provisioning.get("maintenance") or provisioning.get("phase") in (
                "PreparingBoot", "AwaitingLive", "Installing", "AwaitingInstalled", "Verifying"):
            raise RuntimeError("Server " + name + " is reserved by provisioning")
        host = server.get("status", {}).get("hostSSH", {})
        desired = host.get("keyPairRef")
        if not host.get("keyReady") or host.get("phase") != "Ready" or not desired or host.get(
                "installedKeyPairRef") != desired:
            raise RuntimeError("Server " + name + " has not verified its managed SSH identity")
        key = public_key(host["publicKey"])
        if fingerprint(key) != host.get("fingerprint") or host.get(
                "installedFingerprint") != host.get("fingerprint"):
            raise RuntimeError("Server host-key fingerprint mismatch")
        entries.append(alias(server) + " " + key + "\n")
        for group in inventory.values():
            if name in group.get("hosts", {}):
                group["hosts"][name]["ansible_ssh_common_args"] = "-o HostKeyAlias=" + shlex.quote(
                    alias(server))
    return "".join(entries)


def main():
    api = API()
    _, inventory = capture_inventory(api, os.environ["INVENTORY_CAPTURE_GROUP"])
    trust = prepare(api, inventory)
    write_private(os.environ["ANSIBLE_KNOWN_HOSTS_FILE"], trust)
    write_private(os.environ["ANSIBLE_INVENTORY"], json.dumps(inventory))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Runner host trust refused: " + str(exc), file=sys.stderr)
        sys.exit(1)
