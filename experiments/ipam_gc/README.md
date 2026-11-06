# ipam_gc (experiment)

Test-only. host-local IPAM keeps one file per reserved pod IP under `/var/lib/cni/networks/<network>/`;
only a successful CNI DEL removes it. Teardowns that time out or are killed leak the reservation, and the
directory survives reboots, so a node's pod range eventually fills ("no IP addresses available in range set").
This removes reservations whose sandbox no longer exists (`crictl pods -q --no-trunc`), keeping any held by a
known sandbox (ready or not). The lab trials run it before each trial and record the count as that trial's leak.

```sh
ansible-playbook experiments/ipam_gc/play.yml -l <node> -e mode=run
```
