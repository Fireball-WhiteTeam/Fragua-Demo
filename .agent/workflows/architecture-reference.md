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
┌───────▼──────────┐    ┌────────▼──────────┐
│  fragua-edge-01  │    │  fragua-edge-02   │
│  (K3s Server)    │    │  (K3s Agent)      │
│  Ubuntu 24.04    │    │  Ubuntu 24.04     │
│                  │    │                   │
│  Pods:           │    │  Pods:            │
│  ├── CODESYS     │    │  ├── CODESYS      │
│  │   (codesys-   │    │  │   (codesys-    │
│  │    control)   │    │  │    control)    │
│  ├── Ign Edge    │    │  ├── Ign Edge     │
│  │   (Edge ONLY) │    │  │   (Edge ONLY)  │
│  └── Flux Tunnel │    │  └── Flux Tunnel  │
│                  │    │                   │
│  WG: 100.64.0.X │    │  WG: 100.64.0.Y  │
└──────────────────┘    └───────────────────┘
```

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
| Underlay | Azure VNet / Public IP | SSH:22, WG:51820 |
| VPN | WireGuard | 100.64.0.0/24 |
| Mesh | Flux/Ziti (zero-trust) | 6262 (controller) |
| K8s | K3s / Flannel (wg0 iface) | 6443, 10250 |
| App | CODESYS + Ignition Edge | OPC-UA:4840, HTTP:8088 |

## Container Images

| App | Image | Deployed Via |
|---|---|---|
| CODESYS AMD64 | `codesys/codesys-control` | App Store / HelmChart CRD |
| Ignition Edge | `inductiveautomation/ignition` | App Store / HelmChart CRD |
| Flux Edge Tunnel | (from flux-helm-charts) | Helm CLI |

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
