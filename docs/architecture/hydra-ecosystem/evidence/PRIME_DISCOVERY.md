# Prime Discovery Evidence

- Captured: `2026-07-22T02:07:43Z`
- Collection mode: read-only SSH over Tailscale
- Host alias: `hydra-prime`
- Observed Tailscale IP: `100.119.205.44`

## Identity

```text
hydra-prime
 Static hostname: hydra-prime
       Icon name: computer
      Machine ID: [REDACTED]
         Boot ID: [REDACTED]
Operating System: Debian GNU/Linux 13 (trixie)
          Kernel: Linux 6.18.33+rpt-rpi-2712
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
Linux hydra-prime 6.18.33+rpt-rpi-2712 #1 SMP PREEMPT Debian 1:6.18.33-1+rpt1 (2026-06-01) aarch64 GNU/Linux
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
Mem:            15Gi       6.1Gi       2.8Gi       398Mi       7.5Gi       9.8Gi
Swap:          2.0Gi          0B       2.0Gi
```

## Storage

```text
Filesystem      Size  Used Avail Use% Mounted on
udev            7.9G     0  7.9G   0% /dev
tmpfs           3.2G  337M  2.9G  11% /run
/dev/mmcblk0p2  117G   51G   62G  46% /
tmpfs           8.0G  4.5M  8.0G   1% /dev/shm
tmpfs           5.0M     0  5.0M   0% /run/lock
tmpfs           1.0M     0  1.0M   0% /run/credentials/systemd-journald.service
tmpfs           8.0G  2.9M  8.0G   1% /tmp
/dev/sda2       120G  2.1G  118G   2% /mnt/hydra-vault
/dev/mmcblk0p1  510M   83M  428M  17% /boot/firmware
tmpfs           1.6G   80K  1.6G   1% /run/user/1000
tmpfs           1.0M     0  1.0M   0% /run/credentials/getty@tty1.service
tmpfs           1.0M     0  1.0M   0% /run/credentials/serial-getty@ttyAMA0.service

NAME        MAJ:MIN RM   SIZE RO TYPE MOUNTPOINTS
loop0         7:0    0     2G  0 loop
sda           8:0    1 119.5G  0 disk
├─sda1        8:1    1   200M  0 part
└─sda2        8:2    1 119.3G  0 part /mnt/hydra-vault
mmcblk0     179:0    0 119.1G  0 disk
├─mmcblk0p1 179:1    0   512M  0 part /boot/firmware
└─mmcblk0p2 179:2    0 118.6G  0 part /
zram0       254:0    0     2G  0 disk [SWAP]
```

## Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128
eth0             DOWN
wlan0            UP             10.0.0.11/24 fe80::24f8:7df5:d810:c263/64
br-2e1bd728e992  UP             172.22.0.1/16 fe80::3440:d5ff:fe54:d995/64
br-613da0e1d873  UP             172.24.0.1/16 fe80::40fe:ff:feca:5d08/64
br-944a42bc3be2  UP             172.23.0.1/16 fe80::3833:dbff:fe93:254e/64
br-bb3a12cba35c  UP             172.25.0.1/16 fe80::10f7:16ff:fea6:d912/64
br-c066652517a1  DOWN           172.18.0.1/16
docker0          UP             172.17.0.1/16 fe80::50ec:61ff:fe38:93cf/64
br-49c636b2f523  DOWN           172.20.0.1/16
br-802371092fd1  UP             172.19.0.1/16 fe80::acc7:71ff:fe38:123f/64
br-96d7d9e37329  DOWN           172.27.0.1/16
br-a31ac841e984  UP             172.26.0.1/16 fe80::209e:8cff:feb0:a140/64
br-2154d2b2dc86  UP             172.21.0.1/16 fe80::c5c:41ff:fe74:a000/64
tailscale0       UNKNOWN        100.119.205.44/32 fd7a:115c:a1e0::eb3a:cd2c/128 fe80::c66a:ee78:f00e:4831/64
veth0b0af0b@if2  UP             fe80::f046:5aff:fe86:fc29/64
vethbd110cd@if2  UP             fe80::c5a:4cff:fe73:a207/64
veth45761a1@if2  UP             fe80::b064:4fff:fe6f:c221/64
veth9b36f29@if3  UP             fe80::dcec:49ff:feaa:fe5b/64
veth3ec6f8a@if2  UP             fe80::3401:74ff:fe13:3395/64
veth5a13612@if2  UP             fe80::a81d:a9ff:fe59:58be/64
vetheb211c2@if2  UP             fe80::bc5f:98ff:fead:69c1/64
vethd836b33@if2  UP             fe80::f08a:e4ff:fef4:e67a/64
veth7359441@if2  UP             fe80::10b1:29ff:fed3:f0f/64
veth54edca4@if2  UP             fe80::4058:84ff:feb7:75c7/64
veth8494872@if2  UP             fe80::3c91:caff:fe34:507c/64
vetha7bf0d2@if2  UP             fe80::9037:1bff:fe85:fa88/64
vethb5c68d5@if2  UP             fe80::a87f:c9ff:fe05:25b5/64
veth8b247bb@if2  UP             fe80::c0ff:51ff:feb0:fba1/64
veth225ad94@if2  UP             fe80::8a1:84ff:fe8f:b9b6/64
veth8be7d39@if2  UP             fe80::b0b3:acff:feae:f31d/64
veth8548767@if2  UP             fe80::8829:b9ff:fefd:759e/64
veth1608204@if2  UP             fe80::3449:2eff:fe99:a9a7/64
veth473aa22@if2  UP             fe80::60a0:fff:fe9f:c3a6/64
veth9ed490e@if3  UP             fe80::ceb:bfff:fe7d:f683/64

default via 10.0.0.1 dev wlan0 proto dhcp src 10.0.0.11 metric 600
10.0.0.0/24 dev wlan0 proto kernel scope link src 10.0.0.11 metric 600
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1
172.18.0.0/16 dev br-c066652517a1 proto kernel scope link src 172.18.0.1 linkdown
172.19.0.0/16 dev br-802371092fd1 proto kernel scope link src 172.19.0.1
172.20.0.0/16 dev br-49c636b2f523 proto kernel scope link src 172.20.0.1 linkdown
172.21.0.0/16 dev br-2154d2b2dc86 proto kernel scope link src 172.21.0.1
172.22.0.0/16 dev br-2e1bd728e992 proto kernel scope link src 172.22.0.1
172.23.0.0/16 dev br-944a42bc3be2 proto kernel scope link src 172.23.0.1
172.24.0.0/16 dev br-613da0e1d873 proto kernel scope link src 172.24.0.1
172.25.0.0/16 dev br-bb3a12cba35c proto kernel scope link src 172.25.0.1
172.26.0.0/16 dev br-a31ac841e984 proto kernel scope link src 172.26.0.1
172.27.0.0/16 dev br-96d7d9e37329 proto kernel scope link src 172.27.0.1 linkdown
```

## Tailscale

```text
100.119.205.44   hydra-prime           hydra-prime.taile4d750.ts.net  linux    idle; offers exit node
100.87.157.71    gl-be3600             tagged-devices                 linux    -
100.101.170.106  hydra-live            firecat.techllc@               linux    offline, last seen 12d ago
100.103.4.38     hydra-titan           tagged-devices                 linux    active; direct 10.0.0.7:41641, tx 217073280 rx 20784648
100.121.185.3    iphone-15             firecat.techllc@               iOS      -
100.68.14.37     matthews-macbook-air  firecat.techllc@               macOS    active; direct 10.0.0.10:41641, tx 62092 rx 53188
100.121.27.51    matthews-tablet-tcl   tagged-devices                 android  -

100.119.205.44
```

## Running services

```text
  UNIT                              LOAD   ACTIVE SUB     DESCRIPTION
  bluetooth.service                 loaded active running Bluetooth service
  containerd.service                loaded active running containerd container runtime
  cron.service                      loaded active running Regular background program processing daemon
  crowdsec-firewall-bouncer.service loaded active running The firewall bouncer for CrowdSec
  crowdsec.service                  loaded active running Crowdsec agent
  dbus.service                      loaded active running D-Bus System Message Bus
  docker.service                    loaded active running Docker Application Container Engine
  fail2ban.service                  loaded active running Fail2Ban Service
  getty@tty1.service                loaded active running Getty on tty1
  grafana-server.service            loaded active running Grafana instance
  hydra-autoblock.service           loaded active running Hydra Auto Block Engine
  hydra-correlation.service         loaded active running Hydra Attack Correlation Engine
  hydra-defense.service             loaded active running Hydra Defense System
  hydra-trigger.service             loaded active running Hydra Dashboard Trigger Server
  hydra-watchdog.service            loaded active running Hydra Security Watchdog
  NetworkManager.service            loaded active running Network Manager
  nginx.service                     loaded active running A high performance web server and a reverse proxy server
  ntfy.service                      loaded active running ntfy server
  ollama.service                    loaded active running Ollama Service
  pihole-FTL.service                loaded active running Pi-hole FTL
  polkit.service                    loaded active running Authorization Manager
  prometheus-node-exporter.service  loaded active running Prometheus exporter for machine metrics
  prometheus.service                loaded active running Monitoring system and time series database
  serial-getty@ttyAMA0.service      loaded active running Serial Getty on ttyAMA0
  ssh.service                       loaded active running OpenBSD Secure Shell server
  suricata.service                  loaded active running Suricata IDS/IDP daemon
  systemd-hostnamed.service         loaded active running Hostname Service
  systemd-journald.service          loaded active running Journal Service
  systemd-logind.service            loaded active running User Login Management
  systemd-timesyncd.service         loaded active running Network Time Synchronization
  systemd-udevd.service             loaded active running Rule-based Manager for Device Events and Files
  tailscaled.service                loaded active running Tailscale node agent
  udisks2.service                   loaded active running Disk Manager
  unattended-upgrades.service       loaded active running Unattended Upgrades Shutdown
  unbound.service                   loaded active running Unbound DNS server
  user@1000.service                 loaded active running User Manager for UID 1000
  wpa_supplicant.service            loaded active running WPA supplicant

Legend: LOAD   → Reflects whether the unit definition was properly loaded.
        ACTIVE → The high-level unit activation state, i.e. generalization of SUB.
        SUB    → The low-level unit activation state, values depend on unit type.

37 loaded units listed.
```

## Listening ports

```text
Netid State  Recv-Q Send-Q               Local Address:Port  Peer Address:PortProcess
udp   UNCONN 0      0                          0.0.0.0:53         0.0.0.0:*
udp   UNCONN 0      0                          0.0.0.0:123        0.0.0.0:*
udp   UNCONN 0      0                          0.0.0.0:41641      0.0.0.0:*
udp   UNCONN 0      0                        127.0.0.1:5335       0.0.0.0:*
udp   UNCONN 0      0                        127.0.0.1:5335       0.0.0.0:*
udp   UNCONN 0      0                                *:53               *:*
udp   UNCONN 0      0                                *:123              *:*
udp   UNCONN 0      0                                *:41641            *:*
tcp   LISTEN 0      256                      127.0.0.1:5335       0.0.0.0:*
tcp   LISTEN 0      256                      127.0.0.1:5335       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:6060       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:8000       0.0.0.0:*    users:(("woodpecker-serv",pid=1322779,fd=8))
tcp   LISTEN 0      4096                     127.0.0.1:8082       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:8081       0.0.0.0:*
tcp   LISTEN 0      200                        0.0.0.0:8080       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:9443       0.0.0.0:*
tcp   LISTEN 0      511                        0.0.0.0:9900       0.0.0.0:*
tcp   LISTEN 0      32                         0.0.0.0:53         0.0.0.0:*
tcp   LISTEN 0      128                        0.0.0.0:22         0.0.0.0:*
tcp   LISTEN 0      511                        0.0.0.0:80         0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:9000       0.0.0.0:*    users:(("woodpecker-serv",pid=1322779,fd=7))
tcp   LISTEN 0      511                        0.0.0.0:8802       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:9100       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:9090       0.0.0.0:*
tcp   LISTEN 0      5                        127.0.0.1:8799       0.0.0.0:*
tcp   LISTEN 0      4096                100.119.205.44:443        0.0.0.0:*
tcp   LISTEN 0      511                        0.0.0.0:3202       0.0.0.0:*
tcp   LISTEN 0      511                        0.0.0.0:3300       0.0.0.0:*
tcp   LISTEN 0      511                        0.0.0.0:3303       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:3100       0.0.0.0:*
tcp   LISTEN 0      4096                100.119.205.44:8443       0.0.0.0:*
tcp   LISTEN 0      4096                100.119.205.44:8444       0.0.0.0:*
tcp   LISTEN 0      4096                100.119.205.44:8445       0.0.0.0:*
tcp   LISTEN 0      511                        0.0.0.0:3505       0.0.0.0:*
tcp   LISTEN 0      511                        0.0.0.0:3606       0.0.0.0:*
tcp   LISTEN 0      511                        0.0.0.0:3707       0.0.0.0:*
tcp   LISTEN 0      4096                    172.17.0.1:3005       0.0.0.0:*
tcp   LISTEN 0      511                        0.0.0.0:20211      0.0.0.0:*
tcp   LISTEN 0      128                        0.0.0.0:20212      0.0.0.0:*
tcp   LISTEN 0      4096                100.119.205.44:57895      0.0.0.0:*
tcp   LISTEN 0      4096                    172.17.0.1:11434      0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:3002       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:3003       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:3000       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:3001       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:3006       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:3007       0.0.0.0:*
tcp   LISTEN 0      4096                     127.0.0.1:3010       0.0.0.0:*
tcp   LISTEN 0      511                        0.0.0.0:3008       0.0.0.0:*
tcp   LISTEN 0      4096                             *:45876            *:*
tcp   LISTEN 0      4096   [fd7a:115c:a1e0::eb3a:cd2c]:64692         [::]:*
tcp   LISTEN 0      4096   [fd7a:115c:a1e0::eb3a:cd2c]:443           [::]:*
tcp   LISTEN 0      4096   [fd7a:115c:a1e0::eb3a:cd2c]:8444          [::]:*
tcp   LISTEN 0      4096   [fd7a:115c:a1e0::eb3a:cd2c]:8445          [::]:*
tcp   LISTEN 0      4096   [fd7a:115c:a1e0::eb3a:cd2c]:8443          [::]:*
tcp   LISTEN 0      32                            [::]:53            [::]:*
tcp   LISTEN 0      128                           [::]:22            [::]:*
tcp   LISTEN 0      4096                             *:3015             *:*
```

## Containers

```text
NAMES                        IMAGE                                        STATUS                          PORTS
paperless                    ghcr.io/paperless-ngx/paperless-ngx:latest   Up 2 weeks (healthy)            127.0.0.1:3007->8000/tcp
paperless-db                 postgres:16                                  Up 2 weeks                      5432/tcp
paperless-redis              redis:7                                      Up 2 weeks                      6379/tcp
paperless-auth-proxy         nginx:alpine                                 Up 2 weeks                      80/tcp
authentik-worker             ghcr.io/goauthentik/server:latest            Up 2 weeks (healthy)
authentik-server             ghcr.io/goauthentik/server:latest            Up 2 weeks (healthy)            127.0.0.1:9443->9443/tcp, 172.17.0.1:3005->9000/tcp
authentik-postgres           postgres:16-alpine                           Up 2 weeks                      5432/tcp
authentik-redis              redis:alpine                                 Up 2 weeks                      6379/tcp
hydra-homepage               ghcr.io/gethomepage/homepage:latest          Up 2 weeks (healthy)            127.0.0.1:3001->3000/tcp
gitea-runner                 gitea/act_runner:latest                      Restarting (1) 40 seconds ago
woodpecker-agent             woodpeckerci/woodpecker-agent:v3             Up 2 weeks (healthy)
woodpecker-server            woodpeckerci/woodpecker-server:v3            Up 2 weeks (healthy)
gitea                        gitea/gitea:latest                           Up 2 weeks
wikijs                       ghcr.io/requarks/wiki:2                      Up 2 weeks                      3443/tcp, 127.0.0.1:3010->3000/tcp
wikijs-db                    postgres:16-alpine                           Up 2 weeks                      5432/tcp
authentik-gitea-oidc-proxy   nginx:alpine                                 Up 2 weeks                      80/tcp
open-webui                   ghcr.io/open-webui/open-webui:main           Up 2 weeks (healthy)            127.0.0.1:3002->8080/tcp
anythingllm                  mintplexlabs/anythingllm:latest              Up 2 weeks (healthy)            127.0.0.1:3003->3001/tcp
hydra-loki                   grafana/loki:latest                          Up 2 weeks                      127.0.0.1:3100->3100/tcp
vaultwarden                  vaultwarden/server:latest                    Up 2 weeks (healthy)            127.0.0.1:3006->80/tcp
hydra-alloy                  grafana/alloy:latest                         Up 2 weeks
netalertx                    ghcr.io/netalertx/netalertx:latest           Up 2 weeks (healthy)
beszel-agent                 henrygd/beszel-agent:latest                  Up 2 weeks
hydra-alana-api              nginx:alpine                                 Up 2 weeks                      80/tcp
```

## Relevant repositories

```text
No repositories were returned by the initial discovery command.
The repository-discovery pipeline encountered a sed parsing error and
requires a targeted follow-up scan.
```

## Hermes

```text
No Hermes executable was found on the remote user's PATH during the
initial discovery. Installation status requires targeted verification.
```

## Discovery observations

- Prime was successfully reached through the `hydra-prime` SSH alias over
  Tailscale.
- The LAN address recorded in the previous Mac SSH configuration was stale.
- The initial repository scan encountered an unterminated `sed` expression.
  Repository inventory is therefore incomplete.
- Hermes was not detected on the remote user's active `PATH`.
- The `gitea-runner` container was observed in a restarting state.
- Hydra Live was observed offline and last seen 12 days before this capture.
- No changes were made to Prime during evidence collection.
