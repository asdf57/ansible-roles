# kernel_pin (experiment)

Test-only. Installs a pinned Debian kernel package on one node so it can match an
older host (here bookworm `linux-image-6.1.0-45-amd64` = 6.1.170-1, which still
supports cgroup v1 memory/cpuset; Debian's 6.12 does not). Nothing in `plays/`,
`roles/`, provisioning or any Configuration references it.

The `.deb` comes from snapshot.debian.org's content-addressed store and is checked
against its SHA-1 before it reaches the node; apt installs it with dependencies.

```sh
ansible-playbook experiments/kernel_pin/play.yml -l <server> -e mode=install
ansible-playbook experiments/kernel_pin/play.yml -l <server> -e mode=uninstall
```

Install only selects the test kernel for the **next boot** (`grub-reboot`), so a
failed first boot returns to the distro kernel on a power cycle. Re-run install
after it has booted to make it the GRUB default. Uninstall removes the default;
reboot onto the distro kernel and run uninstall again to purge the package.
