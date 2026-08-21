# Fragua Demo: Architecture Reference

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
│   - flux.embernet.ai:443 (Flux/Ziti Controller)       │
│   - Ignition Cloud — MULTI-TENANT, k3s on embernet001 │
│     ignition-cloud.fireball-system.svc:8088/:8060     │
│   - AnvilMQ — MQTT broker, overlay 100.65.0.2:1883    │
│     (Sparkplug B fan-in from all edges)               │
└────────────────────┬─────────────────────────────────┘
                     │ Flux Zero-Trust Overlay (Ziti) — PRIMARY
                     │ WireGuard (100.64.0.0/24) — ranked below it
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
│    host, 2.0.3)     │    │    host, 2.0.3)      │
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

> **2026-06-04 architecture change.** The legacy `wg-quick@embernet0` systemd unit + `embernet-wg-watchdog.timer` + in-cluster `flux-tunnel-fragua-edge-*` DaemonSet pods were ALL replaced by a single per-edge **embernet-endpoint container** running on the host with `--network host`. The container owns the `embernet0` netlink interface (kernel WG via wgctrl) and the loopback API at `127.0.0.1:8765`. K3s flannel-iface stays bound to `embernet0`, flannel doesn't care who created the interface.

> **2026-07-22 — data plane RESTORED; model reworked to Sparkplug-via-AnvilMQ.**
> The Ziti data-plane gap logged on 2026-07-20 (embernet-endpoint held only an API
> session; `100.65.0.x` had no route) has been resolved by Patrick. The synthetic
> overlay IPs are reachable from both edges again, so the edge→broker and edge→cloud
> dials succeed. With transport live, the canonical data path is **no longer Gateway
> Network** — it is **MQTT / Sparkplug B fanned in through AnvilMQ into the
> multi-tenant Ignition Cloud** (see Data Flow below). Gateway Network's 1:1 gateway
> trust does not scale to a multi-tenant cloud; a shared Sparkplug broker is the
> designed many-edges→one-gateway fan-in.
>
> _History retained for context: enrollment had silently never completed from
> 2026-06-01 (raw OTT, `authenticators: 0`, masked by a stub flux driver ≤v0.0.47)
> until both edges re-enrolled on v0.0.99 (2026-07-20, `authenticators: 1 [cert]`,
> `hasApiSession: True`); the `flux-edge-tunnel` DaemonSet that originally served the
> `100.65.0.x` tproxy had been deleted in the 2026-06-04 cutover._

## Data Flow (reworked 2026-07-22 — Sparkplug B via AnvilMQ → multi-tenant cloud)

The edges are **data producers only**; the multi-tenant Ignition Cloud on
`embernet001` is the single consumer. Tenant isolation is by Sparkplug group
namespace (`groupId = fragua`), not by per-gateway trust.

```
 REAL TAGS                                    SIMULATED TAGS (demo)
 CODESYS Runtime (PLC_PRG, 77 tags)           emberburn  (sim generator;
     │ OPC-UA  (local, in-pod)                 chart rework underway —
     ▼                                         NOT yet deployable)
 Ignition Edge  (fragua-edge-01 / -02)              │
     │ MQTT Transmission — Sparkplug B                │ Sparkplug B
     │ report-by-exception                            │ spBv1.0/fragua/…
     │ groupId=fragua, edgeNodeId=<edge>              │
     ▼                                                ▼
 ┌──────────────────────────────────────────────────────────┐
 │  AnvilMQ  — MQTT broker (the pipe)                        │
 │  Flux/Ziti overlay  100.65.0.2:1883                       │
 │  fans every producer into one spBv1.0/fragua/# namespace  │
 └───────────────────────────┬──────────────────────────────┘
                             │ MQTT Engine subscribes spBv1.0/fragua/#
                             ▼
 ┌──────────────────────────────────────────────────────────┐
 │  Ignition Cloud  — MULTI-TENANT                           │
 │  k3s workload on embernet001, ns fireball-system          │
 │  svc ignition-cloud.fireball-system.svc:8088 (web) / :8060 │
 │  one gateway, N tenants separated by Sparkplug groupId     │
 └───────────────────────────┬──────────────────────────────┘
                             │ internal (tag history / providers)
                             ▼
 EmberNET Dashboard  (dashboard.embernet.ai — trends, alarms, tag widgets)
```

**Why MQTT/Sparkplug replaces Gateway Network here.** Gateway Network is a
point-to-point gateway-trust link (the four-field outgoing-connection wire-up in
`WHITE-TEAM-IGNITION-HANDOFF.md`). That is a 1:1 relationship and does not fan in
cleanly to a shared multi-tenant cloud. Sparkplug B through a shared AnvilMQ broker
is report-by-exception, store-and-forward on the edge, and namespaced per tenant —
the correct many-edges→one-gateway shape, and a *lighter* edge leg than GW Network's
full tag-provider mirroring + remote back-probes.

**emberburn** publishes simulated Sparkplug tags into the same AnvilMQ namespace, so
the cloud + dashboard light up with realistic data before (or alongside) live CODESYS
tags. Its chart is being reworked under the CHART-CONTRACT §9 alignment and cannot be
deployed yet.

## Network Layers

> ### ⚠️ `100.65.0.2` (AnvilMQ) sits INSIDE the router's DNS intercept pool
>
> This records live state, not a recommendation. The router hands `100.65.0.0/16` out
> dynamically to DNS-named services, so a *static* intercept address inside that range is
> a collision waiting to happen — and it did: allocating `fragua-k3s-api.flux.internal`
> handed out `100.65.0.2`, AnvilMQ's address, on the very first DNS name.
>
> Static service addresses belong in the tenant's own service block, outside the pool.
> Reservations live in `embernet-iac/templates/app/flux-address-reservations.tsv`. Moving
> AnvilMQ is a live change and has not been done — do not "fix" it in this document alone.
>
> The related rule that is NOT negotiable: `--dnsSvcIpRange 100.65.0.0/16`, never
> `100.64.0.0/10`. A `/10` swallows the WireGuard range.

| Layer | Technology | Ports |
|---|---|---|
| Underlay | Azure VNet / Public IP | SSH:22 (admin), UDP/443 (WG mgmt) |
| **Mesh — PRIMARY** | Flux/Ziti overlay via embernet-endpoint — **data plane restored 2026-07-22**; `100.65.0.x` synthetic IPs routable from edges | `vpn.embernet.ai:443`, `flux.embernet.ai:443`, `cdn.embernet.ai:443` |
| VPN — ranked below Flux | WireGuard via embernet-endpoint daemon. Not the preferred path: it is UDP and needs a port the site permits, while Flux rides outbound 443 | `embernet0` iface, 100.64.2.0/24 (Fragua) |
| Overlay data | Sparkplug B (MQTT) edge → AnvilMQ → cloud | AnvilMQ `100.65.0.2:1883`; ignition-cloud `100.65.0.1:8060` |
| K8s | K3s / Flannel (`flannel-iface: embernet0`) | 6443, 10250 |
| App | CODESYS + Ignition Edge (+ emberburn sim) | OPC-UA:4840, HTTP:8088, MQTT:1883 |

## Container Images

| App | Image | Deployed Via |
|---|---|---|
| CODESYS AMD64 | `codesys/codesys-control` | App Store / HelmChart CRD |
| Ignition Edge | `ghcr.io/embernet-ai/ignition-edge:8.3.8` | App Store / HelmChart CRD (chart v1.1.0) |
| EmberNET Endpoint (per-edge host VPN + Ziti session) | `ghcr.io/embernet-ai/embernetlite:2.0.3` (or `:stable`) | `podman run --network host` (NOT k3s) |

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
