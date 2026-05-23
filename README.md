# Fragua — EmberNet Industrial Platform Reference Deployment

> **Author:** Patrick Ryan, CTO — Fireball Industries
> **Stack:** Azure × 2, K3s, WireGuard, OpenZiti (Flux), CODESYS Control SL, Ignition Edge 8.3, EmberNet Network Probe, Rancher import, Industrial Dashboard tenancy
> **Footprint:** Two `Standard_D2as_v4` VMs across two Azure regions. That's the entire customer-side edge.

---

This repo is the deployment artifact + audit trail for **Fragua**, a Fireball-customer-shaped reference deployment of the EmberNet platform onto a deliberately tiny edge footprint. Two Azure VMs, opposite-coast regions, joined into the EmberNet central control plane via Rancher import and the ArcNet (WireGuard) management network. Each VM runs a CODESYS PLC runtime + Ignition Edge gateway + Network Probe pod, all reaching back to EmberNet Cloud over the Flux/OpenZiti zero-trust overlay.

It exists because every prior site-onboarding doc we had read like "step 1: ssh in, step 2: do an undocumented dance for 90 minutes, step 3: discover the platform CRD changed two minor versions ago." If a customer asks what it takes to stand a Fragua-shaped edge up from cold metal, the answer is: this repo, plus the chart upgrades it forced upstream (provisioner + probe + Ignition Edge + CODESYS — PRs filed back into `embernet-ai/*`).

Demo customer name "Fragua" is a placeholder. Pick whoever you're selling to next and re-skin the tenant.

## What's actually wired up

```
                       Internet (public)
                              │
              flux.embernet.ai:443           cdn.embernet.ai:443
              (Ziti controller)              (Ziti router, SNI passthrough)
                              │
                         Azure LB
                              │
                       ┌──────┴───────────────┐
                       │  EmberNet Central    │
                       │  ───────────────     │
                       │  • K3s control plane │
                       │  • Industrial        │
                       │     Dashboard        │
                       │  • Ignition Cloud    │
                       │  • Flux controller   │
                       │  • Rancher           │
                       └──────────────────────┘
                              │
            WireGuard mesh ── UDP/443 → hub: embernet003
                              │
              ┌───────────────┴───────────────┐
              │                               │
       fragua-edge-01 (eastus2)         fragua-edge-02 (centralus)
       ───────────────────────         ───────────────────────
       • K3s server                    • K3s agent
       • flux-edge-tunnel (Ziti)       • flux-edge-tunnel (Ziti)
       • CODESYS Control SL (Podman)   • CODESYS Control SL (Podman)
       • Ignition Edge 8.3 (Podman)    • Ignition Edge 8.3 (Podman)
       • EmberNet Network Probe        • (probe + endpoint controller)
       • Endpoint Controller (TBD)     • Endpoint Controller (TBD)
              │                               │
              ▼                               ▼
       Downstream OT LAN              Downstream OT LAN
       (real customer site:           (real customer site:
       PLCs, HMIs, drives,             cell controllers, RTUs,
       sensors, OPC servers)           switches, IPCs)
```

Two regions on purpose. Region-pair failover stories are easier to tell when the topology is asymmetric. Centralus + eastus2 also happens to be the path most of our existing customers fall on — east coast HQ, central US manufacturing.

## What you have to install BEFORE the user can do anything

By the time anyone hits the dashboard expecting a populated tenant view, all of the following have already happened. Your engineers don't open tickets for these; they own them.

| # | Thing | Owner | Where it lives | Notes |
|---|---|---|---|---|
| 1 | Azure VMs provisioned, WG keys exchanged, both edges peered into the hub | Platform | `deploy/wireguard/` | Two configs, hub peer additions, NSG inbound `UDP/443` for WG, `TCP/443` for Flux. |
| 2 | K3s 1.35.4+k3s1 on both edges (server on -01, agent on -02), Longhorn, cert-manager, ghcr-secret replicated | Platform | `deploy/k3s/` | Use `overlayfs` snapshotter. NOT `overlay`. The k3s containerd build does not accept that string. We learned the hard way. |
| 3 | Flux/Ziti identities enrolled per edge via JWT, flux-edge-tunnel chart `v2.0.8` deployed | Platform | `deploy/flux/` | Identity persisted on Longhorn RWX so the pod survives restart without re-enroll. |
| 4 | Rancher cluster import (cluster id `c-j7gtg`, displayName `Fragua`) | Platform | `deploy/rancher/` | Set `agent-tls-mode=system-store` globally on Rancher OR the cattle-cluster-agent will CrashLoopBackOff against K3s. |
| 5 | CODESYS Control SL 4.20 + Ignition Edge 8.3.6 deployed via Podman on each edge | Platform | `deploy/codesys/`, `deploy/ignition/` | Either Podman (current Fragua-Demo) or via the helm charts in `deploy/charts/` (use those once the upstream PRs land). |
| 6 | Industrial Dashboard Tenant CR + node labels | Platform | `deploy/dashboard/tenant.yaml` | Without the Tenant CR you import into Rancher fine but never appear in the dashboard tenant dropdown. Apply the CR + roll the dashboard deployment to pick it up. |
| 7 | **EmberNet Endpoint Controller** | **Fireball engineering** | Forthcoming helm chart | Lands on each edge as a container or DaemonSet. Owns downstream OT network discovery, claims devices into the tenant, normalizes them as Modbus/EtherNet-IP/OPC-UA/etc., publishes them as Ignition tags + dashboard inventory rows. This is the piece that turns "two random VMs running PLCs" into "a customer site the dashboard actually knows about." |

Once all seven are in place, the user-facing experience kicks in.

## How a user interacts with this — Dashboard side

The user opens `https://dashboard.embernet.ai`, signs in via Azure AD SSO, and the dashboard drops them at the tenant selector. They pick **Fragua (Demo)** (or whatever you re-skinned the tenant to). What they see and what they can do:

### Global Command (SuperAdmin only)
Top of the nav. Shows all tenants, downstream clusters, App Store catalog, fleet-wide ops. If you're showing this to a customer, you're either inviting them in as a partner or you're flexing. Both legitimate.

### Tenant home
Once a tenant is selected, the home view shows:

- **Nodes pane** — every K3s node that has `embernet.ai/tenant=fragua-demo` labeled. For Fragua: `fragua-edge-01` (control plane) and `fragua-edge-02` (edge worker). Each card shows CPU/RAM/pod count, last-seen, the tenant-name + node-type + onboarded-at labels you wrote in.
- **Sites + facilities** — Fragua has one facility, "demo". You can break it down further (`embernet.ai/building=`, `embernet.ai/cell=`) if the customer has a multi-building layout. Trane has 200+. Fragua has one.
- **Apps deployed** — anything from the App Store that's running anywhere in the tenant. Click a tile, you get the standard helm release page + a "Logs" / "Shell" / "Restart" trio.

### Click a node
A node card opens a side panel with:

- Live metrics (CPU, RAM, disk, network — pulled from cattle-monitoring stack + node-exporter)
- **Shell button** — opens a WebSocket terminal session into the node via Rancher's proxy API. Authentication carries through from Azure AD, so the customer's audit log shows who SSH'd in. No password reuse, no shared keys.
- **Apps on this node** — list of pods running locally
- **Deploy an App** — opens the App Store filtered to "compatible with this node" (arch, kernel, edition, resource budget). Click `Ignition Edge`, `nodered`, `n8n`, `Grafana-Loki`, whatever. Behind the scenes, for External tenants (like Fragua), the deployment is a Fleet Bundle that propagates through Rancher Fleet down to the imported cluster. The user doesn't see Fleet. They click "Install."

### Live data widgets
The dashboard's tag-history widgets pull from the central InfluxDB (bucket `industrial_raw`, org `embernet`). Ignition Edge on each Fragua VM is writing into that bucket via the store-and-forward historian over the Flux/Ziti tunnel. Drag a tag onto a widget, you get a 1-minute / 1-hour / 30-day historical chart with no extra setup. The pipeline is:

```
CODESYS runtime → OPC-UA (localhost:4840 on the edge)
       → Ignition Edge tag provider (consumes the OPC-UA tags)
       → Ignition Edge store-and-forward historian (buffers locally if offline)
       → Outgoing Gateway Network connection (encrypted Ziti tunnel)
       → Ignition Cloud (running in EmberNet Central as a pod)
       → InfluxDB writer
       → Dashboard widgets
```

If the customer's internet is out, the Edge buffers locally on Longhorn-backed PVCs. When it comes back up, the historian flushes the queue. You don't lose data unless the VM disk dies, and you have Longhorn replicas to address that.

### Alerts + alarms
Each tenant has scoped alarm visibility. Fragua sees Fragua alarms only — even though everything's in the same InfluxDB bucket, the dashboard enforces the tenant filter at query time. Alarms are configured in Ignition (Edge-side for local, Cloud-side for tenant-wide).

## How a user interacts with this — CODESYS side

CODESYS Control SL is the Linux runtime sitting inside a Podman container on each edge. Demo mode runs in a 30-minute cycle when no license is installed; activate a license in production. The OPC-UA server inside the runtime listens on `:4840` on the host network (the container is `--network=host`).

Engineers do not log into the edge VM to write PLC code. They sit at a Windows workstation with the CODESYS Development System IDE installed (also free).

### Setup, one-time per engineer
1. Install CODESYS Development System 3.5.20+ on a Windows workstation.
2. **Gateway address:** point at the edge VM's WireGuard IP. For Fragua that's `100.64.0.30` (edge-01) and `100.64.0.31` (edge-02). The engineer's workstation needs a WG tunnel back to the EmberNet hub OR a ZeroTier session — whichever your customer's IT team is comfortable with. The dashboard's Shell session doesn't help here; CODESYS speaks its own protocol on `:11740`.
3. **Username / password:** the gateway's bootstrap credentials. We document the demo values in `.agent/CREDENTIALS.md` (gitignored, don't share). Rotate these in production.

### Day-to-day workflow
1. Open or create a CODESYS project.
2. Communications → Scan Network → the edge VM gateway shows up as `Ignition-fragua-edge-01` or similar (whatever hostname is set inside the runtime). Double-click to connect.
3. Build (F11) → Login (Alt+F8) → Download. Code is live on the runtime in <5 seconds for normal-sized projects.
4. Use the watch window to verify tags. Online change works.
5. Once you're happy, expose the variables you want to share with Ignition by tagging them as **OPC-UA visible** in the symbol configuration. They appear under the OPC-UA server's namespace.

### What CODESYS gives you that's actually useful for industrial work
- **IEC 61131-3** full set of languages (ST, FBD, LD, SFC, IL) — you're not stuck with one
- **Soft-PLC scan times** of <1ms when you stay off the GIL-equivalent (i.e., don't do file IO from a cyclic task)
- **Field bus support** — when the Endpoint Controller wires up Modbus / EtherNet/IP / Profinet / EtherCAT downstream, the runtime can poll them at scan-cycle rate. CODESYS isn't OPC-UA-only.
- **Visualization** — there's a built-in HMI (CODESYS Visualization). Honestly: skip it. Use Ignition Perspective for HMI. CODESYS Vis hasn't aged well.

### Where it talks to Ignition
- OPC-UA at `opc.tcp://localhost:4840` on the same host. Ignition Edge's OPC UA Module is configured at install time to consume that endpoint. Tags flow Ignition-side automatically.
- Modbus, S7, EtherNet/IP, etc. are also exposed by CODESYS as native protocols if you have downstream slaves. The Endpoint Controller's whole job is to bridge those upstream into the dashboard's device inventory.

## How the Ignition Edge ↔ Ignition Cloud relationship works

Ignition Edge on each Fragua VM is a **Gateway Network outbound dialer** to Ignition Cloud (running as a Kubernetes pod in EmberNet Central, namespace `fireball-system`). Configuration on the Edge side:

- **Outgoing Connection target:** `100.65.0.1:8060` — that's the Ziti synthetic IP for the `ignition-cloud` overlay service. The Fragua flux-edge-tunnel pod intercepts that TCP destination via iptables tproxy and routes the connection through the Ziti tunnel out to `cdn.embernet.ai:443`, where traefik does SNI passthrough into the flux-router and then on to cp005, which terminates the inner connection at the actual Ignition Cloud K8s service.
- **No SSL on the inner connection.** The Ziti overlay handles encryption end-to-end. Layering TLS on top inside the tunnel is double-encryption and burns CPU for no benefit.
- **No outbound port exposure on the OT firewall side.** The only outbound the customer firewall has to allow is UDP/443 (the WireGuard mgmt tunnel) and TCP/443 (the Ziti dial path). If their firewall doesn't allow outbound 443 they have bigger problems than not running EmberNet.

Once Edge dials Cloud and Cloud approves the incoming connection (auto-approve is configurable; manual is safer for new sites), tag history starts flowing. The first cycle is usually a flood as the Edge backfills whatever sat in its store-and-forward queue while it was unconnected. Subsequent cycles are deltas.

## Quick-reference

| Thing | Address |
|---|---|
| Industrial Dashboard | `https://dashboard.embernet.ai` |
| Rancher | `https://clusters.embernet.ai` (cluster `c-j7gtg` = Fragua) |
| Ziti controller (mgmt + client auth) | `flux.embernet.ai:443` |
| Ziti router (data plane) | `cdn.embernet.ai:443` — looks like a CDN by design |
| Ignition Cloud K8s svc | `ignition-cloud.fireball-system.svc.cluster.local:8060` |
| Ignition Edge web UI | `http://20.80.241.221:8088` (fragua-edge-01) / `http://52.176.39.25:8088` (fragua-edge-02) |
| CODESYS gateway | `:11740` on each edge's WG IP (100.64.0.30 / 100.64.0.31) |
| CODESYS OPC-UA | `:4840` on each edge's WG IP |
| Network Probe | runs in K3s namespace `fireball-system`, exposes `:8080` via Ziti to dashboard |
| Provisioner (self-enroll JWT issuer) | `https://provisioner.embernet.ai/api/v1/provision` |
| Credentials | `.agent/CREDENTIALS.md` (gitignored, don't share) |

## What this repo also produced (lessons-learned, upstream)

Standing this up surfaced four upstream issues that we fixed and pushed back so the next site doesn't eat the same bugs:

- `Embernet-ai/embernet-provisioner#1` — **five** runtime fixes (httpx 0.27 verify-kwarg removal, wg-easy v15 `id`-on-create response shape change, wg-easy `/api/session` 404, OpenVPN sidecar 500, idempotent authenticator-cleanup across pod restarts)
- `Embernet-ai/Ignition-Edge-Pod#1` — opt-in self-enrollment via the provisioner (`provisioner.enabled=true`) using the same init-container pattern as the probe chart
- `Embernet-ai/Codesys-AMD-64-x86#1` — install hardening: equivs-built `codemeter-lite` shim, drop the `apt-get install -f -y` fallback that silently removed `codesyscontrol`, hard post-install assertion that the binary exists. Same fix needed to be backported to the `cp02` Containerfile — every cp02 deployment to date is silently running `sleep infinity` with no PLC runtime present. Yes, really.
- Phase 7b — full WireGuard / iptables / Ziti router / cert-SAN / traefik SNI passthrough recipe documented at `deploy/PHASE-7B-RUNBOOK.md`. The "blocked on Ziti admin" framing from prior sites was wrong; the work is doable end-to-end from this repo's tooling.

Documents in this repo worth reading before doing the next site:

- `deploy/AUDIT.md` — phase-by-phase status + every gotcha we hit and how we fixed it
- `deploy/PHASE-7B-RUNBOOK.md` — Ziti overlay setup, top to bottom
- `deploy/wireguard/ENGINEER-FIX.md` and `AWS-RECOVERY.md` — WG convention writeups for the cross-cloud mesh
- `deploy/CLICKUP-UPDATE.md` — status update at handoff time
- `deploy/dashboard/tenant.yaml` — Tenant CR to make a new site visible in the dashboard
- `deploy/charts/embernet-probe-1.2.1/` — fork of the upstream probe chart with the self-enroll pattern

---

*Fragua is what we point at when someone asks "show me what a real customer edge looks like" and we want to answer without booking 90 minutes. Two VMs. One repo. Everything else is documented.*

— Patrick
