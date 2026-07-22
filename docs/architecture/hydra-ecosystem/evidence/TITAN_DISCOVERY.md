# Titan Discovery Evidence

- Captured: `2026-07-22T02:16:25Z`
- Collection mode: read-only SSH over Tailscale
- Remote target: `hydra@100.103.4.38`
- Expected host: `hydra-titan`

## Identity

```text
hydra-titan
 Static hostname: hydra-titan
       Icon name: computer
      Machine ID: [REDACTED]
         Boot ID: [REDACTED]
Operating System: Debian GNU/Linux 13 (trixie)
          Kernel: Linux 6.18.34+rpt-rpi-2712
    Architecture: arm64
hydra
```

## Operating system

```text
PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
NAME="Debian GNU/Linux"
VERSION_ID="13"
VERSION="13 (trixie)"
VERSION_CODENAME=trixie
DEBIAN_VERSION_FULL=13.6
ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"
Linux hydra-titan 6.18.34+rpt-rpi-2712 #1 SMP PREEMPT Debian 1:6.18.34-1+rpt1 (2026-06-09) aarch64 GNU/Linux
```

## CPU and memory

```text
Architecture:                            aarch64
CPU op-mode(s):                          32-bit, 64-bit
Byte Order:                              Little Endian
CPU(s):                                  4
On-line CPU(s) list:                     0-3
Vendor ID:                               ARM
Model name:                              Cortex-A76
Model:                                   1
Thread(s) per core:                      1
Core(s) per cluster:                     4
Socket(s):                               -
Cluster(s):                              1
Stepping:                                r4p1
Frequency boost:                         disabled
CPU(s) scaling MHz:                      100%
CPU max MHz:                             2400.0000
CPU min MHz:                             1500.0000
BogoMIPS:                                108.00
Flags:                                   fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm lrcpc dcpop asimddp
L1d cache:                               256 KiB (4 instances)
L1i cache:                               256 KiB (4 instances)
L2 cache:                                2 MiB (4 instances)
L3 cache:                                2 MiB (1 instance)
NUMA node(s):                            8
NUMA node0 CPU(s):                       0-3
NUMA node1 CPU(s):                       0-3
NUMA node2 CPU(s):                       0-3
NUMA node3 CPU(s):                       0-3
NUMA node4 CPU(s):                       0-3
NUMA node5 CPU(s):                       0-3
NUMA node6 CPU(s):                       0-3
NUMA node7 CPU(s):                       0-3
Vulnerability Gather data sampling:      Not affected
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             Not affected
Vulnerability L1tf:                      Not affected
Vulnerability Mds:                       Not affected
Vulnerability Meltdown:                  Not affected
Vulnerability Mmio stale data:           Not affected
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Not affected
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; CSV2, BHB
Vulnerability Srbds:                     Not affected
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Not affected
Vulnerability Vmscape:                   Not affected

               total        used        free      shared  buff/cache   available
Mem:            15Gi       9.9Gi       3.8Gi       399Mi       2.6Gi       5.9Gi
Swap:          2.0Gi          0B       2.0Gi
```

## Storage

```text
Filesystem      Size  Used Avail Use% Mounted on
udev            7.9G     0  7.9G   0% /dev
tmpfs           3.2G  329M  2.9G  11% /run
/dev/mmcblk0p2  235G   15G  211G   7% /
tmpfs           8.0G  592K  8.0G   1% /dev/shm
tmpfs           5.0M   48K  5.0M   1% /run/lock
tmpfs           1.0M     0  1.0M   0% /run/credentials/systemd-journald.service
tmpfs           8.0G   64K  8.0G   1% /tmp
/dev/mmcblk0p1  510M   79M  432M  16% /boot/firmware
tmpfs           1.6G  272K  1.6G   1% /run/user/1000
/dev/sda1       113G  914M  107G   1% /mnt/titan-vault
tmpfs           1.0M     0  1.0M   0% /run/credentials/getty@tty1.service
tmpfs           1.0M     0  1.0M   0% /run/credentials/serial-getty@ttyAMA10.service

NAME        MAJ:MIN RM   SIZE RO TYPE MOUNTPOINTS
loop0         7:0    0     2G  0 loop
sda           8:0    1 115.3G  0 disk
└─sda1        8:1    1 115.3G  0 part /mnt/titan-vault
mmcblk0     179:0    0 238.8G  0 disk
├─mmcblk0p1 179:1    0   512M  0 part /boot/firmware
└─mmcblk0p2 179:2    0 238.2G  0 part /
zram0       254:0    0     2G  0 disk [SWAP]
nvme0n1     259:0    0 238.5G  0 disk
├─nvme0n1p1 259:1    0   512M  0 part
└─nvme0n1p2 259:2    0  14.4G  0 part
```

## Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128
eth0             DOWN
wlan0            UP             10.0.0.7/24 fe80::fd43:e1:e0ce:e6c3/64
tailscale0       UNKNOWN        100.103.4.38/32 fd7a:115c:a1e0::613a:427/128 fe80::27c3:dd5b:99fc:3aa/64
br-5b91868aba8c  UP             172.19.0.1/16 fe80::42:35ff:feb5:6ef1/64
br-b7400dc2d449  UP             172.22.0.1/16 fe80::42:e3ff:fe9b:d86d/64
docker0          DOWN           172.17.0.1/16
br-1dd881b7a2d3  UP             172.18.0.1/16 fe80::42:8ff:fee0:3442/64
veth96b2344@if9  UP             fe80::a4a3:a6ff:fe75:4115/64
veth3aaae02@if11 UP             fe80::ec9c:91ff:fe93:b22e/64
veth461a4e9@if15 UP             fe80::98a6:a6ff:feaa:6599/64

default via 10.0.0.1 dev wlan0 proto dhcp src 10.0.0.7 metric 600
10.0.0.0/24 dev wlan0 proto kernel scope link src 10.0.0.7 metric 600
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1 linkdown
172.18.0.0/16 dev br-1dd881b7a2d3 proto kernel scope link src 172.18.0.1
172.19.0.0/16 dev br-5b91868aba8c proto kernel scope link src 172.19.0.1
172.22.0.0/16 dev br-b7400dc2d449 proto kernel scope link src 172.22.0.1
```

## Tailscale

```text
100.103.4.38     hydra-titan           hydra-titan.taile4d750.ts.net  linux    -
100.87.157.71    gl-be3600             tagged-devices                 linux    -
100.101.170.106  hydra-live            firecat.techllc@               linux    offline, last seen 12d ago
100.119.205.44   hydra-prime           tagged-devices                 linux    active; offers exit node; direct 10.0.0.11:41641, tx 22383980 rx 215425916
100.121.185.3    iphone-15             firecat.techllc@               iOS      -
100.68.14.37     matthews-macbook-air  firecat.techllc@               macOS    active; direct 10.0.0.10:41641, tx 37476 rx 32324
100.121.27.51    matthews-tablet-tcl   tagged-devices                 android  -

100.103.4.38
```

## Running services

```text
  UNIT                          LOAD   ACTIVE SUB     DESCRIPTION
  accounts-daemon.service       loaded active running Accounts Service
  auditd.service                loaded active running Security Audit Logging Service
  avahi-daemon.service          loaded active running Avahi mDNS/DNS-SD Stack
  bluetooth.service             loaded active running Bluetooth service
  containerd.service            loaded active running containerd container runtime
  cron.service                  loaded active running Regular background program processing daemon
  cups-browsed.service          loaded active running Make remote CUPS printers available locally
  cups.service                  loaded active running CUPS Scheduler
  dbus.service                  loaded active running D-Bus System Message Bus
  docker.service                loaded active running Docker Application Container Engine
  fail2ban.service              loaded active running Fail2Ban Service
  getty@tty1.service            loaded active running Getty on tty1
  lightdm.service               loaded active running Light Display Manager
  ModemManager.service          loaded active running Modem Manager
  NetworkManager.service        loaded active running Network Manager
  nfs-blkmap.service            loaded active running pNFS block layout mapping daemon
  polkit.service                loaded active running Authorization Manager
  serial-getty@ttyAMA10.service loaded active running Serial Getty on ttyAMA10
  ssh.service                   loaded active running OpenBSD Secure Shell server
  systemd-hostnamed.service     loaded active running Hostname Service
  systemd-journald.service      loaded active running Journal Service
  systemd-logind.service        loaded active running User Login Management
  systemd-timesyncd.service     loaded active running Network Time Synchronization
  systemd-udevd.service         loaded active running Rule-based Manager for Device Events and Files
  tailscaled.service            loaded active running Tailscale node agent
  titan-update-share.service    loaded active running Titan Update Intelligence HTTP Share
  udisks2.service               loaded active running Disk Manager
  unattended-upgrades.service   loaded active running Unattended Upgrades Shutdown
  user@1000.service             loaded active running User Manager for UID 1000
  wayvnc-control.service        loaded active running VNC Control Service
  wayvnc.service                loaded active running VNC Server
  wpa_supplicant.service        loaded active running WPA supplicant

Legend: LOAD   → Reflects whether the unit definition was properly loaded.
        ACTIVE → The high-level unit activation state, i.e. generalization of SUB.
        SUB    → The low-level unit activation state, values depend on unit type.

32 loaded units listed.
```

## Listening ports

```text
Netid State  Recv-Q Send-Q              Local Address:Port  Peer Address:PortProcess
udp   UNCONN 0      0                         0.0.0.0:5353       0.0.0.0:*
udp   UNCONN 0      0                         0.0.0.0:41641      0.0.0.0:*
udp   UNCONN 0      0                         0.0.0.0:51171      0.0.0.0:*
udp   UNCONN 0      0                               *:5353             *:*
udp   UNCONN 0      0                               *:41641            *:*
udp   UNCONN 0      0                               *:51967            *:*
tcp   LISTEN 0      4096                 100.103.4.38:3002       0.0.0.0:*
tcp   LISTEN 0      5                    100.103.4.38:8787       0.0.0.0:*
tcp   LISTEN 0      128                  100.103.4.38:22         0.0.0.0:*
tcp   LISTEN 0      4096                      0.0.0.0:3099       0.0.0.0:*
tcp   LISTEN 0      4096                    127.0.0.1:40995      0.0.0.0:*
tcp   LISTEN 0      4096                 100.103.4.38:50254      0.0.0.0:*
tcp   LISTEN 0      4096                    127.0.0.1:631        0.0.0.0:*
tcp   LISTEN 0      16                   100.103.4.38:5900       0.0.0.0:*
tcp   LISTEN 0      4096                      0.0.0.0:8090       0.0.0.0:*
tcp   LISTEN 0      511                             *:3001             *:*
tcp   LISTEN 0      4096                         [::]:3099          [::]:*
tcp   LISTEN 0      4096                        [::1]:631           [::]:*
tcp   LISTEN 0      4096   [fd7a:115c:a1e0::613a:427]:51515         [::]:*
tcp   LISTEN 0      4096                         [::]:8090          [::]:*
```

## Containers

```text
NAMES                     IMAGE                                     STATUS                PORTS
freellmapi-freellmapi-1   ghcr.io/tashfeenahmed/freellmapi:latest   Up 3 days (healthy)   100.103.4.38:3002->3001/tcp
hydra-cleaner-app         nginx:alpine                              Up 3 days             0.0.0.0:3099->80/tcp, :::3099->80/tcp
uptime-kuma               louislam/uptime-kuma:1                    Up 3 days (healthy)
beszel-agent              henrygd/beszel-agent                      Up 3 days
beszel                    henrygd/beszel                            Up 3 days             0.0.0.0:8090->8090/tcp, :::8090->8090/tcp
```

## Relevant repositories

```text
/home/hydra/services/freellmapi
/opt/hydra-os
```

## Hermes executable

```text
No Hermes executable found on active PATH
```

## Hermes installation candidates

```text
```

## Development runtimes

```text
python3    /usr/bin/python3
Python 3.13.5
python     /usr/bin/python
Python 3.13.5
pip        /usr/bin/pip
pip 25.1.1 from /usr/lib/python3/dist-packages/pip (python 3.13)
pip3       /usr/bin/pip3
pip 25.1.1 from /usr/lib/python3/dist-packages/pip (python 3.13)
git        /usr/bin/git
git version 2.47.3
docker     /usr/bin/docker
Docker version 26.1.5+dfsg1, build a72d7cd
```

## Discovery observations

- Titan was reached using the current Tailscale address.
- Evidence collection was read-only.
- Machine and boot identifiers were redacted before staging.
- Empty sections indicate that the corresponding command returned no visible data.
- No services, repositories, containers, packages, or configuration files were modified.
