# Fragua Demo — Deployment Workflow

## Overview
Deploy 2 Azure Ubuntu 24.04 VMs into EmberNet with CODESYS + Ignition Edge.

## Phase Execution Order

### Phase 1: Azure VM Provisioning
- Create resource group `rg-fragua-demo`
- Provision 2x `Standard_B2s` VMs with Ubuntu 24.04 LTS
- Configure NSG (SSH + WireGuard UDP/51820)
- Verify SSH access

### Phase 2: Base OS + WireGuard
- Update packages, install nfs-common + wireguard + podman
- Configure WireGuard peers with embernet003
- Assign IPs from 100.64.0.0/24
- Verify handshake

### Phase 3: K3s Cluster
- VM1: K3s server bound to WG IP (--snapshotter=native)
- VM2: K3s agent joining VM1
- Apply embernet.ai/* node labels

### Phase 4: Core Platform
- cert-manager → Longhorn → metrics-server → CoreDNS (verify)
- ghcr-secret in all namespaces
- flux-controller-admin secret

### Phase 5: Flux Edge Tunnel
- Generate Ziti JWTs from Flux management API
- Deploy flux-edge-tunnel v2.0.8+ per node
- Verify tunnel pods + dashboard visibility

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
