# 🔥 White Team — Ignition Edge Wire-Up (Fragua-Demo Handoff)

> **Audience:** White team, Ignition specialists.
> **Author:** Patrick Ryan, CTO — Fireball Industries.
> **Date:** 2026-05-24.
> **Bar:** This goes into a real customer demo. No half-finished, no `// TODO` with no owner, no "we'll get to it." Wire it. Verify it. Hand it back.

Everything below is what's left on the Ignition side for the **Fragua-Demo** stand-up. Platform side is done — your job is the four-field web-UI config on each Edge gateway and the matching approve-step on Cloud. That's it. If you're spending more than 30 minutes per edge, something is wrong and you need to ping me.

---

## ✅ Already Done (Don't Redo)

So you don't waste a White Monster re-checking my work:

- **Ignition Edge 8.3.6** deployed via Podman on **both** Fragua VMs (`fragua-edge-01`, `fragua-edge-02`). Edge edition. Project named exactly `Edge` — **do not rename it.** Edge edition does a hard name check on `Edge` (no suffix, no prefix, no flavor). Rename it and the gateway rejects the project on next restart. I have lost time to this. You will not.
- **Ignition Cloud** running as a K8s pod in EmberNet Central, namespace `fireball-system`. Service: `ignition-cloud.fireball-system.svc.cluster.local:8060` (Gateway Network port).
- **Flux/Ziti overlay** end-to-end. The service `ignition-cloud` (Ziti id `6H5U38Lo55M5aY9Oforg3`) is dialable from each Fragua edge at the synthetic IP **`100.65.0.1:8060`**. I verified this myself with `nc -zv 100.65.0.1 8060` → `CONNECTED`. flux-edge-tunnel handles the tproxy. cp005 binds the Cloud side. SNI passthrough at `cdn.embernet.ai:443`. Six hops. They all work.
- **Outbound firewall** on the Fragua edge VMs allows **UDP/443** (ArcNet/WireGuard mgmt tunnel) and **TCP/443** (Flux dial path). Those are the only outbound ports the customer firewall needs. **Do not ask for more.** The whole point is the OT firewall stays closed.

If a piece of the above is broken when you sit down to wire Edge, stop, ping me, **do not** try to fix it from the Ignition side. Wrong layer.

---

## 🛠️ What's Left — The Four Fields on Each Edge

You're doing this **twice** — once per Fragua edge VM. The wire-up is identical on both.

### 🌐 Web UI access

| Edge | URL | Notes |
|---|---|---|
| `fragua-edge-01` | `http://20.80.241.221:8088` | eastus2 |
| `fragua-edge-02` | `http://52.176.39.25:8088` | centralus |

Bootstrap admin credentials are in `.agent/CREDENTIALS.md` in the [`Fragua-Demo`](https://github.com/fireball-industries/Fragua-Demo) repo workspace. **Gitignored — never commit, never paste in Slack, never email.** Ask me if you don't have access to the workspace; I'll get you in.

### 🔧 Step-by-step (per edge)

1. Open the Edge web UI. Log in with the bootstrap credentials.
2. Navigate: **Config → Networking → Gateway Network → Outgoing Connections → Create new Outgoing Gateway Connection**.
3. Fill **exactly these four fields**. Nothing else. No tweaks. No "what about SSL?" — read the SSL note below first:

   | Field | Value | Why |
   |---|---|---|
   | **Host** | `100.65.0.1` | Flux synthetic IP for the `ignition-cloud` service. flux-edge-tunnel does the tproxy magic; Ignition just talks plain TCP to this address. |
   | **Port** | `8060` | Ignition Gateway Network port. |
   | **Enabled** | `True` | The default. Don't touch it. |
   | **Use SSL** | `False` | **Critical. See below.** |

4. **Description (optional but do it):** `ignition-cloud via Flux overlay`. Future-you reading the config in 6 months will thank current-you.
5. Save.

### 🚫 SSL is `False`. Yes, on purpose.

I know your instinct is "SSL on, always" — that's correct in 99% of environments. **This is the 1%.** The Flux/Ziti tunnel is doing end-to-end mTLS encryption underneath this connection. Layering Ignition's TLS on top is **double-encryption** — it burns CPU on both Edge and Cloud, adds latency, and gives you zero additional security because the underlying transport is already authenticated mTLS with rotating certs. Leave it off.

If your audit checklist requires "SSL on" as a checkbox: write a note next to it citing this doc and the fact that the underlying Ziti transport is mTLS. The auditor either understands or you escalate to me. **Do not enable SSL on this connection** — it works either way, but the CPU cost on Cloud-side is real once you have multiple sites dialing in.

### ✅ Cloud-side approval

The connection is **outbound from Edge → Cloud**, so Cloud has to approve the incoming dial.

1. Open Ignition Cloud's web UI — proxied through the EmberNet Dashboard. From [dashboard.embernet.ai](https://dashboard.embernet.ai), Fragua tenant → Apps → `ignition-cloud` → Open. (Or hit the cluster directly via kubectl port-forward if the dashboard's still mid-fix on the tenant view bug — see open-items doc.)
2. **Config → Networking → Gateway Network → Incoming Connections**.
3. You'll see two `PENDING` entries — one from each Fragua edge.
4. **Approve both.** Set "Trust Mode" to **`Specified Certificates`** (not "Unrestricted"). Edge's certificate fingerprint will be auto-populated; just confirm.
5. Save.

Within ~10 seconds the entries flip to `CONNECTED` and the Gateway Network handshake completes.

---

## 📊 What Happens Next (Verify In This Order)

Don't move on to the next step until the previous one is green.

### Step 1 — GW Network connection `RUNNING`
- **Edge side:** Status indicator on the outgoing connection turns green / `Running`. If it says `Faulted` or `Disabled`, check the **Gateway → Diagnostics → Logs** for the actual error. The most common cause if you skipped my instructions is "SSL handshake failed" — turn SSL off.
- **Cloud side:** Incoming connection from the matching Edge identifier shows `Running` / `Connected`.

**Verification command** (run on the edge VM directly, requires SSH):

```bash
# From the edge VM as emberadmin@<edge>
sudo k3s kubectl -n fireball-system get pod -l app.kubernetes.io/name=ignition-edge
sudo k3s kubectl -n fireball-system logs deploy/ignition-edge --tail=80 | grep -i "gateway network\|gan\|outgoing"
```

Look for: `Outgoing gateway connection to ignition-cloud established` (or similar — exact wording depends on Ignition version, but it's obvious when it works).

### Step 2 — Tag provider exposed across the GW Network
- On **Cloud**, the Edge's tag provider should now appear under **Remote Tag Providers**.
- If it doesn't: on Edge, **Config → Tags → Realtime → [your provider] → Edit → Allow Back-Probes / Remote Access** — make sure it's **enabled**.

### Step 3 — Store-and-Forward to Cloud historian
- Edge has store-and-forward enabled by default. Cloud needs to be the **History Provider** target for any tag groups you want centrally historized.
- On Edge: **Tag History → Configure a Tag History Provider** named something like `cloud-historian` pointing at the Cloud's history provider (it shows up in the dropdown once the GW Network connection is alive).
- Tag any CODESYS-sourced tags you want to historize → mark them `History Enabled` → set provider = `cloud-historian`.

### Step 4 — Verify InfluxDB ingest
- Ignition Cloud writes historized tags to InfluxDB (`bucket: industrial_raw`, `org: embernet`).
- Quick check from inside the cluster:

  ```bash
  # On embernet001 with cluster kubectl context
  sudo k3s kubectl -n fireball-system exec deploy/influxdb -- influx query 'from(bucket:"industrial_raw") |> range(start: -5m) |> filter(fn:(r) => r.tenant == "fragua-demo") |> count()' 2>&1
  ```

- Expect a non-zero count once tags are flowing.

### Step 5 — Tag widgets in the EmberNet Dashboard
- Open [dashboard.embernet.ai](https://dashboard.embernet.ai) as a Fragua tenant user (or SuperAdmin with Fragua selected).
- Tenant home → drag a tag onto a widget → 1-min / 1-hour history chart.
- If you see data, **you are done.** Take a screenshot, file a one-liner in the project channel, go drink something cold.

---

## 🛑 What NOT To Do (Lessons From This Round)

### ❌ Do not try the SQLite-direct insert into `WSCONNECTIONSETTINGS`
We tried it. The row persists. **Ignition's runtime does NOT pick it up at restart.** The Gateway Network module only re-reads its config via the web UI's save path (or an Edge restart following a `.gwbk` import). Direct SQL is a dead end for Ignition 8.3. Don't lose an hour to this — I already did.

### ❌ Do not enable SSL on the outgoing connection
See above. It's wrapped in mTLS by Flux already. Double-encryption costs CPU and gains nothing.

### ❌ Do not rename the `Edge` project
Edge edition does a hard name check. Project name must be exactly `Edge`. Not `Edge-Fragua`. Not `edge`. Not `EdgeMaster`. `Edge`. If a tenant wants their site name in the project, put it in the **Project Description** field, not the name.

### ❌ Do not request additional outbound firewall ports
**UDP/443** (mgmt) and **TCP/443** (Flux dial). That's the contract with the customer's OT firewall team. If you find yourself wanting to open `:8088` or `:8060` or `:8043` outbound, **stop** — that means you're trying to bypass the Ziti tunnel, which means you're solving the wrong problem.

### ❌ Do not change the `image.tag` of the Ignition Edge container without coordinating
The container is `8.3.6`. If you upgrade it, the project format check and the GW Network protocol version need to be re-verified on both sides. Not a White team call alone — ping platform first.

---

## 🆘 If It Breaks

In order of "check this first":

1. **Edge can't reach Cloud at all**
   - On the edge VM: `nc -zv 100.65.0.1 8060`
   - If `FAIL`: it's a tunnel issue, not Ignition. Ping me. Don't waste time in the Ignition UI.
   - If `CONNECTED`: tunnel's fine, Ignition config is the issue.

2. **Edge connects but immediately disconnects with SSL handshake errors**
   - 99% likely SSL is on. Turn it off (see SSL note).

3. **Connection shows `RUNNING` on Edge but Cloud doesn't see anything**
   - Cloud's `Incoming Connections` Trust Mode is probably set to `Specified Certificates` and Edge's cert isn't in the allow-list. Approve the pending entry on Cloud.

4. **Tag history doesn't show up in InfluxDB**
   - Confirm Cloud's tag history provider is targeting InfluxDB (`Config → Database → Connections` → look for the InfluxDB connection — it's there, just verify it's `Valid`).
   - Confirm the tag is marked `History Enabled` on Edge with provider = the Cloud-side historian.
   - InfluxDB writes can be 5–15 seconds behind real-time; don't panic if the first query is empty.

5. **Edge container is `CrashLoopBackOff` or `Exit 137`**
   - This isn't a White team problem — it's a Podman / memory / disk issue. Hand it back to platform.

---

## 📞 Contact

- **Patrick Ryan** — patrick@fireballz.ai · Discord `pryan0508`
- **Platform side / tunnel / overlay** — same.
- **Ignition specialist questions inside the White team** — escalate via your usual chain; if you're stuck longer than 30 min on the wire-up itself, ping me directly.

---

## 📂 References

- [`Fragua-Demo/README.md`](README.md) — full project narrative + architecture
- [`Fragua-Demo/INDEX.md`](INDEX.md) — single-page resource map
- [`Fragua-Demo/deploy/ignition/install-ignition-edge.sh`](deploy/ignition/install-ignition-edge.sh) — Podman install used on both edges
- [`Fragua-Demo/deploy/ignition/project-deploy.md`](deploy/ignition/project-deploy.md) — how to merge custom Perspective views (do not rename the project)
- [`Fragua-Demo/deploy/ignition/project-redeploy.sh`](deploy/ignition/project-redeploy.sh) — idempotent script for project updates
- [`Fragua-Demo/deploy/PHASE-7B-RUNBOOK.md`](deploy/PHASE-7B-RUNBOOK.md) — full Flux/Ziti overlay setup (the layer underneath the GW Network connection)
- `.agent/CREDENTIALS.md` — Edge bootstrap creds (gitignored, request access from me)

---

**TL;DR:** Two edges. Four fields each. SSL off. Approve the inbound on Cloud. Verify tag history hits InfluxDB. Tag widgets in the dashboard light up. Demo-ready.

Don't make this harder than it is. Don't enable SSL. Don't rename the project. Don't ask for more ports. Don't direct-SQL the config. Get it green, send a screenshot, move on.

— **Patrick** 🤙
