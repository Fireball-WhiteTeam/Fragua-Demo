# Fragua Demo — Documentation & Resource Index

> Single-page index to every doc, runbook, chart, script, config, and external resource that this repo touches.
> Open this when you need to find something fast.
> Open `README.md` when you need the narrative.

---

## Top-level

| File | What it is |
|---|---|
| [`README.md`](README.md) | Project overview — what Fragua is, what's deployed, how users interact via Dashboard + CODESYS once the EmberNet Endpoint Controller lands. **Start here.** |
| [`INDEX.md`](INDEX.md) | This file. Resource map. |
| [`.gitignore`](.gitignore) | Excludes per-device JWTs, identity blobs, `.agent/` scratch + credentials. |

---

## Audit + Status

| File | Purpose |
|---|---|
| [`deploy/AUDIT.md`](deploy/AUDIT.md) | Phase-by-phase deployment status, every gotcha hit during stand-up + how each was fixed. Authoritative log of what's done. |
| [`deploy/CLICKUP-UPDATE.md`](deploy/CLICKUP-UPDATE.md) | Plain-language status update at handoff — written in CTO tone for sharing into ClickUp/Slack. |
| [`deploy/tuning-notes.md`](deploy/tuning-notes.md) | Disk IOPS / Defender / heap tuning that resolved the Rancher flap. |

---

## Phase 7b — Ziti overlay (the part everyone got stuck on)

| File | Purpose |
|---|---|
| [`deploy/PHASE-7B-RUNBOOK.md`](deploy/PHASE-7B-RUNBOOK.md) | End-to-end Ziti overlay setup from controller → router → edge tunnel → service definition. Includes the Python snippet to add another service over the same path. **The full Phase 7b recipe.** |

---

## WireGuard

| File | Purpose |
|---|---|
| [`deploy/wireguard/fragua-edge-01.wg0.conf`](deploy/wireguard/fragua-edge-01.wg0.conf) | edge-01 WG config (peer block targets the hub) |
| [`deploy/wireguard/fragua-edge-02.wg0.conf`](deploy/wireguard/fragua-edge-02.wg0.conf) | edge-02 WG config |
| [`deploy/wireguard/embernet003-peer-addition.txt`](deploy/wireguard/embernet003-peer-addition.txt) | Peer blocks to append on the hub for both Fragua edges |
| [`deploy/wireguard/ENGINEER-FIX.md`](deploy/wireguard/ENGINEER-FIX.md) | Convention drift writeup — `ListenPort` semantics on peers vs hub, OT firewall traversal, MTU |
| [`deploy/wireguard/AWS-RECOVERY.md`](deploy/wireguard/AWS-RECOVERY.md) | Recovery procedure for the AWS-side WG nodes (embernet004/005/006) when the mesh wedges |

---

## K3s

| File | Purpose |
|---|---|
| [`deploy/k3s/fragua-edge-01.config.yaml`](deploy/k3s/fragua-edge-01.config.yaml) | K3s server config — use `overlayfs`, NOT `overlay`, snapshotter |
| [`deploy/k3s/fragua-edge-02.config.yaml`](deploy/k3s/fragua-edge-02.config.yaml) | K3s agent config |
| [`deploy/k3s/tenant-namespace.md`](deploy/k3s/tenant-namespace.md) | Tenant namespace + network policy setup |

---

## Flux / Ziti

| File | Purpose |
|---|---|
| [`deploy/flux/helm-install.sh`](deploy/flux/helm-install.sh) | flux-edge-tunnel v2.0.8 helm install with identity persistence on Longhorn RWX |
| `deploy/flux/jwts/` | Per-device enrollment JWTs **(gitignored — never commit)** |

---

## Dashboard / Industrial Dashboard

| File | Purpose |
|---|---|
| [`deploy/dashboard/tenant.yaml`](deploy/dashboard/tenant.yaml) | Fragua Tenant CR manifest. Without this CR, Fragua won't appear in the dashboard tenant dropdown even after Rancher import. Apply on the EmberNet cluster + roll the dashboard deploy. |

---

## CODESYS

| File | Purpose |
|---|---|
| [`deploy/codesys/install-codesys.sh`](deploy/codesys/install-codesys.sh) | Patched Podman install with the recursive `find` for the nested `.deb` + the equivs `codemeter-lite` shim. Replaces the broken cp02 install pattern. |

---

## Ignition Edge

| File | Purpose |
|---|---|
| [`deploy/ignition/install-ignition-edge.sh`](deploy/ignition/install-ignition-edge.sh) | Podman install of Ignition Edge 8.3.6 mirroring the cp02 pattern |
| [`deploy/ignition/project-deploy.md`](deploy/ignition/project-deploy.md) | How to merge custom Perspective views into the `Edge` project (Edge edition rejects any project not named `Edge` — hard name check) |
| [`deploy/ignition/project-redeploy.sh`](deploy/ignition/project-redeploy.sh) | Idempotent script to push project updates to the Edge container |

---

## Helm charts (forks with upstream PRs filed)

| Chart | Local fork | Upstream PR |
|---|---|---|
| `embernet-probe` | [`deploy/charts/embernet-probe-1.2.1/`](deploy/charts/embernet-probe-1.2.1/) | (already-pushed fork chart, plus upstream PR on `Ignition-Edge-Pod` for the same self-enroll pattern) |
| `embernet-probe` chart fork notes | [`deploy/charts/embernet-probe-1.2.1/README-fork.md`](deploy/charts/embernet-probe-1.2.1/README-fork.md) | — |

---

## Rancher

| File | Purpose |
|---|---|
| [`deploy/rancher/README.md`](deploy/rancher/README.md) | Cluster import details (cluster id `c-j7gtg`, displayName `Fragua`, `agent-tls-mode=system-store` requirement on K3s, manifest URL) |

---

## Credentials & operator artifacts

| File | Purpose |
|---|---|
| `.agent/CREDENTIALS.md` | All operating credentials + tool paths + endpoints + caveats. **Gitignored** — never commit. Workspace-local artifact for any agent (Claude / Antigravity / VS Code) running in this repo. |
| `.agent/context.md` | Inherited session context |
| `.agent/workflows/` | Workflow definitions |
| `.agent/repos/` | Cloned chart + provisioner repos for local edit + PR work **(gitignored)** |

---

## Upstream PRs filed

| Repo | PR | What it fixes |
|---|---|---|
| [`Embernet-ai/embernet-provisioner`](https://github.com/Embernet-ai/embernet-provisioner/pull/1) | #1 | Five runtime fixes — httpx 0.27 `verify=` kwarg, wg-easy v15 `id`-on-create, wg-easy `/api/session` 404 best-effort, OpenVPN sidecar 500 best-effort, authenticator-cleanup for idempotent re-enrollment |
| [`Embernet-ai/Ignition-Edge-Pod`](https://github.com/Embernet-ai/Ignition-Edge-Pod/pull/1) | #1 | Opt-in `provisioner.enabled=true` self-enrollment via the same init-container pattern as embernet-probe |
| [`Embernet-ai/Codesys-AMD-64-x86`](https://github.com/Embernet-ai/Codesys-AMD-64-x86/pull/1) | #1 | Install hardening — equivs `codemeter-lite` shim, drop the silent `apt-get install -f -y` package removal, hard post-install assertion on the binary |

---

## External (live) resources

| Resource | URL |
|---|---|
| Industrial Dashboard | `https://dashboard.embernet.ai` |
| Rancher | `https://clusters.embernet.ai` |
| Ziti controller (Flux) | `https://flux.embernet.ai:443` |
| Ziti router (data plane, SNI-passthrough) | `cdn.embernet.ai:443` |
| Provisioner self-enroll API | `https://provisioner.embernet.ai/api/v1/provision` |
| Ignition Edge web UI — fragua-edge-01 | `http://20.80.241.221:8088` |
| Ignition Edge web UI — fragua-edge-02 | `http://52.176.39.25:8088` |

---

## Key in-cluster names + IDs

| Object | Value |
|---|---|
| Fragua Rancher cluster CR | `c-j7gtg` (displayName: `Fragua`) |
| Fragua tenant CR | `tenant-fragua-demo/fragua-demo` |
| Ziti edge router | `flux-router-v2` (id `P.csD4A7T1`) — replaced legacy `relay-us-east-1` |
| `ignition-cloud` Ziti service | id `6H5U38Lo55M5aY9Oforg3` |
| `embernet-dashboard-callback` Ziti service | id `3yTGmAyMZej2B2OEEbUD2I` |
| Bind side (cp005 daemonset) | `flux-tunnel-embernet-cp005-flux-edge-tunnel` |
| Fragua tunnel | `flux-tunnel-fragua-edge-01-flux-edge-tunnel` |
| Probe pod | `embernet-probe-*` in `fireball-system` ns on fragua-edge-01 |
| Ziti synthetic IP for `ignition-cloud:8060` | `100.65.0.1:8060` |

---

## Quick `kubectl` cheats

```bash
# From any host with EmberNet cluster kubectl context

# See Fragua tenant
kubectl get tenant -A | grep fragua

# See Ziti services
kubectl -n embernet-provisioner exec deploy/embernet-provisioner -- python3 -c "
import os, httpx
zc = os.environ['ZITI_CONTROLLER_URL']
c = httpx.Client(verify=False, base_url=zc, timeout=15)
tok = c.post('/edge/management/v1/authenticate', params={'method':'password'},
             json={'username': os.environ['ZITI_ADMIN_USER'],
                   'password': os.environ['ZITI_ADMIN_PASSWORD']}).json()['data']['token']
import json
r = c.get('/edge/management/v1/services', headers={'zt-session': tok})
for s in r.json()['data']: print(s['name'], s['id'])
"

# Confirm Ziti dial works from a Fragua edge (run on edge VM directly)
timeout 5 bash -c '</dev/tcp/100.65.0.1/8060 && echo OK || echo FAIL'
```

```bash
# From a Fragua edge (run as emberadmin@<edge>)

# K3s nodes
sudo k3s kubectl get nodes -o wide

# Ignition Edge live
curl -sS http://localhost:8088/system/gwinfo

# Probe status
sudo k3s kubectl -n fireball-system get pod -l app.kubernetes.io/name=embernet-probe

# Flux tunnel sees ignition-cloud + dashboard-callback services
sudo k3s kubectl -n flux-system logs ds/flux-tunnel-fragua-edge-01-flux-edge-tunnel --tail=200 | grep -i "adding service"
```

---

## Open follow-ups (not closed at the time of this index)

1. **Wire Ignition Edge → ignition-cloud GW Network connection.** Direct SQLite insert into `WSCONNECTIONSETTINGS` doesn't trigger the runtime to pick it up. Either click-config in the Ignition Edge web UI (4 fields) or import a `.gwbk` slice from a known-good Edge.
2. **Probe → dashboard callback OIDC session refresh.** The `embernet-dashboard-callback` Ziti service is fully created on the controller; the probe's OpenZiti SDK fails its `subject_token` refresh against `https://flux.embernet.ai:443/oidc/oauth/token`. Engineer-side controller config investigation.
3. **EmberNet Endpoint Controller install** on each Fragua edge — the piece that owns downstream OT device discovery + claim. Helm chart forthcoming from Fireball engineering.
4. **Smoke artifact cleanup** — stale Ziti identities `fragua-smoke-*` + leftover wg-easy peers (housekeeping, demo doesn't depend on it).

---

*Patrick Ryan — Fireball Industries*
