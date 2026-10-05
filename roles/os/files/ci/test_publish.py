"""Local-only integration tests: no real Copyparty writes."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("publisher", Path(__file__).with_name("publish.py"))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class Response:
    status = 201
    def __enter__(self): return self
    def __exit__(self, *args): return False


class PublishingTests(unittest.TestCase):
    def test_manifest_is_last_and_immutable_artifacts_have_hashes(self):
        requests = []
        class Opener:
            def open(self, request, **kwargs):
                if isinstance(request, str):
                    raise publisher.urllib.error.HTTPError(request, 403, "private", {}, None)
                payload = request.data.read()
                self.assert_size = len(payload) == int(request.headers["Content-length"])
                requests.append((request.full_url, payload, self.assert_size))
                return Response()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "iso").mkdir(); (root / "netboot").mkdir()
            for relative in ("iso/test.iso", "netboot/vmlinuz", "netboot/initrd.img", "netboot/filesystem.squashfs"):
                (root / relative).write_bytes(relative.encode())
            (root / "image.yaml").write_text(json.dumps({"distribution":"debian", "artifactBaseURL":"https://files.example"}))
            (root / "build.json").write_text(json.dumps({"isoUid":"iso-uid", "buildId":"build-id"}))
            with patch.dict(os.environ, {"FILE_REGISTRY_PASSWORD":"test-only"}), patch.object(publisher.urllib.request, "build_opener", return_value=Opener()):
                publisher.publish(root)
        self.assertEqual(len(requests), 5)
        self.assertTrue(all(size for _, _, size in requests))
        self.assertTrue(requests[-1][0].endswith("/manifests/build-id.json"))
        manifest = json.loads(requests[-1][1])
        self.assertEqual(len(manifest["artifacts"]), 4)
        self.assertTrue(all("/builds/build-id/" in item["url"] and len(item["sha256"]) == 64 for item in manifest["artifacts"]))

    def test_public_storage_is_rejected_before_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "image.yaml").write_text(json.dumps({"artifactBaseURL": "https://files.example"}))
            (root / "build.json").write_text("{}")
            class Opener:
                def open(self, request, **kwargs):
                    if not isinstance(request, str):
                        raise AssertionError("Attempted upload to public storage")
                    return Response()
            with patch.object(publisher.urllib.request, "build_opener", return_value=Opener()):
                with self.assertRaisesRegex(ValueError, "anonymous reads"):
                    publisher.publish(root)

    def test_unsafe_endpoint_and_redirects_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "build.json").write_text("{}")
            for endpoint in ("http://files.example", "https://user:pw@files.example", "https://files.example?token=secret"):
                (root / "image.yaml").write_text(json.dumps({"artifactBaseURL": endpoint}))
                with self.assertRaises(ValueError): publisher.publish(root)
        with self.assertRaises(ValueError): publisher.NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.example")


if __name__ == "__main__":
    unittest.main()
