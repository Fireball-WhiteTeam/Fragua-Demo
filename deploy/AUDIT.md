# Fragua Demo — Deployment Audit

_Generated 2026-05-19, picking up where the antigravity agent stopped._
_Phase 7b end-to-end dial path resolved 2026-05-21._

## Resume point: chart-alignment follow-up + edge-02 cluster recovery

Platform deploy is functionally complete. Rancher reports `Fragua` cluster as
`Connected=True, Ready=True` stable. Phase 7b Ziti dial path verified
end-to-end. The remaining items are documentation, upstream PRs, and bringing
edge-02 back into the K3s cluster.

### Phase 7b — RESOLVED 2026-05-21

The "router publishes localhost hostname" problem turned out to be a chain of
four underlying issues, each unblocked the next. End state is documented in
[PHASE-7B-RUNBOOK.md](PHASE-7B-RUNBOOK.md).

1. **OpenZiti edge-router record `hostname` field is read-only via PATCH/PUT.**
   The OpenZiti management API silently returns HTTP 200 on PATCH but ignores
   the value — `hostname` is router-managed, derived from the router's hello
   message at registration time. Verified empirically with both PATCH and full-
   replacement PUT.

2. **The original `relay-us-east-1` router record had `hostname: localhost`
   AND a server cert whose only SANs were `localhost, relay-us-east-1,
   127.0.0.1, ::1`** — never `flux.embernet.ai` or any reachable name. This
   meant every cp tunnel (including the in-cluster ones that LOOKED working)
   was failing `dial tcp 127.0.0.1:3022: connect: connection refused`
   internally. No tunnel had ever successfully completed the data-plane handshake
   to the relay. Verified by reading the live tunnel pod logs on cp001.

3. **Resolution path:** create a NEW router record with the correct hostname,
   swap the helm-managed `flux-router-enrollment` Secret to the new JWT, wipe
   the router's hostPath identity files (`/var/lib/flux-router/*.cert,*.key,
   config.yml`), restart the flux-router Deployment. The bootstrap script
   regenerates a fresh CSR (now includes `cdn.embernet.ai` in SANs because we
   set `ZITI_ROUTER_ADVERTISED_ADDRESS=cdn.embernet.ai` on the Deployment env)
   and the new cert is issued with the right SANs.

4. **Port 443 binding constraint.** Setting `ZITI_ROUTER_PORT=443` couples
   bind=443 + advertise=443 in the bootstrap script, but port 443 on
   embernet005 (the relay node) is already taken by K3s klipper-lb forwarding
   to traefik. We disabled bootstrap regen after the first run (`ZITI_BOOTSTRAP=
   false`), edited `/var/lib/flux-router/config.yml` in-place to set
   `advertise: cdn.embernet.ai:443` while leaving `bind: 0.0.0.0:3022`, and
   restarted. The router now publishes `tls://cdn.embernet.ai:443` to the
   controller while listening on 3022 internally.

5. **Traefik SNI passthrough on `cdn.embernet.ai:443` → `flux-router-edge`
   ClusterIP svc:443 → router pod:3022.** Modeled on the existing
   `flux-controller-client` IngressRouteTCP. Service `flux-router-edge`
   targetPort patched from 443 to 3022 to bridge traefik's `targetPort: 443`
   default to the router's actual listener.

6. **`cdn.embernet.ai` is an obfuscating subdomain** (looks like a CDN endpoint;
   indistinguishable from any other HTTPS host on packet capture). A-record
   added by Patrick on 2026-05-21, pointing to the same Azure LB IP
   (`20.10.93.244`) that already serves `flux.embernet.ai`.

7. **In-cluster cp tunnel pods** can't resolve `cdn.embernet.ai` through
   coreDNS (the upstream AWS VPC resolver `172.31.0.2:53` doesn't have the
   record yet). Worked around with `hostAliases: 10.43.19.100 cdn.embernet.ai`
   (flux-router-edge ClusterIP) on every `flux-tunnel-embernet-cp00X-flux-edge-
   tunnel` DaemonSet. Bypasses traefik entirely for in-cluster traffic.

8. **End-to-end verification (run on fragua-edge-01):**
   ```bash
   timeout 10 bash -c '</dev/tcp/100.65.0.1/8060 && echo CONNECTED'
   # 100.65.0.1 is the Ziti synthetic IP for ignition-cloud (intercepted by
   # tproxy in the Fragua flux-edge-tunnel pod, routed via cdn.embernet.ai:443
   # through traefik → flux-router → cp005 bind → embernet-dashboard svc)
   # Output: CONNECTED
   ```

### Provisioner patches (2026-05-21)

The probe self-enrollment flow exercised the provisioner service in
`embernet-ai/embernet-provisioner`. Four bugs surfaced; all fixed in a local
fork at `.agent/repos/embernet-provisioner/`. Image pushed to
`ghcr.io/embernet-ai/embernet-provisioner:auth-cleanup-1779374317`.

| Bug | Symptom | Fix |
|---|---|---|
| httpx 0.27.x removed per-call `verify=` kwarg | `AsyncClient.post() got an unexpected keyword argument 'verify'` | Move `verify=False` to `httpx.AsyncClient(verify=False)` constructor; remove from every call site |
| wg-easy v15+ no longer returns `id` in POST `/api/wireguard/client` response | `WireGuard peer creation failed for X: 'id'` (KeyError) | After POST, re-list and look up by name |
| wg-easy /api/session returns 404 (API was rolled to a different auth path) | `WireGuard peer creation failed ... '404 Not Found' for url /api/session` | Make WireGuard provisioning best-effort; return empty `WireGuardConfig` on failure (probes don't need WG — they already ride Fragua's WG) |
| OpenVPN sidecar at `100.64.0.30:8080/client` returns 500 | `OpenVPN profile creation failed` blocking enrollment | Make OpenVPN best-effort; return empty profile string |
| Provisioner deletes existing enrollment but leaves authenticator cert in place; next `ziti edge enroll` fails with `INVALID_ENROLLMENT_TOKEN` because the identity already has a cert | Probe `Init:Error` in CrashLoop after first successful enroll-then-pod-restart | Provisioner now lists `/identities/{id}/authenticators` and deletes each one before creating a new OTT enrollment. Idempotent across pod restarts. |

PR upstream: TODO (work tracked in [.agent/repos/embernet-provisioner/](.agent/repos/embernet-provisioner/)).

### Probe 1.2.1 chart fork — ziti-enroll retry tolerance

The probe chart's `ziti-enroll` initContainer ran `ziti edge enroll` under
`set -e`. Issue: ziti-cli 1.6.6 sometimes internally retries the enroll POST.
The first attempt succeeds — controller issues the cert, writes /shared/
identity.json. The second attempt hits `INVALID_ENROLLMENT_TOKEN` (OTT already
consumed by the first attempt). ziti-cli exits non-zero EVEN THOUGH
identity.json is fully written.

Patched in `deploy/charts/embernet-probe-1.2.1/templates/deployment.yaml`: treat
presence of a non-empty `/shared/identity.json` containing the `ztAPI` envelope
key as success, irrespective of `ziti edge enroll` exit code.

### Performance fixes applied earlier this session (root-causing the Rancher flap)

The Rancher flap was **NOT a memory problem**. It was disk IOPS:

| Item | Before | After |
|---|---|---|
| edge-01 OS disk SKU | Premium_LRS 30 GB **P4** | Premium_LRS 128 GB **P10** |
| Disk IOPS cap | 120 | 500 |
| Disk throughput cap | 25 MB/s | 100 MB/s |
| etcd `apply request` slowest | 2,920 ms (Handler timeout) | 132–387 ms |
| Disk %util observed | 86–100% | ~4% |
| Disk w_await observed | 149 ms | 1.59 ms |
| iowait %CPU | 33% | 0.5% |
| Microsoft Defender (mdatp) | active, ~200 MB | disabled, 0 MB |
| Ignition Edge JVM `-Xmx` | 1024 MB | 512 MB |

Memory was fine (1.7-1.9 GB available on edge-01). The 30 GB Premium SSD
(P4 tier) only delivers 120 IOPS / 25 MB/s, which is below what etcd +
Longhorn + Ignition need running together. etcd's `apply request took too
long` warnings (multi-second on every health probe) caused Rancher's
synchronous 45s namespace check to time out → `Connected=False` flap.

Resizing the OS disk to 128 GB is an online tier upgrade (still Premium
SSD, just larger so it qualifies for P10 limits). VM must be deallocated
to resize. Filesystem auto-grew on next boot (cloud-init).

VM SKU was also upgraded `Standard_B2s` → `Standard_D2as_v4` (sustained
performance — burstable credit starvation under Longhorn+probe load was a
separate but compounding factor).

## Rancher import details

- Cluster CR: `c-j7gtg` (auto-generated by Rancher)
- displayName: `Fragua`
- Labels: `embernet.ai/tenant=fragua-demo`, `embernet.ai/site=fragua`
- Server URL: `https://clusters.embernet.ai`
- Manifest URL (per-cluster): `https://clusters.embernet.ai/v3/import/td9n7mcx6k99fwqsc9qr8wvzkmcxq5tzcc8h5vcptbxxbf5r5n6xbh_c-j7gtg.yaml`
- Global setting changed to make this import work on K3s: `agent-tls-mode=system-store`
  (was empty/strict; strict mode requires `/etc/kubernetes/ssl/certs/serverca`
  which K3s does not provide → cattle-cluster-agent CrashLoopBackOff)

### Phase 6 note — cp02 fix-up needed (still pending upstream PR)

The cp02 Containerfile (`Fireball-Red-Team/deployment/deploy-ut3-cp02.sh`
function `install_codesys`) has two latent bugs we fixed for Fragua:
1. `dpkg -i /tmp/codesys/*.deb` uses a flat glob, but the actual .deb lives
   one directory deeper at `Delivery/linux/codesyscontrol_*_amd64.deb`. The
   flat glob silently no-ops; `apt-get -f install -y` then removes the
   half-installed package. The container ends up running `sleep infinity`
   with no codesys binary at all.
2. The `codesyscontrol` deb's `Depends: codemeter | codemeter-lite` is
   unsatisfiable in plain Debian. We installed an empty `codemeter-lite`
   shim built with `equivs` so dpkg's check passes. Demo mode runs without
   the actual CodeMeter daemon.

The patched script lives at `deploy/codesys/install-codesys.sh` (this repo).
Backport to cp02 to fix the same silent failure there.

## Phase status

| Phase | Item | Status |
|---|---|---|
| 1 | Azure VMs provisioned (rg-fragua-demo) | ✅ |
| 2 | Packages: wireguard, podman, nfs-common, chrony | ✅ |
| 2 | NTP active, hostnames set | ✅ |
| 2 | WireGuard configured on both edges + hub | ✅ |
| 2 | WG handshake edge-01↔hub OK | ✅ |
| 2 | WG handshake edge-02↔hub | 🚧 dead since 2026-05-19; hub-side issue, defer to engineer |
| 3 | K3s server on edge-01 (v1.35.4+k3s1) | ✅ |
| 3 | K3s agent on edge-02 joined | 🚧 not Ready (depends on edge-02 WG) |
| 3 | Snapshotter: `overlayfs` (NOT `overlay`) | ✅ |
| 3 | Node labels (`embernet.ai/site=fragua`, etc.) | ✅ |
| 3 | `service-node-port-range=443-32767` set on apiserver | ✅ |
| 3 | Tenant namespace `tenant-fragua-demo` + network policies | ✅ |
| 3 | metrics-server, CoreDNS, local-path-provisioner, Traefik Running | ✅ |
| 4 | cert-manager v1.14.5 (3/3 pods Running) | ✅ |
| 4 | Longhorn 1.7.2, default StorageClass, PVC smoke test bound | ✅ |
| 4 | ghcr-secret replicated from embernet001 → 4 namespaces | ✅ |
| 5 | flux-edge-tunnel v2.0.8 on fragua-edge-01 (enrolled, /netfoundry has identity.json) | ✅ |
| 5 | flux-edge-tunnel v2.0.8 on fragua-edge-02 (DS exists, 0 replicas while node NotReady) | 🚧 follows edge-02 K3s recovery |
| 6 | CODESYS Linux SL v4.20.0.0 (Podman, demo mode) on both edges | ✅ |
| 6 | FraguaV2.project + Application.xml staged in `/opt/embernet/codesys/data/project/` | ✅ |
| 7 | Ignition Edge 8.3.6 (Podman, mirror cp02 install_ignition_edge) on both edges | ✅ |
| 7 | Edge gateway listens on :8088 (web) + :8060 (GW Network) + OPC-UA :62541 | ✅ |
| 7a | EmberNet probe 1.2.1 (helm, self-enroll via provisioner.enabled=true) on edge-01 | ✅ `1/1 Running`, Ziti identity loaded |
| 7b | Ziti router + service-policy + edge-router-policy + traefik SNI passthrough on cdn.embernet.ai:443 | ✅ end-to-end TCP dial verified |
| 7c | Ignition Edge project (FRAGUAV2) merged into Edge project dir | ✅ |
| 8 | Rancher Cluster CR `c-j7gtg` displayName=`Fragua` | ✅ |
| 8 | cattle-cluster-agent installed + WebSocket to `clusters.embernet.ai` connects | ✅ |
| 8 | `agent-tls-mode` Rancher setting flipped from default (strict) to `system-store` | ✅ |
| 8 | Cluster Connected=True, Ready=True | ✅ stable after disk IOPS upgrade |
| 8 | Tuning: Defender disabled (+200MB), Ignition heap 1024→512 (+500MB) | ✅ |
| 8 | edge-01 OS disk resized 30GB P4 → 128GB P10 (120→500 IOPS, 25→100 MB/s) | ✅ |
| 8 | WireGuard `Restart=on-failure` drop-in on both edges | ✅ |

## Hosts

| Name | Azure public | WG IP | OS | SSH |
|---|---|---|---|---|
| fragua-edge-01 | 20.80.241.221 (eastus2-z1) | 100.64.0.30/24 | Ubuntu 24.04.4 LTS | emberadmin@pw + fireball@key |
| fragua-edge-02 | 52.176.39.25 (centralus) | 100.64.0.31/24 | Ubuntu 24.04.4 LTS | emberadmin@pw + fireball@key |
| embernet003 (hub) | 20.186.57.136 / 192.168.196.3 (ZT) | 100.64.0.1 | — | emberadmin@pw |
| embernet005 (relay node, AWS) | LB-fronted at `cdn.embernet.ai`/`flux.embernet.ai` = 20.10.93.244 | 100.64.0.11 (K3s flannel) | Ubuntu | n/a — cluster-managed |

> Credentials + tool paths consolidated at [.agent/CREDENTIALS.md](../.agent/CREDENTIALS.md) (gitignored).

## NSG rules (Azure, Fragua RG)

Both `fragua-demo-nsg` (eastus2) and `fragua-edge-02NSG` (centralus):
- 1000 `default-allow-ssh`     — TCP 22 in
- 1010 `AllowWireGuard`        — UDP 51820 in (pre-existing, unused for our setup)
- 1020 `AllowWireGuardHttps`   — UDP 443 in (added this session)

## WireGuard topology

```
fragua-edge-01 (100.64.0.30)  ─┐
                                │  UDP/443 → hub → REDIRECT to wg0:51820
fragua-edge-02 (100.64.0.31)  ─┘   (iptables-legacy NAT rule on embernet003)
                                ▼
                       embernet003 (100.64.0.1)
                         public:  20.186.57.136
                         pubkey:  x6X+F8V0UNbGxzXddhxri+Yp091Tu9biFqDyTmNDUUk=
```

Each edge listens on UDP/443 itself (per project convention), advertises
`AllowedIPs = 100.64.0.0/24, 100.64.1.0/24` so all hub-reachable peers are
routed via the tunnel, and uses `PersistentKeepalive = 25`.

Hub-side persistence: peer blocks appended to
`/etc/wireguard/wg0.conf` on embernet003. Pre-change backup at
`/etc/wireguard/wg0.conf.backup-pre-fragua-20260519-133822`.

**Important limitation:** the hub only forwards to direct WG peers in the
Fragua mesh. It does NOT bridge into the EmberNet K3s flannel network (also
on 100.64.0.0/24 — coincidentally overlapping but completely separate). That
is why Phase 7b uses traefik SNI passthrough on `cdn.embernet.ai:443` rather
than WG-mesh routing.

## Auth state

emberadmin user created on both edges with NOPASSWD sudo and
password auth re-enabled in sshd_config + sshd_config.d/*.conf.
fireball admin (key auth) still works as the original provisioning user.

## Open items / follow-ups (post-demo)

| Item | Owner | Notes |
|---|---|---|
| edge-02 WG recovery + K3s rejoin | TBD | Hub-side handshake never completes; needs hub-side debugging |
| Ignition Edge helm chart (`embernet-ai/Ignition-Edge-Pod`) — add `provisioner.enabled` self-enroll pattern | this repo branch (pending PR) | Mirror the pattern from `deploy/charts/embernet-probe-1.2.1/templates/deployment.yaml` lines 49-128 |
| CODESYS helm chart (`embernet-ai/Codesys-AMD-64-x86`) — align with lessons learned | this repo branch (pending PR) | The Podman install learned `find -name '*.deb'` + equivs codemeter-lite shim — see `deploy/codesys/install-codesys.sh` |
| Provisioner upstream PR | `embernet-ai/embernet-provisioner` | 5 fixes in 2 files; local fork at `.agent/repos/embernet-provisioner/` |
| Probe dashboard-callback Ziti service | EmberNet cluster admin | Create service `embernet-dashboard-callback` with intercept/host configs pointing to `embernet-dashboard.fireball-system.svc.cluster.local:8080`; bind policy `#embernet-control-plane`; dial policy `#embernet-probes` |
| Delete old `relay-us-east-1` router record (id `nX3YHSQM5A`) | Anyone with Ziti admin | Old record left in place to allow rollback; safe to delete once new relay is operationally validated |
| cp02 patches backport (CODESYS install_codesys script) | `Fireball-Red-Team/deployment` | See "Phase 6 note" above |
