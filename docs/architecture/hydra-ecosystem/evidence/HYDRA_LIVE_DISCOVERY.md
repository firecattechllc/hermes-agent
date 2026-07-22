# Hydra Live Discovery Evidence

## Discovery scope

This document records sanitized, read-only discovery evidence collected directly
from the Hydra Live virtual machine.

- Expected Tailscale node: `hydra-live`
- Environment type: VMware virtual machine
- Discovery purpose: Hydra ecosystem architecture baseline
- Machine identifiers, boot identifiers, local addresses, and account identifiers
  are omitted or generalized where they are not required for architecture decisions.

## Identity

```text
User: hydra
Hostname: hydra-VMware20-1
Chassis: vm
Virtualization: vmware
Hardware vendor: VMware, Inc.
Architecture: arm64
```

The operating-system hostname and the Tailscale node name are not identical:

```text
Operating-system hostname: hydra-VMware20-1
Tailscale node name: hydra-live
```

## Operating system

```text
Operating System: Ubuntu 26.04 LTS
Codename: resolute
Kernel: Linux 7.0.0-27-generic
Architecture: aarch64
```

## Virtual hardware

```text
Virtual CPUs: 2
CPU architecture: aarch64
Memory: 7.7 GiB
Swap: 4.0 GiB
Virtualization platform: VMware
```

At discovery time, approximately 3.9 GiB of memory was available.

## Storage

The VM uses an approximately 72 GB virtual NVMe disk.

```text
Root filesystem: approximately 68 GB
Root used: approximately 28 GB
Root available: approximately 37 GB
Root utilization: approximately 43%
Root filesystem type: ext4
Disk encryption: LUKS
Volume management: LVM
EFI partition: present
Separate boot partition: present
```

The Ubuntu 26.04 ARM64 installation image remained mounted as virtual optical
media at discovery time.

## Network

The VM had the following network classes:

```text
Primary LAN interface: private IPv4 address
Tailscale interface: active
Docker bridge: active
Additional Docker bridge: active
```

The default route used the VMware/LAN interface.

Exact local and overlay addresses are excluded from this architecture document.

## Tailscale

Tailscale was connected and the following ecosystem nodes were visible:

```text
hydra-live
hydra-prime
hydra-titan
matthews-macbook-air
iphone-15
matthews-tablet-tcl
gl-be3600
```

Prime advertised exit-node capability.

The active native service was:

```text
tailscaled.service: active and running
```

A second Snap-managed Tailscale service was also enabled but failed:

```text
snap.tailscale.tailscaled.service: failed
```

This indicates duplicate Tailscale installation or service configuration and
should be reconciled during stabilization.

## System health

Systemd reported:

```text
System state: degraded
Failed units: 2
```

Failed units:

```text
hydra-fleet-heartbeat.service
snap.tailscale.tailscaled.service
```

The failed fleet heartbeat should be investigated before Hydra Live is treated
as a dependable continuously monitored node.

## Running platform services

Notable active services included:

```text
containerd.service
docker.service
fail2ban.service
hydra-live.service
ollama.service
open-vm-tools.service
ssh.service
tailscaled.service
ufw.service
unattended-upgrades.service
```

Hydra Live’s principal service was enabled and active:

```text
hydra-live.service: active and running
Description: Hydra Live Desktop Server
```

## Listening services

Observed service exposure included:

```text
22/tcp     SSH
3000/tcp   Open WebUI container
3099/tcp   Hydra Cleaner container
3130/tcp   Python service
11434/tcp  Ollama, bound to loopback
```

Tailscale and normal local discovery or resolver ports were also present.

Ports `3000`, `3099`, and `3130` listened on all interfaces at discovery time.
Their intended exposure, authentication, ownership, and firewall policy should
be verified during security hardening.

Ollama listened only on the loopback interface.

## Containers

Docker was installed and running.

```text
Docker version: 29.1.3
```

Active containers:

```text
Name: hydra-cleaner-app
Image: nginx:alpine
Status: running
Published port: 3099 -> 80/tcp
```

```text
Name: open-webui
Image: ghcr.io/open-webui/open-webui:main
Status: healthy
Published port: 3000 -> 8080/tcp
```

No Podman runtime was detected in the discovery output.

## Hydra Live files

Relevant home-directory paths included:

```text
/home/hydra/.hydra
/home/hydra/hydra-live
```

The initial repository scan did not identify `/home/hydra/hydra-live` as a Git
working tree.

The only Git repository found within the configured search depth was:

```text
/home/hydra/mtkclient
Branch: main
Remote: public upstream repository
Working tree: clean in the collected output
```

The purpose of `mtkclient` within the Hydra Live VM should be documented or the
repository removed if it is no longer required.

## Hermes

```text
No Hermes executable found on active PATH
```

Hydra Live therefore cannot currently be assumed to host or directly execute
the Hermes CLI.

The architecture should initially treat Hermes as an external governed control
plane that communicates with Hydra Live through approved interfaces.

## Development runtimes

```text
Python: 3.14.4
Git: 2.53.0
```

Node.js and npm versions were not returned by the discovery command and should
not be assumed installed.

## Boot configuration

Notable enabled services included:

```text
docker.service
fail2ban.service
hydra-live.service
ollama.service
open-vm-tools.service
ssh.service
tailscaled.service
ufw.service
unattended-upgrades.service
```

The obsolete or conflicting Snap Tailscale service was also enabled.

## Discovery observations

1. Hydra Live is operational and reachable through Tailscale.
2. The VM is an ARM64 Ubuntu guest hosted through VMware.
3. The primary Hydra Live service is enabled and running.
4. Docker hosts Open WebUI and Hydra Cleaner.
5. Ollama is running and restricted to loopback.
6. Hermes is not installed on the active command path.
7. The system is degraded because the fleet heartbeat and duplicate Snap
   Tailscale services failed.
8. Several application ports listen on all interfaces and require an explicit
   exposure and authentication review.
9. The root filesystem is encrypted with LUKS and has sufficient free space for
   current stabilization work.
10. The installation ISO remains attached and should eventually be disconnected.
11. The `/home/hydra/hydra-live` directory requires a separate application-level
    inspection because it was not recognized as a Git repository.
12. Hydra Live should remain unchanged until the architecture review assigns its
    definitive runtime, trust-boundary, and deployment responsibilities.

## Recommended next evidence

Before changing the VM, collect targeted read-only evidence for:

- `hydra-live.service` unit definition and launch command
- `hydra-fleet-heartbeat.service` status and recent logs
- ownership and purpose of port `3130`
- Docker Compose files, bind mounts, volumes, and restart policies
- UFW policy and exposed-port rules
- contents and structure of `/home/hydra/hydra-live`
- reason both native and Snap Tailscale services are installed
- persistence and backup locations for Open WebUI and Hydra Cleaner
- VMware snapshot and recovery readiness
