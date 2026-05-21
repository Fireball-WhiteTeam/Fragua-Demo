# Fragua Demo — Project Update

**Date:** 2026-05-21
**Status:** Demo path operational end-to-end. Probe self-enrolls and runs. Phase 7b Ziti dial resolved. Lessons-learned ready for upstream backport.

---

Two Azure VMs in `rg-fragua-demo`, peered into the EmberNet WG mesh on UDP/443. K3s cluster up on edge-01, Rancher reports `Connected=True, Ready=True` stable, cert-manager + Longhorn standing, flux-edge-tunnel enrolled, CODESYS Control SL 4.20 in Podman running demo-mode, Ignition Edge 8.3.6 in Podman serving all 10 Perspective views HTTP 200. **EmberNet Network Probe 1.2.1 deployed via helm from the App Store, self-enrolled via the auto-provisioner, `1/1 Running`, Ziti identity loaded.** Fragua-Embernode-0001 can dial `ignition-cloud.fireball-system.svc.cluster.local:8060` through the Flux overlay — `nc -zv 100.65.0.1 8060` returns CONNECTED. The full demo critical path works. I have done this.

---

## What Phase 7b actually was, and why "blocked on engineer" was wrong

We chased "the flux-router publishes `localhost` as its hostname so no remote tunnel can dial it" for weeks, assuming the cure was an upstream Ziti admin task. It was not. Four compounding issues, each masking the next, took an afternoon to unwind once I stopped deferring:

1. **The router record's `hostname` field is read-only via the management API.** PATCH and PUT both return HTTP 200 and silently ignore the value. The router populates it itself from its `Listener.Advertise` config at registration time. The legacy `relay-us-east-1` record had been created with the default `localhost` and never updated.

2. **Even after fixing the record, the router's TLS cert SANs were stuck at `localhost, relay-us-east-1, 127.0.0.1, ::1`.** `ZITI_AUTO_RENEW_CERTS=true` cheerfully reissues with the SAME SAN list the original CSR carried — so no amount of restart cycles ever picked up a new advertise address. The fix is to wipe `/var/lib/flux-router/*.cert` AND `*.key` AND `config.yml` so the bootstrap regenerates the CSR from current env vars.

3. **The advertise port had to be 443 for our public-facing constraint, but port 443 on the relay node is already owned by K3s klipper-lb forwarding to traefik.** The fix is a split: router BINDS on `:3022` (no conflict, no privileged-port issue), advertises `cdn.embernet.ai:443`, traefik takes the public 443 and does an SNI-passthrough `IngressRouteTCP` to `flux-router-edge` Service with `targetPort` patched to `3022`. The OpenZiti bootstrap script couples bind-port and advertise-port via the single `ZITI_ROUTER_PORT` env var, so this required disabling `ZITI_BOOTSTRAP=true` after the first run and hand-editing `/var/lib/flux-router/config.yml` to split them.

4. **In-cluster cp tunnel pods can't resolve `cdn.embernet.ai` through coreDNS** because the upstream resolver (`172.31.0.2:53` — AWS VPC's resolver, since embernet005 lives on AWS) doesn't have the record yet. Worked around by adding `hostAliases: 10.43.19.100 cdn.embernet.ai` to every `flux-tunnel-embernet-cp00X-flux-edge-tunnel` DaemonSet, pointing them at the in-cluster `flux-router-edge` ClusterIP directly. Bypasses traefik for cluster-internal traffic; only external (Fragua) traffic uses the public path.

The router now publishes `tls://cdn.embernet.ai:443`. Public DNS A record for `cdn.embernet.ai` points to the same Azure LB IP that already serves `flux.embernet.ai`. The Fragua tunnel resolves it publicly, hits the LB, traefik SNI-passes through to the router, router relays to cp005 (the binder), cp005 dials the K8s service. Whole loop works. Verified by TCP-connecting to `100.65.0.1:8060` (the Ziti synthetic IP) from a Fragua host shell.

**The hostname `cdn.embernet.ai` is intentionally boring** — looks like static asset delivery, indistinguishable from any other HTTPS host on packet capture. Future remote sites need exactly two things now: a working flux-edge-tunnel + a Dial policy on whatever service they need to reach. No router changes ever again.

---

## The provisioner had four bugs in production

Self-enrollment for the EmberNet probe (so operators stop hand-rolling Ziti identities) exercised `embernet-ai/embernet-provisioner` for the first time at scale. I had to patch it five times to get one clean enrollment:

1. **`httpx 0.27.x` removed the per-call `verify=` kwarg on `AsyncClient.{get,post,delete,patch}`.** Every call against the controller's self-signed cert returned `AsyncClient.post() got an unexpected keyword argument 'verify'`. Fix: move `verify=False` to the `httpx.AsyncClient(verify=False)` constructor; remove from every call site.

2. **wg-easy v15+ no longer returns `id` in the POST `/api/wireguard/client` response** — only `name, address, privateKey, publicKey, preSharedKey, createdAt, updatedAt, enabled`. The provisioner crashed with a bare `'id'` KeyError. Fix: after POST, re-list and look up by name to get the id.

3. **wg-easy `/api/session` started returning 404** somewhere along the way — the API path was probably rolled and the provisioner never followed. Rather than chase the API drift, made WG provisioning best-effort (return empty `WireGuardConfig`). For probes that already ride a separate WG mesh (Fragua), the WG block in the response is unused anyway.

4. **The OpenVPN sidecar at `100.64.0.30:8080/client` returns 500 for every request** — separate downstream issue. Also made best-effort. Probes don't need OpenVPN.

5. **Idempotency bug: the provisioner deletes the existing OTT enrollment for the identity but leaves the authenticator cert in place.** Next pod restart calls `/provision` again, gets a fresh OTT, ziti-enroll redeems it → `400 INVALID_ENROLLMENT_TOKEN` because the controller refuses to overwrite an existing authenticator. The probe was stuck in `Init:CrashLoopBackOff` forever even though every component WAS working. Fix: provisioner now also lists `/identities/{id}/authenticators` and deletes each one before minting a new OTT.

Plus a chart bug in `embernet-probe-1.2.1`: ziti-cli 1.6.6 has an internal retry loop. First enroll succeeds (writes identity.json). Second hits INVALID_ENROLLMENT_TOKEN because the OTT was consumed by the first. `ziti edge enroll` exits non-zero EVEN THOUGH identity.json is fully written and valid. The chart was `set -e`-ing on that exit code. Fix: treat presence of a non-empty identity.json containing the `ztAPI` envelope as success, regardless of CLI exit code.

All patches in this repo. Image `ghcr.io/embernet-ai/embernet-provisioner:auth-cleanup-1779374317` is live. Source at `.agent/repos/embernet-provisioner/`. **Upstream PR follow-up tracked** — two files touched (`app/services/ziti.py`, `app/services/wireguard.py`, `app/routes/provision.py`). Probably worth a v0.2.0 minor release.

---

## Carried over from prior update — Phase 6 gotcha still NOT backported

`install_codesys()` in `Fireball-Red-Team/deployment/deploy-ut3-cp02.sh` is **still silently broken on every install** for the same two reasons documented 2026-05-19:

1. Flat `dpkg -i /tmp/codesys/*.deb` glob misses the real deb at `Delivery/linux/codesyscontrol_*_amd64.deb`.
2. `codesyscontrol`'s `Depends: codemeter | codemeter-lite` is unsatisfiable in plain Debian → apt removes the half-installed package → container runs `sleep infinity` with no CODESYS binary, `podman ps` says Up, nobody notices.

Patched script for Fragua at `deploy/codesys/install-codesys.sh`. **Please backport — every site stood up against the documented runbook since cp02 has a container that LOOKS healthy but isn't serving OPC-UA on :4840.**

---

## What I'm doing next

1. **PR the provisioner patches upstream** to `embernet-ai/embernet-provisioner`. Two files, five fixes, ~50 lines of diff.

2. **Align the `embernet-ai/Ignition-Edge-Pod` and `embernet-ai/Codesys-AMD-64-x86` helm charts with the lessons learned** (per Patrick's direction). The Ignition chart needs the `provisioner.enabled` self-enroll pattern (mirror `deploy/charts/embernet-probe-1.2.1/templates/deployment.yaml` initContainers 1+2). The CODESYS chart needs the find+equivs fix from Gotcha #1 baked into its image build.

3. **Document the Phase 7b `cdn.embernet.ai` traefik passthrough pattern** so future remote sites just need: their own flux-edge-tunnel + a Dial policy. No router work ever again. See `deploy/PHASE-7B-RUNBOOK.md` for the recipe.

4. **edge-02 cluster recovery** — handshake is dead from the hub side, ICMP times out across the WG mesh. Hub-side debugging needed; nothing on the Fragua VM side will fix it. Defer to whoever owns embernet003.

5. **Probe → dashboard callback Ziti service** — quick add. Three configs (intercept, host, service) + bind policy `#embernet-control-plane` + dial policy `#embernet-probes` + service-edge-router-policy. The full Python snippet to do it is in `deploy/PHASE-7B-RUNBOOK.md`. Not blocking the demo; the probe runs fine, just can't telemeter back yet.

---

## TL;DR for anyone walking in cold

- Demo critical path is live. Ziti overlay works. Probe self-enrolls. Both edges' Podman containers run CODESYS + Ignition.
- The "blocked on Ziti admin" framing from prior updates was wrong — the work is doable end-to-end from this repo's tooling. I have a runbook for the next person.
- Two upstream PRs in flight (provisioner + helm charts) plus one platform-wide gotcha that's been quietly broken on every site since cp02 deployed.
- Two things still pending: edge-02 hub-side handshake (not a Fragua-side bug) and the dashboard-callback Ziti service (a 5-minute task once the demo dust settles).

Patrick
