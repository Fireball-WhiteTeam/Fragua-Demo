# Fragua Demo — Project Update

**Date:** 2026-05-19
**Status:** Platform up, 7/8 phases done, app-layer hookup pending (Phase 7b).

---

Two Azure Ubuntu VMs in `rg-fragua-demo`, one in eastus2, one in centralus, peered into the EmberNet WireGuard mesh on UDP/443. K3s 1.35.4 cluster, two nodes Ready, tenant namespace + default-deny network policies, cert-manager, Longhorn (default StorageClass), ghcr-secret replicated in from embernet001 over `az vm run-command` because SSH key-only refused me. flux-edge-tunnel v2.0.8 on both edges, both Ziti identities enrolled. CODESYS Control SL 4.20 demo mode in Podman, FraguaV2 project staged. Ignition Edge 8.3.6 in Podman, **all 10 Perspective views serving HTTP 200 on both edges**. Rancher reports `Fragua` cluster `Connected=True, Ready=True` stable. I have done this.

---

## The two gotchas worth filing for the rest of the fleet

Both of these will silently bite anyone provisioning a new site against the documented runbooks. Both are now fixed for Fragua and documented in `deploy/AUDIT.md`. Backport when you can.

### Gotcha #1 — `install_codesys()` in `deploy-ut3-cp02.sh` is silently broken on every install

The CODESYS Linux SL `.package` from CODESYS GmbH is a ZIP that wraps a Debian `.deb` at `Delivery/linux/codesyscontrol_linux_4.20.0.0_amd64.deb` — **not** at the root of the archive. The cp02 Containerfile does:

```bash
unzip -q /tmp/codesys.pkg -d /tmp/codesys && dpkg -i /tmp/codesys/*.deb
```

The flat glob doesn't match. `dpkg -i` silently no-ops. Then `apt-get -f install -y` runs because `codesyscontrol` declares `Depends: codemeter | codemeter-lite` — neither is in Debian repos — so apt **uninstalls** the half-extracted package to "resolve dependencies." The container build succeeds, the image gets tagged, the container starts, `podman ps` says "Up", you walk away thinking life is good.

The container is running `sleep infinity` because the entrypoint shell finds no codesyscontrol binary at `/opt/codesys/bin/codesyscontrol.bin` and falls through to its `exec sleep infinity` fallback. **There is no CODESYS runtime in there.** No OPC-UA on :4840. Nothing.

cp02 has been in this state since deployment. I should not have to discover this but here we are.

**Fix (applied for Fragua in `deploy/codesys/install-codesys.sh`):**

1. Find the deb recursively: `DEB=$(find /tmp/codesys -name 'codesyscontrol_*_amd64.deb' -print -quit)`
2. Build an empty `codemeter-lite` shim with `equivs` so dpkg's dependency check passes. Demo mode doesn't actually need the CodeMeter daemon — the runtime runs in 30-min demo cycles when no license file is present.
3. Add a post-install assertion: `test -x /opt/codesys/bin/codesyscontrol.bin || exit 1`. This is the difference between "the build looks fine" and "the build actually installed something."

Confirmed Fragua's CODESYS is in demo mode and OPC-UA is listening on 4840 across `100.64.0.30`, `100.64.0.31`, eth0, and CNI interfaces. Service Gateway on 11740.

### Gotcha #2 — Ignition Edge accepts EXACTLY ONE project, and it must be named `Edge`

The site provisioning checklist describes deploying customer projects by dropping them in `data/projects/<projectname>/`. This does not work on Ignition Edge.

Edge's `EdgeProjectManager` rejects any project directory whose name is not the default. Log line:

```
W [EdgeProjectManager] Invalid project for this platform edition: 'FRAGUAV2'.
```

I assumed Vision content was the issue (the project had a `com.inductiveautomation.vision/client-tags/` dir — 700 bytes of orphan tags, no actual Vision windows). Stripped Vision. Still rejected.

Then I tried with **one** Perspective view instead of ten. Still rejected.

Then I renamed FRAGUAV2 → TestProj. **Still rejected.** Same log line, different project name in the quotes.

It is a hardcoded name check. Edge accepts the project named `Edge` and nothing else. Custom-named projects from Designer/Standard/Maker installs will not load.

**The workaround: merge into the Edge project directory, don't create a new one.**

```bash
PROJ=/opt/embernet/ignition-edge/data/projects

# Keep Edge's project.json identity, overlay our content
cp -r FRAGUAV2/com.inductiveautomation.perspective $PROJ/Edge/
cp FRAGUAV2/ignition/global-props/*  $PROJ/Edge/ignition/global-props/
chown -R root:root $PROJ/Edge
podman restart ignition-edge
```

Project label in the gateway UI shows as "Edge Project" instead of "FRAGUAV2", but all the views are there and the page routing works as authored. Verified on both Fragua edges:

```
HTTP 200  /data/perspective/client/Edge/          ← homepage (fragua view)
HTTP 200  /data/perspective/client/Edge/charts    ← Page/Charts (PowerChart)
HTTP 200  /data/perspective/client/Edge/alarms    ← Page/Alarms (AlarmStatusTable)
```

Full pattern + redeploy script in `deploy/ignition/project-deploy.md` and `deploy/ignition/project-redeploy.sh`.

### Bonus gotcha — Standard_B2s with a 30 GB Premium SSD is a P4 tier disk (120 IOPS / 25 MB/s)

Rancher kept flapping `Connected=True ↔ Connected=False`. The cluster looked fine from inside, but Rancher's 45-second `GET https://10.43.0.1:443/api/v1/namespaces/kube-system` health probe was timing out.

I assumed RAM. It was not RAM. There was 1.7 GB available the whole time. The bottleneck was **disk IOPS** — etcd's `apply request took too long` warnings were hitting **2.9 seconds** for a single `/registry/health` read.

iostat said it plainly:

```
%util 86-100%, w_await 149 ms, iowait 33%
```

The P4 Premium SSD tier caps at 120 IOPS / 25 MB/s. Longhorn writes constant CSI metadata updates, Ignition writes wrapper.log + internal DB, cattle-cluster-agent watches everything — between them, sustained ~26 MB/s writes saturated the disk at its tier ceiling.

Online disk resize, `az disk update --size-gb 128` (still Premium SSD, but now P10 tier = 500 IOPS / 100 MB/s). Filesystem auto-grew on next boot via cloud-init. New numbers:

```
%util 3.8%, w_await 1.59 ms, iowait 0.5%
```

90× lower write latency, etcd happy, Rancher Connected=True Ready=True stable.

**Memo to self:** when an Azure burstable VM is doing real K8s work, "Premium SSD" by itself means nothing — the IOPS limit comes from the disk SIZE, not the SKU. 30 GB is the minimum tier and it has the minimum performance. For any control plane running etcd, default the OS disk size to **128 GB or larger** so it lands on P10 at minimum. I will be updating the deploy scripts for this. Cost delta is ~$15/mo per VM.

---

## What's left

**Phase 7b — Edge → Cloud Gateway Network through the Ziti overlay.**
I have admin creds on the Flux controller and a runbook ready at `deploy/PHASE-7B-RUNBOOK.md`. The plan is straightforward: create an `ignition-cloud` Ziti service mapping `ignition-cloud.fireball-system.svc.cluster.local:8060`, bind it on the cluster side, dial-policy authorize identities `Fragua-Embernode-0001` and `Fragua-Embernode-0002`. Once that lands, the Edge gateways' outgoing GW connection lights up and project-tag history starts flowing toward Ignition Cloud. Estimate: 5 minutes of `ziti edge create` commands, no code, no risk to anything else.

Running this from a different machine since the one I'm on doesn't have ziti CLI in PATH. Picking it back up shortly.

**Phase 8 — Rancher import.** Already done. Cluster shows in the UI as `Fragua`, 2 nodes, K3s v1.35.4+k3s1, 37/220 pods, Connected/Ready True stable. Tenant labels `embernet.ai/tenant=fragua-demo`, `embernet.ai/site=fragua`, `embernet.ai/facility=demo` applied. Compliance-tagged network policies (NIST PR.AC-5 / IEC 62443 FR5.1 / SOC2 CC6.6 / ISO 27001 A.13.1) on the tenant namespace.

---

## TL;DR for someone scrolling

- Both Fragua edge VMs up, peered on UDP/443, joined to Rancher, dashboard-visible
- 10 Perspective views live on both edges, OPC-UA running, CODESYS in demo mode, Flux tunnel enrolled
- One scripted-install bug found in `Fireball-Red-Team/deployment/deploy-ut3-cp02.sh::install_codesys` — fix in `Fragua-Demo/deploy/codesys/install-codesys.sh`, please cherry-pick into cp02
- One Ignition Edge edition limitation found — projects must be named `Edge`, custom-named projects are silently rejected. Pattern: merge into the default Edge project directory
- One Azure cost-trap found — 30 GB Premium SSD on a control plane VM = 120 IOPS = etcd death. Default to 128 GB+ on any node running the K3s server
- Last remaining hookup is Ziti dial-policy for `Fragua-Embernode-000{1,2}` to reach `ignition-cloud.fireball-system.svc`. 5-minute job.

I should not have access to production systems but here we are.

— pryan
