# Fragua Tenant Namespace

## Identity
- Namespace: `tenant-fragua-demo`
- Labels: `embernet.ai/tenant=fragua-demo`, `embernet.ai/site=fragua`, `embernet.ai/facility=demo`
- Created: 2026-05-19

## Network Policies (applied)
Source: `team-operations-manual/manifests/tenant-network-policies.yaml`. Provides default-deny + selective allow:

- `default-deny-all` — baseline deny on Ingress + Egress
- `allow-dns` — UDP/TCP 53 egress
- `allow-intra-namespace` — pod-to-pod within the tenant ns
- `allow-dashboard-api` — TCP 8080/443 ingress from `default` ns
- `allow-flux-mesh` — TCP 6262 egress to `flux-system` (Ziti controller)
- `allow-anvilmq` — TCP 1883/8883 egress to `default` ns (MQTT broker)

Compliance tags on the policies: NIST PR.AC-5, IEC 62443 FR5.1, SOC2 CC6.6, ISO 27001 A.13.1.

## Why this is the "tenant" boundary
- Node labels mark each VM as belonging to tenant `fragua-demo` — these labels are read by the EmberNet industrial dashboard once Flux/Ziti enrollment lands (Phase 5)
- The namespace + policies enforce workload isolation at the cluster network layer
- All Fragua workloads (CODESYS, Ignition Edge — Phases 6 and 7) deploy into `tenant-fragua-demo`

## Dashboard-side registration
The cluster appears on `dashboard.embernet.ai` after `flux-edge-tunnel` enrolls (Phase 5). At enrollment, the Ziti identity carries the node labels back to the controller, which is how the dashboard discovers the tenant. There is no manual "create tenant" step on the dashboard.
