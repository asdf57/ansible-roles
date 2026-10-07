"""Exercise the real Ansible role; only systemd is stubbed in this container."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import public_key, write_private


def main():
    if not Path("/.dockerenv").exists() or os.getuid() != 0:
        raise RuntimeError("Use a disposable, network-isolated root container")
    with tempfile.TemporaryDirectory(prefix="host-role-") as tmp:
        directory = Path(tmp)
        for name in ("original", "managed"):
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f",
                 str(directory / name)], check=True)
        write_private("/etc/ssh/ssh_host_ed25519_key", (directory / "original").read_text())
        write_private("/etc/ssh/sshd_config",
                      "HostKey /etc/ssh/ssh_host_ed25519_key\nPasswordAuthentication no\n")
        counter = directory / "reloads"
        # This is a fixture service manager, not a production replacement.
        write_private(
            "/usr/local/bin/systemctl", "#!/bin/sh\ncase \"$1\" in\n"
            "show) printf 'LoadState=loaded\\nActiveState=active\\nSubState=running\\n' ;;\n"
            "reload) printf 'reload\\n' >> " + str(counter) + " ;;\n"
            "*) exit 1 ;;\nesac\n")
        Path("/usr/local/bin/systemctl").chmod(0o755)
        os.environ["ANSIBLE_ROLES_PATH"] = "/source/roles"
        os.environ["ANSIBLE_LOCAL_TEMP"] = str(directory / "ansible-local")
        desired = public_key((directory / "managed.pub").read_text())
        variables = directory / "variables.json"
        write_private(
            variables,
            json.dumps({
                "managed_host_private_key_file": str(directory / "managed"),
                "managed_host_public_key": desired
            }))
        args = [
            "ansible-playbook", "-i", "localhost,", "-c", "local",
            "/source/plays/ssh_host_keys.yml", "-e", "@" + str(variables)
        ]
        for attempt in range(2):
            result = subprocess.run(args, capture_output=True, text=True, timeout=120)
            if result.returncode:
                print(result.stdout, file=sys.stderr)
                print(result.stderr, file=sys.stderr)
                raise RuntimeError("Real host-key Ansible role failed")
            actual = subprocess.run(["ssh-keygen", "-y", "-f", "/etc/ssh/ssh_host_ed25519_key"],
                                    capture_output=True, text=True, check=True).stdout
            assert public_key(actual) == desired
            assert Path("/etc/ssh/ssh_host_ed25519_key").stat().st_mode & 0o777 == 0o600
        assert counter.read_text().splitlines() == [
            "reload"
        ], "Idempotent retry reloaded sshd unnecessarily"
        print(
            "PASS: real Ansible key staging, public/private matching, sshd validation, atomic installation, permissions and idempotent reload"
        )


if __name__ == "__main__":
    main()
