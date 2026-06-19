# Containerize EmberNET Endpoint on the Fragua Edges — Phased Plan

> **Status:** Draft for review. Do NOT execute until Patrick approves.
> **Date drafted:** 2026-06-03
> **Author:** Claude (Opus 4.7) for Patrick Ryan, CTO Fireball.

---

## 1. What we have today (as of the morning of 2026-06-03)

Reading the artifacts in `.agent/` + the live state on `fragua-edge-01` /
`fragua-edge-02` after the 2026-06-01 work:

| Layer | Current state on Fragua edges |
|---|---|
| WireGuard underlay (host) | `wg-quick@embernet0` systemd unit, /etc/wireguard/embernet0.conf copied from the enrollment-written /var/lib/embernet/wireguard/embernet.conf at enrollment time. |
| WG watchdog | `embernet-wg-watchdog.timer` (60s), shell script at `/usr/local/bin/embernet-wg-watchdog` restarting `wg-quick@embernet0` on missing iface or handshake stale > 180s. |
| K3s | `flannel-iface: embernet0`, both nodes Ready, pod-to-pod cross-node working (after the 2026-06-01 k3s restart that brought flannel.1 up). |
| In-cluster Flux/Ziti tunneler | `flux-tunnel-fragua-edge-{01,02}-flux-edge-tunnel-*` DaemonSet pods, holding the LEGACY Ziti identities `199XSfc7B1` (Fragua-Embernode-0001) and `XSuRSfc2B1` (Fragua-Embernode-0002). These provide the 100.65.0.x synthetic-IP tproxy on each node. |
| embernet-endpoint daemon | **Installed (v0.0.33-1) but `embernet.service` is `inactive`** on both edges. Each edge holds an enrolled identity in `/var/lib/embernet/identity/embernet.json -> fragua.json` (edge-01 id `8wIhMwE9I`, edge-02 id `9cO634E9T`, both tagged with role attr `fragua-edge-dial`). |
| Industrial dashboard | v4.0.28 (`bearer.go` accepts `access_as_user` scope; `/api/tenants/me` returns the user's allowed tenants). No per-device "preferred tenant" knob today. |

## 2. Where embernetlite-linux is (origin/main, fetched 2026-06-03)

Since we shipped v0.0.33 on 2026-06-01, three more releases landed:

| Tag | What shipped (why it matters here) |
|---|---|
| **v0.0.34** | `fix(enroll): daemon Wizard scope was still User.Read — caused 401 at /api/tenants/me`. **Required** for daemon-driven (in-container, no CLI) enroll. |
| **v0.0.35** | `fix(enroll): auto-Connect tunnels after Configure (was silently disconnected post-enroll)`. The daemon now actually brings the tunnels UP after enrollment — no separate "tunnels/connect-all" call needed. |
| **v0.0.36** | `log every Wizard phase transition (enroll_phase) for grep-friendly debugging`. Operationally important so the container logs surface phase progress. |

Container image: `ghcr.io/embernet-ai/embernetlite:0.0.36` (multi-arch amd64+arm64) and `:beta`. Built by `.github/workflows/container.yml` on every `v*` tag.

Image contract per `packaging/container/README.md`:
```
podman run -d \
    --name embernet \
    --restart always \
    --network host \
    --cap-add NET_ADMIN --cap-add NET_RAW \
    --device /dev/net/tun \
    -v /etc/embernet:/etc/embernet:ro \
    -v /var/lib/embernet:/var/lib/embernet \
    -v /var/log/embernet:/var/log/embernet \
    -v /run/embernet:/run/embernet \
    ghcr.io/embernet-ai/embernetlite:0.0.36
```

## 3. The gap: tenant pre-pin on daemon-driven re-auth

Code review of `internal/service/linux.go:152` shows the daemon's
Wizard is wired **without** any `SetTenantHint(...)` call — only the
CLI path (`cmd/embernetlite/handlers.go:142`) calls `SetTenantHint`
based on the `--tenant-id` flag. So the daemon currently:

- On fresh enrollment via the loopback API: lands in `PhaseChoosingTenant` and waits for an HTTP caller to `POST /api/v1/enroll/select-tenant` with a tenant ID.
- On refresh-token-driven re-auth: same flow if AAD bumps the user back through interactive consent — no preselection.

For an **unattended container** on the edge, that's a dead-end. We need an env-var- (or settings-file-) driven tenant hint.

**Minimal code change** (proposed for v0.0.37):
- Read `EMBERNET_TENANT_HINT` in `cmd/embernetlite/handlers.go` (the daemon's `handleDaemon` path) and call `wizard.SetTenantHint(...)` after `apiServer.SetWizard(wizard)`.
- Same precedence as the CLI: env wins → otherwise leave the wizard's hint empty.

That's ~5 lines + a test.

## 4. Phase plan

### Phase 0 — Ship v0.0.37 with `EMBERNET_TENANT_HINT` env support
*Why first:* the whole point of running this as a container is unattended re-auth pinned to the fragua tenant. Without this knob, a token refresh that lands on PhaseChoosingTenant blocks the container with no operator to step in.

Steps:
1. Edit `internal/service/linux.go` (or wherever the daemon-side wizard is constructed) to read `EMBERNET_TENANT_HINT` from the environment and call `wizard.SetTenantHint(env)` when non-empty.
2. Add a single unit test that constructs the daemon wizard with that env set and asserts the wizard reports the hint after `Start`.
3. Bump versions: `cmd/embernetlite/version.go`, `cmd/embernetctl/version.go`, `packaging/rpm/embernet-endpoint.spec`, `packaging/deb/debian/changelog`, top of `CHANGELOG.md`.
4. Commit → tag `v0.0.37` → push. CI builds both the `.deb` + the OCI image and publishes `ghcr.io/embernet-ai/embernetlite:0.0.37` + `:beta`.

Hand-off to next phase: image tag `0.0.37` available on GHCR.

### Phase 1 — Stage the container image + host directories on edge-01
*Why first:* validate on the canonical demo node before touching edge-02. Edge-01 is the K3s control-plane; if cutover breaks, edge-02 keeps the cluster.

Steps (all via `az vm run-command` per `.agent/CREDENTIALS.md` §SSH guidance):
1. Pre-pull the image with an authenticated podman pull (GHCR is private — same PAT path we used for the .deb download). Pin tag `0.0.37`.
2. Confirm `/etc/embernet` + `/var/lib/embernet` + `/var/log/embernet` + `/run/embernet` exist with the right ownership (they do; .deb postinst handled it).
3. Write `/etc/embernet/env` with `EMBERNET_TENANT_HINT=fragua` (file owned `root:embernet`, mode `0640`).
4. Run **podman dry-run** (`podman run --rm --read-only -e ...` with a no-op cmd) just to validate caps + mount + env propagate without yet replacing wg-quick.

No production-path change in this phase.

### Phase 2 — Cutover edge-01 from wg-quick + flux-tunnel → embernet container
*Why this order:* the embernet daemon owns embernet0 via netlink + creates the Ziti tproxy in-process. We must remove the conflicting interface-owners (wg-quick on the host, flux-tunnel pod in k3s) before starting the container, or the daemon will fight wg-quick for the netlink iface.

Steps:
1. Snapshot dial state pre-cutover: `for ip in 100.65.0.1:8060 100.65.0.2:1883 100.65.0.10:8088; do timeout 5 bash -c "</dev/tcp/$ip"; done` from the host. Record the baseline.
2. K3s side: remove the flux-tunnel DaemonSet pod from edge-01 only — either patch the DaemonSet's nodeAffinity to exclude `fragua-edge-01`, or `kubectl cordon` + `kubectl delete pod` and let the DaemonSet not reschedule onto a cordoned node. Pod removed → tproxy routes for 100.65.0.x on edge-01 go away momentarily.
3. Host side: `systemctl stop wg-quick@embernet0 && systemctl mask wg-quick@embernet0`. Mask so the watchdog can't bring it back.
4. Host side: `systemctl stop embernet-wg-watchdog.timer && systemctl disable embernet-wg-watchdog.timer`. The container has its own internal watchdog.
5. Start the container:
   ```
   podman run -d --name embernet --restart always --network host \
       --cap-add NET_ADMIN --cap-add NET_RAW --device /dev/net/tun \
       --env-file /etc/embernet/env \
       -v /etc/embernet:/etc/embernet:ro \
       -v /var/lib/embernet:/var/lib/embernet \
       -v /var/log/embernet:/var/log/embernet \
       -v /run/embernet:/run/embernet \
       ghcr.io/embernet-ai/embernetlite:0.0.37
   ```
6. Wait up to 60s for `embernetctl status` (via the host-mounted socket) to return clean, and `wg show embernet0` to show a fresh handshake.
7. Re-run the dial baseline from step 1. All three should `CONNECTED` again, this time through the daemon's in-process tproxy.
8. Verify K3s flannel hasn't lost the iface (it shouldn't — `embernet0` name + IP `100.64.0.30/24` are preserved by the daemon).
9. **Rollback test path** if anything fails: `podman stop embernet && systemctl unmask wg-quick@embernet0 && systemctl start wg-quick@embernet0 && systemctl enable --now embernet-wg-watchdog.timer && kubectl uncordon fragua-edge-01`. We're back to the 2026-06-01 known-good state within ~15s.

### Phase 3 — Cutover edge-02
Same as Phase 2 but for `fragua-edge-02` (WG IP `100.64.0.36`, Ziti id `9cO634E9T`). Run only after edge-01 has been green for at least 10 min so we know the container is stable.

### Phase 4 — Decommission the flux-tunnel DaemonSet for Fragua
*Why:* with embernet containers on both nodes, the flux-tunnel DaemonSet is dead weight on these nodes. Two options:
- **A (recommended):** patch the DaemonSet's `nodeAffinity` to exclude Fragua nodes (`embernet.ai/site != fragua`). Leaves the DaemonSet intact for other clusters that still use it; just stops scheduling on Fragua.
- **B:** delete the DaemonSet outright. Cleaner but more disruptive across other clusters that share the same chart.

We do **A** because the dashboard/cluster-wide chart isn't ours to delete from here.

Also: revoke (or at least tag for removal) the LEGACY Ziti identities `199XSfc7B1` + `XSuRSfc2B1` once we've watched the new identities serve traffic for >24 hr. Don't delete in the same window — leave a rollback path.

### Phase 5 — Update artifacts
After cutover stabilizes:
- `.agent/CREDENTIALS.md`: rewrite the WireGuard mesh section + Ziti dial-policy section to reflect that embernet-endpoint container is the on-host owner of embernet0 and the Ziti tunneler. Drop the references to `wg-quick@embernet0` + `embernet-wg-watchdog.timer` + flux-tunnel DaemonSet as Fragua-relevant pieces.
- `.agent/workflows/architecture-reference.md`: the topology diagram still shows `Flux Tunnel` as a pod and Flannel-iface as `wg0`. Both are wrong as of 2026-06-01 (flannel=embernet0) and will be doubly wrong after this work (embernet daemon, not a separate Flux pod). Rewrite the per-edge box.
- `.agent/workflows/deployment.md`: Phase 5 says "Deploy flux-edge-tunnel v2.0.8+ per node". Replace with "Run embernet-endpoint container per node (image `ghcr.io/embernet-ai/embernetlite:<tag>`, env `EMBERNET_TENANT_HINT=fragua`)".
- `WHITE-TEAM-IGNITION-HANDOFF.md`: the "Already Done" bullet about flux-edge-tunnel handles the tproxy needs a tiny rewrite — the embernet container handles it now.

### Phase 6 — Verification matrix
Same matrix used 2026-06-01, plus container-specific entries:

| Check | Expected | How |
|---|---|---|
| `podman ps --format '{{.Names}} {{.Status}}'` | `embernet healthy (Up XXm)` | host |
| `wg show embernet0` handshake age | < 30s | host |
| K3s nodes both Ready | yes | edge-01 kubectl |
| flannel.1 + route 10.42.1.0/24 present on edge-01 | yes | edge-01 |
| Ziti dial 100.65.0.1:8060 from host | CONNECTED | both edges |
| Ziti dial 100.65.0.2:1883 from host | CONNECTED | both edges |
| Ziti dial 100.65.0.10:8088 from host | CONNECTED | both edges |
| Dashboard `/api/devices?tenant=fragua` shows BOTH edges as Online | yes | dashboard SuperAdmin view |
| `embernetctl status` from host | reports tunnels up, identity loaded | both edges |
| Refresh-token-driven re-auth (simulated by deleting `device.token` and bouncing the container) lands on tenant=fragua without prompting | yes | manual test |

## 5. What is left after Phase 6

After this completes, the open items I see in the broader Fragua-Demo + dashboard surface are:

| Item | Owner | Notes |
|---|---|---|
| `/api/audit/search` tenant scoping | dashboard | Per `industrial-dashboard/.agent/WORK_PLAN.md` line 30–41 — deferred half-day work; `audit_log` schema migration + write-path + read-path + middleware + tests. |
| Admin/Engineer/Operator view tenant pivot | dashboard | Per same WORK_PLAN line 50–62 — retrofit the v4.0.20 picker pattern to the lower views; each view is a separate ~similar surgery. |
| Forge org→dashboard tenant mapping design | dashboard | Per WORK_PLAN line 43–49 — deferred until Forge usage stabilizes. |
| ARM build restoration | dashboard | One-line change in `publish.yml`. |
| OpenVPN sidecar (provisioner pod) | embernet-provisioner | The 2026-06-01 chain left this off — the "toggle" remains off; not blocking the demo. |
| Cosign signing for the embernet-endpoint OCI image | embernet-endpoint | Per `packaging/container/README.md` §Cosign — keypair is out-of-band infra TODO. The image is currently unsigned. |
| `Tenant-Fragua` proper Ziti tenant (vs. the current Fireball-tenant-with-fragua-edge-dial-role-attr workaround) | flux-controller | We added `#fragua-edge-dial` to existing policies 2026-06-01 as a defense-in-depth measure. Long-term the correct shape is a separate Ziti tenant for Fragua with its own admin scope. |

## 6. Risk + rollback

The biggest risk is Phase 2 step 5 — starting the container while the host has any other wg-quick-style claim on `embernet0`. If we miss masking wg-quick or stopping the watchdog, the daemon's netlink calls will collide.

Mitigation: in Phase 2 the **mask** + **stop watchdog** steps are required before the `podman run`. If anything looks off (`wg show embernet0` shows two interfaces, or the daemon logs `EADDRINUSE` / `EBUSY`), abort immediately and execute the rollback path documented inline.

Secondary risk: a refresh-token failure during the cutover window. The new container will read `/var/lib/embernet/refresh.token` written by the v0.0.33 CLI enrollment. If AAD has invalidated it (it shouldn't have — 2 days old), the daemon will trigger a fresh device-code flow. Without the v0.0.37 env support, it would block on PhaseChoosingTenant. **This is why Phase 0 is non-optional.**

## 7. Ask for review

Three decision points where I want a "yes/no/different" before executing:

1. **Phase 0 code change** — add `EMBERNET_TENANT_HINT` env support in the daemon wizard wiring. Approve approach + the v0.0.37 bump?
2. **Phase 2 cutover order on edge-01** — kill flux-tunnel pod FIRST, then host wg-quick, then start container. Or do you want a different sequence (e.g., container up alongside wg-quick first and only kill wg-quick after the container's wgctrl claim has settled — which my reading of the daemon source says will fail since the daemon expects to create the iface fresh)?
3. **Phase 4 — flux-tunnel DaemonSet** — patch nodeAffinity to exclude Fragua (recommended) vs. delete the DaemonSet entirely?

After your three calls, I execute Phases 0 → 6 in order.
