import base64
import copy
import json
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common
import runner_trust
import ssh_host_keys as operator


def key(byte):
    wire = (11).to_bytes(4, "big") + b"ssh-ed25519" + (32).to_bytes(4, "big") + bytes([byte]) * 32
    return "ssh-ed25519 " + base64.b64encode(wire).decode()


def server():
    return {
        "metadata": {
            "name": "node",
            "uid": "server-uid",
            "generation": 1,
            "resourceVersion": "1"
        },
        "status": {
            "hostSSH": {
                "keyReady": True,
                "keyPairRef": {
                    "name": "owned",
                    "uid": "key-uid"
                },
                "publicKey": key(1),
                "fingerprint": common.fingerprint(key(1))
            },
            "desiredSSHTrustBundleDigest": "digest",
            "sshTrust": {
                "publicBundle": "bundle"
            },
            "networking": {
                "management": {
                    "address": {
                        "address": "127.0.0.1"
                    }
                }
            }
        }
    }


class API:

    def __init__(self):
        self.server = server()
        self.writes = []
        self.conflict = False

    def get(self, collection, name):
        return copy.deepcopy(self.server)

    def list(self, collection):
        return [copy.deepcopy(self.server)] if collection == 'servers' else []

    def patch_status(self, value, status):
        if self.conflict or value["metadata"]["resourceVersion"] != self.server["metadata"][
                "resourceVersion"]:
            error = HTTPError("", 409, "Conflict", {}, io.BytesIO())
            error.close()
            raise error
        self.writes.append(copy.deepcopy(status))
        for section, fields in status.items():
            if not isinstance(fields, dict):
                self.server["status"][section] = fields
                continue
            target = self.server["status"].setdefault(section, {})
            for name, value in fields.items():
                if value is None:
                    target.pop(name, None)
                else:
                    target[name] = value
        self.server["metadata"]["resourceVersion"] = str(
            int(self.server["metadata"]["resourceVersion"]) + 1)
        return copy.deepcopy(self.server)


class OperatorTests(unittest.TestCase):

    def test_status_client_routes_by_resource_kind(self):
        with patch.dict(os.environ, STIGMERGY_API_URL='https://api.example',
                        STIGMERGY_API_TOKEN='test-token'):
            api = common.API()
        for kind, collection in [('Server', 'servers'), ('ProvisioningRun', 'provisioning-runs')]:
            resource = {
                'kind': kind,
                'metadata': {
                    'name': 'node',
                    'uid': 'uid',
                    'resourceVersion': '7'
                }
            }
            with patch('common.request_json', return_value={}) as request:
                api.patch_status(resource, {'phase': 'Pending'})
            args = request.call_args.args
            self.assertEqual(args[0], f'https://api.example/api/v1alpha1/{collection}/node/status')
            self.assertEqual(args[3]['metadata'], {'uid': 'uid'})
            self.assertEqual(args[4]['If-Match'], '"7"')

    def test_operator_uses_ansible_resolved_group_and_host_variables(self):
        inventory = {
            'all': {
                'vars': {
                    'storage': {
                        'partitions': ['efi', 'swap', 'ext4']
                    }
                },
                'hosts': {
                    'node': {
                        'ansible_host': '127.0.0.1'
                    }
                }
            }
        }
        resolved = {
            '_meta': {
                'hostvars': {
                    'node': {
                        'ansible_host': '127.0.0.1',
                        'storage': {
                            'partitions': ['efi', 'swap', 'ext4']
                        }
                    }
                }
            }
        }
        with patch.object(common.subprocess, 'check_output',
                          return_value=json.dumps(resolved)) as run:
            result = common.resolved_inventory_hosts(inventory)
        self.assertEqual(result['node']['storage']['partitions'], ['efi', 'swap', 'ext4'])
        self.assertEqual(run.call_args.args[0][0], 'ansible-inventory')

    def test_private_resolution_checks_owned_metadata_and_revokes_token(self):
        api = API()
        value = api.get("servers", "node")
        owned = {
            "metadata": {
                "uid": "key-uid",
                "generation": 1,
                "annotations": {
                    "homelab.io/server-uid": "server-uid"
                }
            },
            "spec": {
                "path": "ssh/hosts/server-uid",
                "secretStoreRef": {
                    "name": "openbao"
                }
            },
            "status": {
                "phase": "Ready",
                "observedGeneration": 1,
                "publicKey": key(1)
            }
        }
        environment = {
            "BAO_ADDR": "https://bao.example",
            "HOST_KEY_BAO_ROLE_ID": "role",
            "HOST_KEY_BAO_SECRET_ID": "secret"
        }
        responses = [{
            "auth": {
                "client_token": "short-lived"
            }
        }, {
            "data": {
                "data": {
                    "sshKeyPairUID": "key-uid",
                    "publicKey": key(1),
                    "privateKey": "fixture"
                }
            }
        }, {}]
        with patch.dict(os.environ,
                        environment), patch.object(api, "get", return_value=owned), patch.object(
                            operator, "request_json", side_effect=responses) as request:
            self.assertEqual(operator.host_private_key(api, value), "fixture")
            self.assertEqual(request.call_count, 3)
            self.assertTrue(request.call_args_list[1].args[0].endswith("/ssh/hosts/server-uid"))
            self.assertTrue(request.call_args_list[2].args[0].endswith("/auth/token/revoke-self"))
        for mutate in (
                lambda k: k["metadata"]["annotations"].update({"homelab.io/server-uid": "other"}),
                lambda k: k["metadata"].update(uid="replacement"),
                lambda k: k["spec"].update(path="ssh/authorities/ca")):
            invalid = copy.deepcopy(owned)
            mutate(invalid)
            with patch.object(api, "get",
                              return_value=invalid), patch.object(operator,
                                                                  "request_json") as request:
                with self.assertRaises(RuntimeError):
                    operator.host_private_key(api, value)
                request.assert_not_called()

    def test_ed25519_validation(self):
        self.assertEqual(common.public_key(key(1) + " comment"), key(1))
        for bad in ("ssh-rsa AAAA", "ssh-ed25519 AAAA", "ssh-ed25519 not-base64",
                    key(1).replace("ssh-ed25519", "bad")):
            with self.assertRaises(Exception):
                common.public_key(bad)

    def test_first_pin_is_written_before_secrets(self):
        api = API()
        calls = []

        def probe(value, host, known, checking="yes"):
            calls.append(checking)
            if checking == "accept-new":
                common.write_private(known, common.alias(value) + " " + key(2) + "\n")

        def private(*args):
            self.assertEqual(api.server["status"]["hostSSH"]["bootstrapPublicKey"], key(2))
            raise RuntimeError("stop before installation")

        with tempfile.TemporaryDirectory() as tmp, patch.object(operator, "ssh_probe",
                                                                side_effect=probe), patch.object(
                                                                    operator, "host_private_key",
                                                                    side_effect=private):
            with self.assertRaises(RuntimeError):
                operator.reconcile(api, server(), "127.0.0.1", Path(tmp))
        self.assertEqual(calls, ["accept-new", "yes"])
        self.assertEqual(api.server["status"]["hostSSH"]["phase"], "BootstrapPinned")

    def test_conflicting_pin_write_never_fetches_secrets(self):
        api = API()
        api.conflict = True

        def probe(value, host, known, checking):
            common.write_private(known, common.alias(value) + " " + key(2) + "\n")

        with tempfile.TemporaryDirectory() as tmp, patch.object(operator, "ssh_probe",
                                                                side_effect=probe), patch.object(
                                                                    operator,
                                                                    "host_private_key") as private:
            with self.assertRaises(HTTPError):
                operator.reconcile(api, server(), "127.0.0.1", Path(tmp))
            private.assert_not_called()

    def test_retry_uses_durable_pin_without_tofu(self):
        api = API()
        api.server["status"]["hostSSH"]["bootstrapPublicKey"] = key(2)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                operator, "ssh_probe",
                side_effect=RuntimeError("changed key")) as probe, patch.object(
                    operator, "host_private_key") as private:
            with self.assertRaises(RuntimeError):
                operator.reconcile(api, api.get("servers", "node"), "127.0.0.1", Path(tmp))
            self.assertEqual(probe.call_args.args[2].read_text().count("ssh-ed25519"), 2)
            self.assertEqual(len(probe.call_args.args), 3)
            private.assert_not_called()

    def test_established_host_never_accepts_bootstrap_key(self):
        api = API()
        trust = api.server["status"]["hostSSH"]
        trust.update(installedKeyPairRef=trust["keyPairRef"],
                     installedFingerprint=trust["fingerprint"], bootstrapPublicKey=key(2))
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                operator, "ssh_probe",
                side_effect=RuntimeError("changed key")) as probe, patch.object(
                    operator, "host_private_key") as private:
            with self.assertRaises(RuntimeError):
                operator.reconcile(api, api.get("servers", "node"), "127.0.0.1", Path(tmp))
            self.assertNotIn(key(2), probe.call_args.args[2].read_text())
            private.assert_not_called()

    def test_stale_uid_generation_binding_or_key_cannot_report(self):
        for mutate in (lambda s: s["metadata"].update(uid="replacement"),
                       lambda s: s["metadata"].update(generation=2),
                       lambda s: s["status"]["hostSSH"]["keyPairRef"].update(uid="replacement"),
                       lambda s: s["status"]["hostSSH"].update(bootstrapPublicKey=key(2)),
                       lambda s: s["status"]["hostSSH"].update(installedKeyPairRef={
                           "name": "owned",
                           "uid": "key-uid"
                       }), lambda s: s["status"].update(desiredSSHTrustBundleDigest="new"),
                       lambda s: s["status"]["networking"]["management"]["address"].update(
                           address="127.0.0.2")):
            api = API()
            snapshot = api.get("servers", "node")
            mutate(api.server)
            with self.assertRaises(RuntimeError):
                operator.observation(api, snapshot, {"phase": "Ready"})
            self.assertEqual(api.writes, [])

    def test_normal_runner_requires_verified_identity(self):
        api = API()
        inventory = {"all": {"hosts": {"node": {"ansible_host": "127.0.0.1"}}}}
        with self.assertRaises(RuntimeError):
            runner_trust.prepare(api, inventory)
        host = api.server["status"]["hostSSH"]
        host.update(installedKeyPairRef=host["keyPairRef"],
                    installedFingerprint=host["fingerprint"], phase="Ready")
        trust = runner_trust.prepare(api, inventory)
        self.assertEqual(trust, "server-server-uid " + key(1) + "\n")
        self.assertEqual(inventory["all"]["hosts"]["node"]["ansible_ssh_common_args"],
                         "-o HostKeyAlias=server-server-uid")

    def test_no_credentials_on_plain_http_or_redirect(self):
        with self.assertRaises(RuntimeError):
            common.request_json("http://api.example/resource", "secret")
        with self.assertRaises(RuntimeError):
            common.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")

    def test_private_file_mode_and_address_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secret"
            common.write_private(path, "private")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for value in ("-oProxyCommand=bad", "host;command", "hostname", "user@127.0.0.1"):
            with self.assertRaises(ValueError):
                common.address(value)


if __name__ == "__main__":
    unittest.main()
