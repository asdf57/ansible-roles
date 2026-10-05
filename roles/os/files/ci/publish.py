#!/usr/bin/env python3
"""Upload immutable artifacts first; publish the completed manifest last."""
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request
from urllib.parse import urlsplit


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Artifact upload redirects are not accepted")


def publish(root=Path("image-output")):
    configuration = json.loads((root / "image.yaml").read_text())
    metadata = json.loads((root / "build.json").read_text())
    endpoint = urlsplit(configuration["artifactBaseURL"])
    if (endpoint.scheme != "https" and not (endpoint.scheme == "http" and endpoint.hostname in ("localhost", "127.0.0.1"))) or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("Artifact uploads require HTTPS (loopback HTTP is test-only)")
    opener = urllib.request.build_opener(NoRedirects())
    base = configuration["artifactBaseURL"].rstrip("/") + "/iso-resources/" + metadata["isoUid"]
    build_path = base + "/builds/" + metadata["buildId"] + "/"
    isos = list((root / "iso").glob("*.iso"))
    if len(isos) != 1:
        raise ValueError("Expected exactly one completed ISO")
    rootfs_name = "arch/x86_64/airootfs.sfs" if configuration["distribution"] == "arch" else "filesystem.squashfs"
    files = [("iso", isos[0], isos[0].name),
             ("kernel", root / "netboot/vmlinuz", "netboot/vmlinuz"),
             ("initrd", root / "netboot/initrd.img", "netboot/initrd.img"),
             ("rootfs", root / "netboot" / rootfs_name, "netboot/" + rootfs_name)]
    headers = {"PW": "pipeline:" + os.environ["FILE_REGISTRY_PASSWORD"], "Content-Type": "application/octet-stream"}

    def upload(path, address):
        # Stream large ISOs rather than holding the whole image in memory.
        for attempt in range(3):
            try:
                with path.open("rb") as data:
                    request = urllib.request.Request(address, data=data, method="PUT", headers={**headers, "Content-Length": str(path.stat().st_size)})
                    with opener.open(request, timeout=300) as response:
                        if response.status not in (200, 201, 204):
                            raise RuntimeError("Upload failed")
                return
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    artifacts = []
    for kind, path, name in files:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        address = build_path + name
        upload(path, address)
        artifacts.append({"type": kind, "url": address, "sha256": digest})
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({**metadata, "artifacts": artifacts}))
    upload(manifest, base + "/manifests/" + metadata["buildId"] + ".json")


if __name__ == "__main__":
    publish()
