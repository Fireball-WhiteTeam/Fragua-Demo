# Fragua Demo: Deployment Workflow

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
- Enroll the device against the dashboard (one-time). **Use v0.0.48+, earlier versions
  write the OTT token but never exchange it for a certificate**, leaving the node
  permanently unenrolled while still reporting `connected`:
  ```
  podman run -d --name embernet-enroll --network host \
      --env-file /etc/embernet/env \
      -v /etc/embernet:/etc/embernet:ro -v /var/lib/embernet:/var/lib/embernet \
      --entrypoint embernetlite ghcr.io/embernet-ai/embernetlite:0.0.99 \
      enroll -tenant-id fragua -device-name <hostname>
  ```
  Flags are single-dash (`-tenant-id`, not `--tenant-id`). This is an **Azure AD
  device-code flow**: read the `user_code` out of `podman logs embernet-enroll` and
  complete the sign-in at <https://login.microsoft.com/device>. Run detached, the
  container blocks until someone signs in.
- Run the container in `--restart always` mode (full command in `.agent/CREDENTIALS.md` §WireGuard mesh)
- Verify enrollment actually completed: **do not trust `state: connected` alone**:
  - `/var/lib/embernet/identity/embernet.json` must be a multi-KB **JSON** blob
    containing `ztAPI` / `id` / `key`. If it is ~1 KB and starts with `eyJ`, it is
    still a raw JWT and the node is NOT enrolled.
  - Controller-side the identity must show `authenticators: 1 -> ['cert']` and
    `hasApiSession: True`.
- Verify `wg show embernet0` shows a fresh handshake + non-zero transfer counters

### Phase 3: K3s Cluster
- VM1 (`fragua-edge-01`, 100.64.2.2): K3s **control plane + etcd**, bound to the
  `embernet0` IP (`flannel-iface: embernet0`)
- VM2 (`fragua-edge-02`, 100.64.2.1): K3s **agent**, joining `https://100.64.2.2:6443`
  over the embernet0 mesh
- Apply embernet.ai/* node labels
- Live configs for both nodes are mirrored in `deploy/k3s/*.config.yaml`

### Phase 4: Core Platform
- cert-manager → Longhorn → metrics-server → CoreDNS (verify)
- ghcr-secret in all namespaces
- flux-controller-admin secret

### Phase 5: Flux/Ziti dial policies (cluster-side wiring)
- The embernet-endpoint daemon holds the Ziti **API session** on each node (no
  `flux-edge-tunnel` DaemonSet anymore)
- Each enrolled device-id needs the `fragua-edge-dial` role attribute set on it (the Fragua dial policies (`fragua-ignition-cloud-dial`, `fragua-anvilmq-mqtt-dial`) include `#fragua-edge-dial` so any device with that attr gets dial access)
- See `.agent/CREDENTIALS.md` §Ziti / Flux endpoints for the policy table

> ⚠️ **Not sufficient on its own (as of 2026-07-20).** Role attributes + dial policies
> grant *authorization* to dial, but embernet-endpoint provides no traffic intercept,
> it is an SDK client, not a tunneler. The `100.65.0.x` synthetic IPs that Phase 7
> depends on are therefore absent, and `hasEdgeRouterConnection` is `False` on both
> edges. See the 2026-07-20 correction in `architecture-reference.md`. Phase 7's
> Edge→Cloud step cannot pass until this is resolved.

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
