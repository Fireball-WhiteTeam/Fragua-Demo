# Fragua Demo — Deployment Workflow

## Overview
Deploy 2 Azure Ubuntu 24.04 VMs into EmberNet with CODESYS + Ignition Edge.

## Phase Execution Order

### Phase 1: Azure VM Provisioning
- Create resource group `rg-fragua-demo`
- Provision 2x `Standard_B2s` VMs with Ubuntu 24.04 LTS
- Configure NSG (SSH + WireGuard UDP/51820)
- Verify SSH access

### Phase 2: Base OS + embernet-endpoint container (replaces both wg-quick AND in-cluster flux-tunnel)
- Update packages, install nfs-common + podman
- One-time host setup:
  - `mkdir -p /etc/embernet /var/lib/embernet /var/log/embernet /run/embernet`
  - `chown 987:987 /var/lib/embernet /var/log/embernet /run/embernet` (embernet UID)
  - Write `/etc/embernet/env` with `EMBERNET_TENANT_HINT=fragua` and `EMBERNET_SAFETY_WATCHDOG_DISABLED=1` (Azure VM compat)
- Enroll the device against the dashboard (one-time): `podman run --rm -it --network host -v /var/lib/embernet:/var/lib/embernet ghcr.io/embernet-ai/embernetlite:0.0.40 enroll --tenant-id fragua --device-name <hostname>`
- Run the container in `--restart always` mode (full command in `.agent/CREDENTIALS.md` §WireGuard mesh)
- Verify `wg show embernet0` shows fresh handshake + `embernetctl status` shows Flux + WireGuard both `connected`

### Phase 3: K3s Cluster
- VM1: K3s server bound to `embernet0` IP (`flannel-iface: embernet0`)
- VM2: K3s agent joining VM1 over the embernet0 mesh
- Apply embernet.ai/* node labels

### Phase 4: Core Platform
- cert-manager → Longhorn → metrics-server → CoreDNS (verify)
- ghcr-secret in all namespaces
- flux-controller-admin secret

### Phase 5: Flux/Ziti dial policies (cluster-side wiring)
- The embernet-endpoint daemon owns the Ziti client on each node (no `flux-edge-tunnel` DaemonSet anymore)
- Each enrolled device-id needs the `fragua-edge-dial` role attribute set on it (the Fragua dial policies — `fragua-ignition-cloud-dial`, `fragua-anvilmq-mqtt-dial` — include `#fragua-edge-dial` so any device with that attr gets dial access)
- See `.agent/CREDENTIALS.md` §Ziti / Flux endpoints for the policy table

### Phase 6: CODESYS
- Deploy CODESYS AMD64 via App Store / HelmChart CRD
- Verify pods + dashboard proxy access
- Load FraguaV2 project via IDE (manual)

### Phase 7: Ignition Edge (NOT Cloud)
- Deploy Ignition Edge via App Store / HelmChart CRD
- Configure Edge→Cloud Gateway Network connection
- Import FRAGUAV2 Ignition project
- Verify tag flow: CODESYS → Edge → Cloud

### Phase 8: Rancher + Verification
- Import cluster to Rancher as `fragua-demo`
- Full verification checklist

## Critical References
- Deployment scripts: `Fireball-Red-Team/deployment` repo
- Runbooks: `team-operations-manual/runbooks/`
- App Store: `team-operations-manual/engineering/app_store_deployment_flow.md`
- Architecture: `team-operations-manual/engineering/architecture_overview.md`
