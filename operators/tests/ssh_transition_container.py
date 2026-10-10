"""Real OpenSSH transition test, only inside an isolated disposable container.

API/secret storage and Ansible installation are faked; SSH handshakes, host pins,
user certificates, private/public key matching and transition orchestration are real.
"""
import copy
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_operators import API
import common
import ssh_host_keys as operator


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def main():
    if not Path("/.dockerenv").exists() or os.getuid() != 0:
        raise RuntimeError("Run as root only inside a disposable network-isolated container")
    command("adduser", "-D", "-s", "/bin/sh", "ansible")
    command("passwd", "-d", "ansible")
    with tempfile.TemporaryDirectory(prefix="host-transition-") as tmp:
        directory = Path(tmp)
        directory.chmod(0o755)
        for name in ("ca", "runner", "temporary", "managed", "unexpected"):
            command("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(directory / name))
        command("ssh-keygen", "-q", "-s", str(directory / "ca"), "-I", "operator-test", "-n",
                "ansible", "-V", "-1m:+10m", str(directory / "runner.pub"))
        common.write_private(directory / "principals", "ansible\n")
        (directory / "principals").chmod(0o644)
        common.write_private(directory / "active", (directory / "temporary").read_text())
        common.write_private(
            directory / "sshd_config", "\n".join([
                "Port 22", "ListenAddress 127.0.0.1", "HostKey " + str(directory / "active"),
                "PidFile " + str(directory / "sshd.pid"), "PasswordAuthentication no",
                "KbdInteractiveAuthentication no", "AuthorizedKeysFile none",
                "PubkeyAuthentication yes", "AuthenticationMethods publickey",
                "TrustedUserCAKeys " + str(directory / "ca.pub"), "AllowUsers ansible",
                "LogLevel ERROR", ""
            ]))
        os.environ["ANSIBLE_PRIVATE_KEY_FILE"] = str(directory / "runner")
        os.environ["ANSIBLE_CERTIFICATE_FILE"] = str(directory / "runner-cert.pub")
        daemon = subprocess.Popen(
            ["/usr/sbin/sshd", "-D", "-e", "-f",
             str(directory / "sshd_config")], stderr=subprocess.PIPE)
        try:
            assert daemon.stderr is not None
            time.sleep(0.2)
            if daemon.poll() is not None:
                raise RuntimeError("Test sshd failed: " + daemon.stderr.read().decode())
            api = API()
            managed = common.public_key((directory / "managed.pub").read_text())
            api.server["status"]["hostSSH"].update(publicKey=managed,
                                                   fingerprint=common.fingerprint(managed))
            # Only fixture credentials are involved; preserve diagnostics for
            # failed handshakes without making the production operator verbose.
            real_probe = operator.ssh_probe

            def diagnostic_probe(value, host, known, checking="yes"):
                try:
                    return real_probe(value, host, known, checking)
                except RuntimeError:
                    result = subprocess.run([
                        "ssh", "-vv", *common.ssh_args(value, known, checking), "ansible@" + host,
                        "true"
                    ], capture_output=True, text=True)
                    print(result.stderr[-6000:], file=sys.stderr)
                    raise

            installs = []

            def private(api_arg, value):
                assert value["status"]["hostSSH"].get(
                    "bootstrapPublicKey") or value["status"]["hostSSH"].get("installedKeyPairRef")
                return (directory / "managed").read_text()

            def install(value, host, task_dir, known, play, variables=None):
                if play == "ssh_host_keys.yml":
                    assert variables is not None
                    # The real role is syntax checked separately. This simulates
                    # its atomic file replacement/reload without systemd in Docker.
                    private_path = Path(variables["managed_host_private_key_file"])
                    common.write_private(directory / "active", private_path.read_text())
                    daemon.send_signal(signal.SIGHUP)
                    time.sleep(0.2)
                    installs.append(play)

            with patch.object(operator, "ssh_probe", side_effect=diagnostic_probe), patch.object(
                    operator, "host_private_key",
                    side_effect=private), patch.object(operator, "run_play", side_effect=install):
                with tempfile.TemporaryDirectory() as task:
                    operator.reconcile(api, api.get("servers", "node"), "127.0.0.1", Path(task))
                trust = api.server["status"]["hostSSH"]
                assert trust["phase"] == "Ready" and "bootstrapPublicKey" not in trust
                assert trust["installedFingerprint"] == common.fingerprint(managed)
                assert api.server["status"]["installedSSHTrustBundleDigest"] == "digest"
                # Retry is strict and reuses the managed identity.
                with tempfile.TemporaryDirectory() as task:
                    operator.reconcile(api, api.get("servers", "node"), "127.0.0.1", Path(task))
                assert len(installs) == 2
                # A subsequent new temporary key must fail before private delivery.
                common.write_private(directory / "active", (directory / "unexpected").read_text())
                daemon.send_signal(signal.SIGHUP)
                time.sleep(0.2)
                with tempfile.TemporaryDirectory() as task, patch.object(
                        operator, "ssh_probe",
                        side_effect=real_probe), patch.object(operator,
                                                              "host_private_key") as private_read:
                    try:
                        operator.reconcile(api, api.get("servers", "node"), "127.0.0.1", Path(task))
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("Unexpected host key was accepted")
                    private_read.assert_not_called()
            print(
                "PASS: real certificate login, durable TOFU pin, managed-key transition, strict retry and changed-key rejection"
            )
        finally:
            daemon.terminate()
            daemon.wait(timeout=5)
            if daemon.stderr is not None:
                daemon.stderr.close()


if __name__ == "__main__":
    main()
