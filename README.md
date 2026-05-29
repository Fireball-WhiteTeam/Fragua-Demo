# 🤙 Fragua Demo — EmberNet on a Real Customer Edge

> **Two Azure VMs. One repo. Zero open ports they didn't ask for.**

**Built by:** Patrick Ryan, CTO @ Fireball Industries 🔥

**Stack:** Azure × 2 · Clusters · ArcNet on UDP/443 · Flux overlay · CODESYS Control SL · Ignition Edge 8.3 · EmberNet Network Probe · Rancher import · EmberNet Dashboard tenancy

**Footprint:** Two `Standard_D2as_v4` VMs across two Azure regions. That's the entire customer-side edge.

---

## What This Is

Fragua is a **reference deployment of EmberNet on the smallest justifiable customer-shaped edge** — two VMs, opposite-coast Azure regions, joined to the EmberNet central control plane via Rancher import and the ArcNet management network. Each edge runs a CODESYS PLC runtime + Ignition Edge gateway + Network Probe pod, all dialing back to EmberNet Cloud over the Flux zero-trust overlay.

**Why this repo exists:** If a customer asks *"what does it actually take to stand a site up from cold metal?"*, the answer is this repo plus the four upstream PRs it forced — not a 6-week services engagement.

---

## Architecture

```
                       Internet
                            │
          flux.embernet.ai:443           cdn.embernet.ai:443
          (Flux controller)              (Flux router — SNI passthrough,
                                          looks like a CDN by design)
                            │
                       Azure LB
                            │
                     ┌──────┴──────────────┐
                     │  EmberNet Central   │
                     │  ─────────────────  │
                     │  • Clusters ctrl    │
                     │  • EmberNet         │
                     │    Dashboard        │
                     │  • Ignition Cloud   │
                     │  • Flux controller  │
                     │  • Rancher          │
                     └─────────────────────┘
                            │
            ArcNet mesh ── UDP/443 → hub: embernet003
                            │
            ┌───────────────┴───────────────┐
            │                               │
     fragua-edge-01 (eastus2)        fragua-edge-02 (centralus)
     ───────────────────────          ───────────────────────
     • Clusters server               • Clusters agent
     • flux-edge-tunnel (Flux)       • flux-edge-tunnel (Flux)
     • CODESYS Control SL (Podman)   • CODESYS Control SL (Podman)
     • Ignition Edge 8.3 (Podman)    • Ignition Edge 8.3 (Podman)
     • EmberNet Network Probe        • EmberNet Network Probe
     • Endpoint Controller (TBD)     • Endpoint Controller (TBD)
            │                               │
            ▼                               ▼
     Downstream OT LAN               Downstream OT LAN
     (PLCs, HMIs, drives,            (cell controllers, RTUs,
     sensors, OPC servers —          switches, IPCs —
     the real customer's             the real customer's
     expensive 1996 hardware)        slightly newer 2004 hardware)
```

Two regions on purpose. Region-pair failover is an easier story to tell when the topology is already asymmetric. East + Central maps to where most industrial customers actually live — east-coast HQ, central-US manufacturing floor.

---

## Pre-Requisites — What Must Be Done Before Anyone Touches the Dashboard

All seven of these need to be in place before the user-facing experience kicks in. If one isn't done, the user sees an empty dropdown and wonders where their site went.

| # | What | Owner | Location | Notes |
|---|------|-------|----------|-------|
| 1 | Azure VMs provisioned, ArcNet keys exchanged, both edges peered into the hub | Platform | `deploy/wireguard/` | NSG inbound `UDP/443` for ArcNet, `TCP/443` for Flux. **No UDP/51820 — that fight is over. We use 443.** |
| 2 | Clusters 1.35.4+k3s1 on both edges (server on -01, agent on -02), Longhorn, cert-manager, ghcr-secret replicated | Platform | `deploy/k3s/` | Snapshotter is `overlayfs` — **not** `overlay`. The k3s containerd build does not accept that string. Writing it down so you learn it quietly instead of loudly. |
| 3 | Flux identities enrolled per edge via JWT, flux-edge-tunnel chart `v2.0.8` deployed | Platform | `deploy/flux/` | Identity persisted on **Longhorn RWX**, not local disk, not emptyDir. Pod restarts without this = re-enroll = new JWT = ticket. |
| 4 | Rancher cluster import (cluster id `c-j7gtg`, displayName `Fragua`) | Platform | `deploy/rancher/` | Set `agent-tls-mode=system-store` globally on Rancher **before** importing, or cattle-cluster-agent CrashLoopBackOffs. I already spent that afternoon. You're welcome. |
| 5 | CODESYS Control SL 4.20 + Ignition Edge 8.3.6 via Podman on each edge | Platform | `deploy/codesys/`, `deploy/ignition/` | Podman for this deployment. Once upstream chart PRs land, this can be done via Helm. |
| 6 | EmberNet Dashboard Tenant CR + node labels applied | Platform | `deploy/dashboard/tenant.yaml` | Skip this and the dashboard tenant dropdown stays empty no matter how clean the Rancher import looks. Apply the CR. Roll the dashboard deployment. Move on. |
| 7 | **EmberNet Endpoint Controller** | **Fireball engineering** | Forthcoming Helm chart | Owns downstream OT discovery, claims devices into the tenant, normalizes Modbus/EtherNet-IP/OPC-UA for Ignition and the dashboard. Without this, the edges are just two VMs running PLCs in the void. |

---

## EmberNet Dashboard — User-Facing Flow

User opens `https://dashboard.embernet.ai`, signs in via Azure AD SSO, selects the **Fragua** tenant. From there:

### Global Command *(SuperAdmin only)*
Top of the nav. Every tenant, every downstream cluster, the App Store catalog, fleet-wide ops. Show this to a customer only if you're onboarding them as a partner or you're deliberately flexing.

### Tenant Home
- **Nodes pane** — every Clusters node tagged `embernet.ai/tenant=fragua-demo`. For Fragua that's `fragua-edge-01` (control plane) and `fragua-edge-02` (edge worker). Each card shows CPU/RAM/pod count, last-seen, and the tenant/node-type/onboarded-at labels. If a card looks wrong, the labels are wrong — fix the labels, not the dashboard.
- **Sites + Facilities** — Fragua has one facility, "demo." Larger customers get the full multi-building/multi-cell breakdown via `embernet.ai/building=` / `embernet.ai/cell=`.
- **Apps Deployed** — anything from the App Store running anywhere in the tenant. Click a tile → standard Helm release page → Logs / Shell / Restart.

### Node Detail Panel
Click any node card to open:

- **Live metrics** — CPU, RAM, disk, network via cattle-monitoring + node-exporter
- **Shell** — WebSocket terminal into the node via Rancher's proxy API. Auth flows from Azure AD, so the audit log shows *who* connected — not "shared SSH key #7."
- **Apps on this node** — locally running pods
- **Deploy an App** — App Store filtered to compatible apps for this node. For External tenants like Fragua, deployment is a Fleet Bundle propagating via Rancher Fleet to the imported cluster. The user clicks **Install**. They never need to know what GitOps is.

### Live Data Widgets
Tag-history widgets pull from central InfluxDB (`bucket: industrial_raw`, `org: embernet`). Ignition Edge writes into that bucket via store-and-forward historian over the Flux tunnel.

```
CODESYS runtime → OPC-UA (localhost:4840 on the edge)
       → Ignition Edge tag provider
       → Ignition Edge store-and-forward historian  (buffers locally if offline)
       → Outgoing Gateway Network connection (encrypted Flux tunnel)
       → Ignition Cloud (EmberNet Central, pod in fireball-system)
       → InfluxDB writer
       → Dashboard widgets
```

Customer internet dies? Edge buffers locally on Longhorn-backed PVCs. Connection restored? Historian flushes the queue. No data loss unless the VM disk dies, and Longhorn replicas handle that. **This is by design, not by luck.**

### Alerts + Alarms
Each tenant has scoped alarm visibility. Fragua sees Fragua alarms only — the dashboard enforces tenant filtering at query time even though everything lands in the same InfluxDB bucket. Alarms configured in Ignition (Edge-side for local, Cloud-side for tenant-wide).

---

## CODESYS — Engineer Workflow

CODESYS Control SL runs as a Linux runtime inside a Podman container on each edge. Without a license it cycles in 30-minute demo windows — license it in production.

**Engineers do not SSH into the edge VM to write PLC code.** They sit at a Windows workstation with CODESYS Development System IDE installed (free, `codesys.com`). The runtime is a remote gateway, not a local IDE host.

### One-Time Per Engineer
1. Install CODESYS Development System 3.5.20+ on a Windows workstation.
2. **Gateway address:** edge VM's ArcNet IP — `100.64.0.30` (edge-01) or `100.64.0.31` (edge-02). Workstation needs an ArcNet tunnel to the EmberNet hub or a ZeroTier session. CODESYS speaks its own protocol on `:11740`.
3. **Credentials:** bootstrap creds are in `.agent/CREDENTIALS.md` (gitignored). **Rotate in production.**

### Day-to-Day Workflow
1. Open or create a CODESYS project.
2. **Communications → Scan Network** — edge gateway appears as `Ignition-fragua-edge-01`. Double-click to connect.
3. **Build (F11) → Login (Alt+F8) → Download.** Code is live on the runtime in under 5 seconds for normal-sized projects.
4. Watch window to verify tags. Online change works — don't be afraid of it.
5. Expose tags to Ignition by marking variables as **OPC-UA visible** in the symbol config. They appear under the OPC-UA server namespace, ready for Ignition to consume.

### What CODESYS Gives You
- **IEC 61131-3** — full language set: ST, FBD, LD, SFC, IL. Mix them.
- **Soft-PLC scan times under 1ms** — as long as you stay out of cyclic tasks with file IO.
- **Fieldbus support** — Modbus / EtherNet-IP / Profinet / EtherCAT at scan-cycle rate once the Endpoint Controller wires the downstream side.
- **Built-in HMI (CODESYS Visualization)** — skip it. Use Ignition Perspective. CODESYS Vis has not aged well and I will die on this hill.

### CODESYS ↔ Ignition
- OPC-UA at `opc.tcp://localhost:4840` on the same host. Ignition Edge's OPC-UA Module is configured at install to consume that endpoint — tags flow automatically.
- Modbus / S7 / EtherNet-IP also exposed by CODESYS as native protocols for downstream devices. Bridging those upstream into the dashboard device inventory is the Endpoint Controller's job.

---

## Ignition Edge ↔ Ignition Cloud — How It Actually Works

Ignition Edge on each Fragua VM is a **Gateway Network outbound dialer** to Ignition Cloud (`fireball-system` namespace, EmberNet Central).

- **Outgoing connection target:** `100.65.0.1:8060` — the Flux synthetic IP for the `ignition-cloud` overlay service. The flux-edge-tunnel pod intercepts via iptables tproxy, routes through the Flux tunnel out to `cdn.embernet.ai:443`, where Traefik does SNI passthrough into the Flux router → cp005 → Ignition Cloud K8s service. Six hops. They all work.
- **No SSL on the inner connection.** Flux handles end-to-end encryption. Double-wrapping TLS just burns CPU.
- **No inbound firewall exposure on the OT side.** Customer firewall needs outbound **UDP/443** (ArcNet mgmt) and **TCP/443** (Flux dial). That's it.

First successful Edge → Cloud handshake triggers a backfill of whatever sat in store-and-forward while unconnected. Subsequent cycles are deltas. The pipeline self-heals.

---

## Quick Reference

| Resource | Address |
|----------|---------|
| EmberNet Dashboard | `https://dashboard.embernet.ai` |
| Rancher | `https://clusters.embernet.ai` (cluster `c-j7gtg` = Fragua) |
| Flux Controller | `flux.embernet.ai:443` |
| Flux Router (data plane) | `cdn.embernet.ai:443` |
| Ignition Cloud K8s svc | `ignition-cloud.fireball-system.svc.cluster.local:8060` |
| Ignition Edge UI (edge-01) | `http://20.80.241.221:8088` |
| Ignition Edge UI (edge-02) | `http://52.176.39.25:8088` |
| CODESYS Gateway | `:11740` on ArcNet IPs `100.64.0.30` / `100.64.0.31` |
| CODESYS OPC-UA | `:4840` on ArcNet IPs `100.64.0.30` / `100.64.0.31` |
| Network Probe | `fireball-system` namespace, `:8080` via Flux to dashboard |
| Provisioner | `https://provisioner.embernet.ai/api/v1/provision` |
| Credentials | `.agent/CREDENTIALS.md` — gitignored, do not commit, do not paste in Slack |

---

## Upstream PRs This Deployment Produced

Standing this up surfaced four bugs that got fixed and pushed back so the next site doesn't eat them.

- **`Embernet-ai/embernet-provisioner#1`** — Five runtime fixes: httpx 0.27 `verify=` kwarg removal, wg-easy v15 `id`-on-create response shape change, wg-easy `/api/session` 404 handling, OpenVPN sidecar 500 handling, idempotent authenticator-cleanup across pod restarts.
- **`Embernet-ai/Ignition-Edge-Pod#1`** — Opt-in self-enrollment via the provisioner (`provisioner.enabled=true`), using the same init-container pattern as the probe chart for consistency.
- **`Embernet-ai/Codesys-AMD-64-x86#1`** — Install hardening: equivs-built `codemeter-lite` shim, removed the `apt-get install -f -y` fallback that *silently* removed `codesyscontrol`, added hard post-install assertion the binary exists. **⚠️ This same fix needs to be backported to the `cp02` Containerfile** — every cp02 deployment to date is silently running `sleep infinity` with no PLC runtime present.
- **Phase 7b end-to-end runbook** — Full ArcNet / iptables / Flux router / cert-SAN / Traefik SNI passthrough recipe at `deploy/PHASE-7B-RUNBOOK.md`.

---

## Docs

| Doc | Purpose |
|-----|---------|
| [`deploy/AUDIT.md`](deploy/AUDIT.md) | Phase-by-phase status + every gotcha, how it was fixed |
| [`deploy/PHASE-7B-RUNBOOK.md`](deploy/PHASE-7B-RUNBOOK.md) | Flux overlay end-to-end |
| [`deploy/wireguard/ENGINEER-FIX.md`](deploy/wireguard/ENGINEER-FIX.md) | ArcNet convention writeup — read before touching `wg0.conf` |
| [`deploy/wireguard/AWS-RECOVERY.md`](deploy/wireguard/AWS-RECOVERY.md) | Cross-cloud mesh recovery |
| [`deploy/CLICKUP-UPDATE.md`](deploy/CLICKUP-UPDATE.md) | Status update at handoff |
| [`deploy/dashboard/tenant.yaml`](deploy/dashboard/tenant.yaml) | Tenant CR — apply this to make a new site appear in the dashboard |
| [`deploy/charts/embernet-probe-1.2.1/`](deploy/charts/embernet-probe-1.2.1/) | Probe chart fork with self-enroll baked in |
| [`INDEX.md`](INDEX.md) | Full resource lookup table |

Open this README for the narrative. Open `INDEX.md` for the lookup table.

---

*Two VMs. One repo. Everything else is documented. If you're reading this, you're either onboarding a site, auditing the deployment, or both at 3 AM. Either way — welcome.*

— **Patrick** 🤙
