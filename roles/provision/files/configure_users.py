"""Configure explicit users in the installed root; never generate credentials."""
import json
import os
from pathlib import Path
import re
import subprocess


def name(value):
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value):
        raise RuntimeError("Unsupported user/group name")
    return value


def run(*args):
    subprocess.run(["chroot", "/mnt", *args], check=True, timeout=30)


for group in json.loads(os.environ["PROVISION_GROUPS"]):
    run("groupadd", "-f", name(group))
for user in json.loads(os.environ["PROVISION_USERS"]):
    username = name(user["name"])
    if username in ("root", "ansible", "homelabd"):
        raise RuntimeError("Reserved account is not an ordinary user")
    groups = [name(group) for group in user.get("groups", [])]
    for group in groups:
        run("groupadd", "-f", group)
    shell = user.get("shell", "/bin/bash")
    if not re.fullmatch(r"/[a-zA-Z0-9_./-]+", shell):
        raise RuntimeError("Invalid shell")
    args = ["useradd", "-m", "-s", shell]
    if groups:
        args += ["-G", ",".join(groups)]
    run(*args, username)
    run("usermod", "-p", "!" if user.get("locked", False) else "*", username)
    keys = user.get("publicKeys", [])
    if keys:
        directory = Path("/mnt/home") / username / ".ssh"
        directory.mkdir(mode=0o700)
        (directory / "authorized_keys").write_text("\n".join(keys) + "\n")
        (directory / "authorized_keys").chmod(0o600)
        run("chown", "-R", username + ":" + username, "/home/" + username + "/.ssh")
