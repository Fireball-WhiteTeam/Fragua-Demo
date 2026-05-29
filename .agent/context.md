# Fragua Demo — Agent Context

## Project Identity
- **Project:** Fragua V2 Demo
- **Type:** Industrial ICS/SCADA demo deployment
- **Site Slug:** `fragua`
- **Tenant:** `fragua-demo`
- **CODESYS Version:** V3.5 SP22 Patch 1+

## What This Repo Contains
- `FraguaV2.project` — CODESYS project (building automation: HVAC, lighting, energy metering, VFD control)
- `FraguaV2.Device.Application.xml` — OPC-UA symbol configuration (77 tags exposed)
- `FRAGUAV2/` — Ignition project (Perspective + Vision modules) for HMI/SCADA visualization

## Infrastructure Target
- 2 Azure Ubuntu 24.04 VMs (`fragua-edge-01`, `fragua-edge-02`)
- Each runs CODESYS + Ignition Edge in Podman containers
- K3s cluster (VM1=server, VM2=agent) joined to EmberNet via WireGuard + Flux
- Edge instances connect back to Ignition Cloud on EmberNet control plane
- Managed from `dashboard.embernet.ai`

## Key Constraints
- **Ignition Edge ONLY** — No Ignition Cloud or Standard on these VMs
- **Podman containers** — Not bare-metal installs
- **EmberNet managed** — Visible in the dashboard, Flux tunnel connected
- **CODESYS ↔ Ignition Edge** — OPC-UA communication between CODESYS runtime and Ignition Edge
- **Ignition Edge → Cloud** — Gateway Network connection to central Ignition Cloud

## Documentation
- See `documentation_index.md` in the conversation artifacts for full doc map
- Primary runbooks: EDGE_NODE_JOIN.md, FLUX_EDGE_TUNNEL_DEPLOYMENT.md, SITE_PROVISIONING_CHECKLIST.md
- App deployment: app_store_deployment_flow.md
- All in `team-operations-manual` repo

## Tag Structure (from Application.xml)
All tags are under `Application.PLC_PRG`:
- **HVAC:** HVAC1_*, HVAC2_* (temp, SP, hysteresis, output, on/off)
- **Lighting:** Luz1–Luz8 (on/off + enable), Noche (night mode), _Luces (count)
- **Energy:** KW, KVA, KVAR, KWH, KVARH, FP, VL1N–VL3N, VL1L2–VL3L1
- **Alarms:** Alta_*, Baja_* (high/low temp alarms with SP and hysteresis)
- **VFD:** VFD_Hz, VFD_On
- **Misc:** Hora (time), _Carga (load), _Timestamp
