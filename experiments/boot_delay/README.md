# boot_delay (experiment)

Test-only. Lab nodes reboot in ~1-3 min; real servers often spend minutes in firmware. A reboot window over
5 minutes matters: the node stays NotReady long enough for the default `tolerationSeconds: 300` evictions to
go through instead of being cancelled. This sets a GRUB countdown to stand in for that firmware time.

```sh
ansible-playbook experiments/boot_delay/play.yml -l <node> -e mode=install -e boot_delay_seconds=180
ansible-playbook experiments/boot_delay/play.yml -l <node> -e mode=uninstall
```
