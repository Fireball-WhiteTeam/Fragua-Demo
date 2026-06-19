# Fragua Demo — Architecture Reference

## Deployment Topology

```
┌──────────────────────────────────────────────────────┐
│                  EmberNet Control Plane               │
│   embernet001 (Azure CP)  ── embernet003 (WG Hub)    │
│   embernet004 (AWS CP)    ── embernet005 (AWS Relay)  │
│                                                       │
│   Services:                                           │
│   - dashboard.embernet.ai (Industrial Dashboard)      │
│   - clusters.embernet.ai (Rancher)                    │
│   - flux.embernet.ai:1280 (Flux/Ziti Controller)      │
│   - ignition-cloud.fireball-system.svc:8088           │
│   - anvilmq:4000 (PLC message broker)                 │
└────────────────────┬─────────────────────────────────┘
                     │ WireGuard (100.64.0.0/24)
                     │ Flux Zero-Trust Overlay (Ziti)
        ┌────────────┴────────────┐
        │                         │
┌─────────────────────┐    ┌──────────────────────┐
│   fragua-edge-01    │    │   fragua-edge-02     │
│   (K3s Server)      │    │   (K3s Agent)        │
│   Ubuntu 24.04      │    │   Ubuntu 24.04       │
│                     │    │                      │
│   Host container:   │    │   Host container:    │
│   embernet-endpoint │    │   embernet-endpoint  │
│   (podman --network │    │   (podman --network  │
│    host, v0.0.40)   │    │    host, v0.0.40)    │
│   ├── WG embernet0  │    │   ├── WG embernet0   │
│   ├── Flux/Ziti     │    │   ├── Flux/Ziti      │
│   └── loopback API  │    │   └── loopback API   │
│                     │    │                      │
│   Pods (K3s):       │    │   Pods (K3s):        │
│   ├── CODESYS       │    │   ├── CODESYS        │
│   └── Ign Edge      │    │   └── Ign Edge       │
│                     │    │                      │
│   WG: 100.64.0.30   │    │   WG: 100.64.0.36    │
└─────────────────────┘    └──────────────────────┘
```

> **2026-06-04 architecture change.** The legacy `wg-quick@embernet0` systemd unit + `embernet-wg-watchdog.timer` + in-cluster `flux-tunnel-fragua-edge-*` DaemonSet pods were ALL replaced by a single per-edge **embernet-endpoint container** running on the host with `--network host`. The container owns the `embernet0` netlink interface (kernel WG via wgctrl), the Flux/Ziti tunneler, and the loopback API at `127.0.0.1:8765`. K3s flannel-iface stays bound to `embernet0` — flannel doesn't care who created the interface.

## Data Flow

```
CODESYS Runtime (PLC_PRG tags)
    │ OPC-UA (local)
    ▼
Ignition Edge
    │ Gateway Network (via Flux overlay)
    ▼
Ignition Cloud (embernet CP)
    │ Internal
    ▼
EmberNET Dashboard (tag display, alarms, trends)
```

## Network Layers

| Layer | Technology | Ports |
|---|---|---|
| Underlay | Azure VNet / Public IP | SSH:22 (admin), UDP/443 (WG mgmt) |
| VPN | WireGuard via embernet-endpoint daemon | `embernet0` iface, 100.64.0.0/10 |
| Mesh | Flux/Ziti (zero-trust) via embernet-endpoint daemon | `vpn.embernet.ai:443`, `flux.embernet.ai:443`, `cdn.embernet.ai:443` |
| K8s | K3s / Flannel (`flannel-iface: embernet0`) | 6443, 10250 |
| App | CODESYS + Ignition Edge | OPC-UA:4840, HTTP:8088 |

## Container Images

| App | Image | Deployed Via |
|---|---|---|
| CODESYS AMD64 | `codesys/codesys-control` | App Store / HelmChart CRD |
| Ignition Edge | `inductiveautomation/ignition` | App Store / HelmChart CRD |
| EmberNET Endpoint (per-edge host VPN + Ziti) | `ghcr.io/embernet-ai/embernetlite:0.0.40` | `podman run --network host` (NOT k3s) |

## K8s Labels (Required)

```yaml
# Node labels
embernet.ai/site: fragua
embernet.ai/facility: demo
embernet.ai/tenant: fragua-demo

# Pod/Service labels (set by Helm charts)
embernet.ai/store-app: "true"
embernet.ai/app-name: "<display-name>"
embernet.ai/gui-type: "web"
embernet.ai/gui-port: "<port>"
app.kubernetes.io/instance: "{{ .Release.Name }}"
```
