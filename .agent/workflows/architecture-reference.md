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
│    host, v0.0.99)   │    │    host, v0.0.99)    │
│   ├── WG embernet0  │    │   ├── WG embernet0   │
│   ├── Flux/Ziti     │    │   ├── Flux/Ziti      │
│   └── loopback API  │    │   └── loopback API   │
│                     │    │                      │
│   Containers:       │    │   Containers:        │
│   ├── CODESYS       │    │   ├── CODESYS        │
│   └── Ign Edge      │    │   └── Ign Edge       │
│                     │    │                      │
│   WG: 100.64.2.2    │    │   WG: 100.64.2.1     │
└─────────────────────┘    └──────────────────────┘
```

> **2026-06-04 architecture change.** The legacy `wg-quick@embernet0` systemd unit + `embernet-wg-watchdog.timer` + in-cluster `flux-tunnel-fragua-edge-*` DaemonSet pods were ALL replaced by a single per-edge **embernet-endpoint container** running on the host with `--network host`. The container owns the `embernet0` netlink interface (kernel WG via wgctrl) and the loopback API at `127.0.0.1:8765`. K3s flannel-iface stays bound to `embernet0` — flannel doesn't care who created the interface.

> **2026-07-20 correction — the Ziti data plane did NOT survive that change.**
> Two facts verified live on both edges today:
>
> 1. **Enrollment had never completed.** From 2026-06-01 until today the file at
>    `/var/lib/embernet/identity/embernet.json` was the raw OTT *enrollment token*,
>    not an enrolled identity — the OTT lapsed 2026-06-08 and the controller showed
>    `authenticators: 0` for both identities. Versions ≤0.0.47 shipped a stub flux
>    driver that reported `state: connected` regardless, which masked this for six
>    weeks. Fixed by `cb6adfe feat(flux): enroll the one-time JWT into a real
>    identity`, shipped in v0.0.48+. Both edges re-enrolled on v0.0.99 on 2026-07-20
>    and now show `authenticators: 1 -> ['cert']` + `hasApiSession: True`.
>
> 2. **embernet-endpoint is NOT a Ziti tunneler and does not replace one.** It
>    depends on `openziti/sdk-golang` (client SDK), not `ziti-tunnel-sdk-go`, and
>    contains no tproxy/intercept code. It holds an in-process Ziti API session; it
>    does **not** intercept host traffic. Consequently there is no tun interface, no
>    `100.65.0.0/24` route, and no nft/iptables intercept on either edge, and
>    `hasEdgeRouterConnection` is `False` on both.
>
> **Open consequence:** the `100.65.0.x` synthetic IPs that Ignition Edge uses to
> reach ignition-cloud (`100.65.0.1:8060`) were provided by the deleted
> `flux-edge-tunnel` DaemonSet. Nothing provides them today — all three dial checks
> fail. Either the endpoint gains a tunneler, or the Ignition leg needs a tunnel
> sidecar/DaemonSet restored. This is the remaining blocker to the tag path below.

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
| VPN | WireGuard via embernet-endpoint daemon | `embernet0` iface, 100.64.2.0/24 (Fragua) |
| Mesh | Flux/Ziti API session via embernet-endpoint daemon — **control plane only, no traffic intercept** | `vpn.embernet.ai:443`, `flux.embernet.ai:443`, `cdn.embernet.ai:443` |
| K8s | K3s / Flannel (`flannel-iface: embernet0`) | 6443, 10250 |
| App | CODESYS + Ignition Edge | OPC-UA:4840, HTTP:8088 |

## Container Images

| App | Image | Deployed Via |
|---|---|---|
| CODESYS AMD64 | `codesys/codesys-control` | App Store / HelmChart CRD |
| Ignition Edge | `inductiveautomation/ignition` | App Store / HelmChart CRD |
| EmberNET Endpoint (per-edge host VPN + Ziti session) | `ghcr.io/embernet-ai/embernetlite:0.0.99` | `podman run --network host` (NOT k3s) |

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
