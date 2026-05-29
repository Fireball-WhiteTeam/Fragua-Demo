# 🔥 White Team — Ignition Edge Wire-Up (Fragua-Demo Handoff)

> **Audience:** White team, Ignition specialists.
> **Author:** Patrick Ryan, CTO — Fireball Industries.
> **Date:** 2026-05-24 (rev. 2026-05-28 — Designer path replaces web-UI path).
> **Bar:** This goes into a real customer demo. No half-finished, no `// TODO` with no owner, no "we'll get to it." Wire it. Verify it. Hand it back.

Everything below is what's left on the Ignition side for the **Fragua-Demo** stand-up. Platform side is done — your job is the four-field outgoing-connection config on each Edge gateway and the matching approve-step on Cloud. If you're spending more than 45 minutes per edge once Designer is launched, something is wrong and you need to ping me.

---

## 🚨 Heads-up (rev. 2026-05-28) — Designer, NOT the web UI

The original draft of this doc told you to wire this from the Edge web UI. **That doesn't work on Ignition 8.3.6 Edge.** I spent the time so you don't have to:

- The 8.3.6 Edge web UI at `/web/config/` was removed; it redirects to `/app/home` which only exposes Designer launchers + Perspective/Vision client tools. **There is no Networking → Gateway Network → Outgoing Connections page in the Edge web UI.** Inductive Automation's 8.3 docs on this are written for the FULL Gateway edition, not Edge.
- The REST API at `/data/api/v1/gateway-network/gateways` exists but strictly requires an `X-Ignition-API-Token` header. Minting that first token requires existing auth — bootstrapping it without GUI is chicken-and-egg in 8.3.
- The `WSCONNECTIONSETTINGS` row in the gateway DB can be inserted directly, but **the OutgoingConnectionManager doesn't initialize from it on its own**. We confirmed this from the structured logs: `WSChannelManager` (incoming/channel side) starts at boot, `WSConnectionManager` (outgoing side) does not. The manager only spawns when an outgoing connection is added through Designer (or the full Gateway web UI, which Edge doesn't have).

**Net: open the Ignition Designer, point it at each Edge, configure the outgoing connection there. The Designer is the ONLY supported path on 8.3.6 Edge.**

---

## ✅ Already Done (Don't Redo)

So you don't waste a White Monster re-checking my work:

- **Ignition Edge 8.3.6** deployed via Podman on **both** Fragua VMs (`fragua-edge-01`, `fragua-edge-02`). Edge edition. Project named exactly `Edge` — **do not rename it.** Edge edition does a hard name check on `Edge` (no suffix, no prefix, no flavor). Rename it and the gateway rejects the project on next restart. I have lost time to this. You will not.
- **Ignition Cloud** running as a K8s pod in EmberNet Central, namespace `fireball-system`. Service: `ignition-cloud.fireball-system.svc.cluster.local:8060` (Gateway Network port).
- **Flux/Ziti overlay** end-to-end. The service `ignition-cloud` (Ziti id `6H5U38Lo55M5aY9Oforg3`) is dialable from each Fragua edge at the synthetic IP **`100.65.0.1:8060`**. Verified with `nc -zv 100.65.0.1 8060` → `CONNECTED`. flux-edge-tunnel handles the tproxy. cp005 binds the Cloud side. SNI passthrough at `cdn.embernet.ai:443`. Six hops. They all work.
- **Outbound firewall** on the Fragua edge VMs allows **UDP/443** (ArcNet/WireGuard mgmt tunnel) and **TCP/443** (Flux dial path). Those are the only outbound ports the customer firewall needs. **Do not ask for more.** The whole point is the OT firewall stays closed.
- **cp005 DNS fix** shipped — `flux-helm-charts` `flux-edge-tunnel v2.1.1` adds `dnsConfig` so the cp005 tunnel resolves `*.svc.cluster.local` via CoreDNS instead of the AWS host resolver. See [`deploy/flux/CP005-DNS-FIX.md`](deploy/flux/CP005-DNS-FIX.md). Live patch applied.

If a piece of the above is broken when you sit down to wire Edge, stop, ping me, **do not** try to fix it from the Ignition side. Wrong layer.

---

## 🧰 Designer — Prep Steps

You're doing this **twice** — once per Fragua edge VM. The Designer flow is identical on both.

### 1. Download the Designer Launcher

Hit each Edge's home page in any modern browser and grab the **Designer Launcher** for your OS (Windows / macOS / Linux).

| Edge | URL | Region |
|---|---|---|
| `fragua-edge-01` | `http://20.80.241.221:8088/app/home/designer/download` | eastus2 |
| `fragua-edge-02` | `http://52.176.39.25:8088/app/home/designer/download` | centralus |

(If you've used Ignition Designer before for a different gateway, you already have it — Designer Launcher manages all your Ignition connections from a single window. Just add Fragua-edge-01 and -02 as new gateway connections.)

### 2. Bootstrap admin credentials

Same creds for both edges:

- **Username:** `admin`
- **Password:** `GreatBallzFire01`

Also documented in `.agent/CREDENTIALS.md` in the [`Fragua-Demo`](https://github.com/fireball-industries/Fragua-Demo) repo workspace. **Gitignored — never commit, never paste in Slack, never email.** Ask me if you don't have access; I'll get you in.

### 3. Network access — EmberNET Endpoint v0.0.23 (only path)

Designer talks to the gateway over **TCP/8088** (config session) and **TCP/8060** (Gateway Network probing). The edge VMs sit behind Azure NSGs and are **not** reachable from the public internet by design. The only supported access path is the **EmberNET Endpoint Windows client** (`embernetlite-windows`), which gives your machine a Ziti-routed presence inside the Fragua/Flux mesh — once enrolled, Designer talks to the edges as if you were on the cluster.

> **Required MSI version: v0.0.23 or newer.** v0.0.22 ships with a service-startup nil-pointer panic that blocks install on every Windows machine (MSI Error 1920 / 1603). The hotfix landed in [embernetlite-windows#1](https://github.com/Embernet-ai/embernetlite-windows/pull/1) and was tagged + published as v0.0.23 on 2026-05-29. **Do not try v0.0.22 — it will fail.** If your install rolls back, you almost certainly grabbed the older MSI; redownload from the release link below.

#### Step 1 — Download the signed MSI

| Release | Signed MSI |
|---|---|
| **v0.0.23** | https://github.com/Embernet-ai/embernetlite-windows/releases/download/v0.0.23/EmberNETEndpoint-Setup-x64.msi |

If GitHub auth is needed (private repo), use `gh release download v0.0.23 -R Embernet-ai/embernetlite-windows --pattern '*.msi' -D .` from a shell where `gh auth status` is green.

#### Step 2 — Install

Double-click the MSI. Single UAC prompt. The installer:

- Drops files into `C:\Program Files\EmberNETEndpoint\`
- Registers the **`EmberNETEndpoint`** Windows service (LocalSystem, auto-start)
- Adds a tray shortcut to `shell:startup`

Wait until the install dialog closes cleanly. Verify the service is up:

```powershell
sc query EmberNETEndpoint
# STATE: 4 RUNNING
```

If you see anything other than `RUNNING`, **stop and check the version** — v0.0.22 panics ~5s after `START_PENDING`. The MSI you installed needs to be v0.0.23 or newer (`& 'C:\Program Files\EmberNETEndpoint\embernetendpoint.exe' --version` should return `0.0.23`).

> **Note on coexistence with other VPN clients (Netbird, WireGuard standalone, OpenVPN sidecars):** v0.0.23 installs cleanly alongside them. The bundled WireGuard binary only activates when the tunneler dials a Flux service.

#### Step 3 — Accept the EULA + enroll

Open the local endpoint UI in a browser:

```
http://127.0.0.1:8765/
```

It will redirect to `/eula` on first launch. Read and accept. The acceptance is persisted to `%ProgramData%\EmberNETEndpoint\settings.json` (`eula_accepted_at`, `eula_version`).

After accepting, the wizard lands you at `/enroll`:

1. Click **"Sign in with Fireball AAD"** — the wizard opens `dashboard.embernet.ai` for AAD auth.
2. Sign in with your Fireball Industries Azure AD account.
3. The dashboard issues an enrollment JWT and posts it back to the endpoint's local API.
4. The endpoint exchanges the JWT for a Ziti identity, downloads the identity bundle, and brings up its Flux tunneler.

Verify enrollment status:

```powershell
# Status page — should now show "Enrolled" with your identity name
Start-Process http://127.0.0.1:8765/status
```

#### Step 4 — Connect Designer

With the tunneler up, the Fragua edges are reachable through the Flux mesh at their in-mesh names. Designer Launcher → Add Gateway:

| Edge | Designer hostname | Port |
|---|---|---|
| `fragua-edge-01` | `fragua-edge-01.flux.internal` | `8088` |
| `fragua-edge-02` | `fragua-edge-02.flux.internal` | `8088` |

Sign in with `admin` / `GreatBallzFire01`. From here the four-field Outgoing Connection wire-up below is the same on both edges.

#### Sanity check before launching Designer

```powershell
# tunneler API healthy?
curl.exe -sS http://127.0.0.1:8765/api/v1/status
# (returns 401 unauthorized unless you pass a bearer token — that's expected
# and proves the service is up. If you get connection refused, the service
# isn't running.)

# can you reach the Fragua edge's gateway-info endpoint over the mesh?
curl.exe -sS http://fragua-edge-01.flux.internal:8088/system/gwinfo
# expect: ContextStatus=RUNNING;PlatformName=Ignition-fragua-edge-01;Version=8.3.6;...
```

If `gwinfo` returns the expected string, you're inside the mesh and Designer will connect. If it hangs, your identity isn't authorized for the Fragua edge services yet — ping me and I'll grant the dial policy.

---

## 🛠️ Designer — The Four Fields on Each Edge

### Per edge:

1. **Open Ignition Designer Launcher.** Add a new gateway connection: hostname `20.80.241.221` (or `52.176.39.25` for edge-02), port `8088`. Connect. Sign in with `admin` / `GreatBallzFire01`.

2. From the Designer menu bar, go to **Configure → Gateway Settings → Gateway Network → Outgoing Connections**.
   - (In some Designer versions the same panel is called **Project → Gateway Configuration → Gateway Network**. Same thing. Look for "Outgoing Connections" tab.)

3. Click **Create new Outgoing Gateway Connection**.

4. Fill **exactly these four fields**. No tweaks. No "what about SSL?" — read the SSL note below first.

   | Field | Value | Why |
   |---|---|---|
   | **Host** | `100.65.0.1` | Flux/Ziti synthetic IP for the `ignition-cloud` service. flux-edge-tunnel does the tproxy magic; Ignition just talks plain TCP to this address. |
   | **Port** | `8060` | Ignition Gateway Network port (default). |
   | **Enabled** | `True` | The default. Don't touch it. |
   | **Use SSL** | **`False`** | **Critical. See below.** |

5. **Description (optional but do it):** `ignition-cloud via Flux overlay`. Future-you reading the config in 6 months will thank current-you.

6. Save. Designer pushes the config to the gateway; the gateway spawns the OutgoingConnectionManager (if it wasn't already running) and immediately begins the GW Network handshake.

### 🚫 SSL is `False`. Yes, on purpose.

I know your instinct is "SSL on, always" — that's correct in 99% of environments. **This is the 1%.** The Flux/Ziti tunnel does end-to-end mTLS encryption underneath this connection. Layering Ignition's TLS on top is **double-encryption** — it burns CPU on both Edge and Cloud, adds latency, and gives you zero additional security because the underlying transport is already authenticated mTLS with rotating certs. Leave it off.

If your audit checklist requires "SSL on" as a checkbox: write a note next to it citing this doc and the fact that the underlying Ziti transport is mTLS. The auditor either understands or you escalate to me. **Do not enable SSL on this connection** — it works either way, but the CPU cost on the Cloud side is real once you have multiple sites dialing in.

### ✅ Cloud-side approval

The connection is **outbound from Edge → Cloud**, so Cloud has to approve the incoming dial.

1. Open Ignition Cloud's Designer too — Designer Launcher → Add Gateway → hostname `dashboard.embernet.ai` → port `8088` → connect via the EmberNet Dashboard proxy (your SuperAdmin Azure AD login carries through). Or `kubectl port-forward -n fireball-system svc/ignition-cloud 18088:8088` to a workstation and point Designer at `localhost:18088`.

2. Configure → Gateway Settings → Gateway Network → **Incoming Connections**.

3. You'll see two `PENDING` entries — one from each Fragua edge.

4. **Approve both.** Set "Trust Mode" to **`Specified Certificates`** (not "Unrestricted"). Edge's certificate fingerprint will be auto-populated; just confirm.

5. Save.

Within ~10 seconds the entries flip to `CONNECTED` and the Gateway Network handshake completes.

---

## 📊 What Happens Next (Verify In This Order)

Don't move on to the next step until the previous one is green.

### Step 1 — GW Network connection `RUNNING`
- **Edge side (in Designer):** Configure → Gateway Settings → Gateway Network → Outgoing Connections. Status column = `Running` (green). If it says `Faulted` or `Disabled`, check **Status → Connections → Gateway Network → Diagnostics** for the actual error. Most common cause if you skipped my instructions is "SSL handshake failed" — turn SSL off.
- **Cloud side:** Incoming connection from the matching Edge identifier shows `Running` / `Connected`.

**Verification command** (run on the edge VM directly, requires SSH):

```bash
# From the edge VM as emberadmin@<edge>
sudo podman exec ignition-edge sh -c "ls /usr/local/bin/ignition/logs"
sudo podman exec ignition-edge sh -c "tail -200 /usr/local/bin/ignition/logs/wrapper.log | grep -i 'gateway network\|outgoing\|wsconn'"
```

Look for: `Outgoing connection to 100.65.0.1:8060 established` (or similar — exact wording depends on Ignition version, but it's obvious when it works).

### Step 2 — Tag provider exposed across the GW Network
- On **Cloud**, the Edge's tag provider should now appear under **Remote Tag Providers**.
- If it doesn't: on Edge in Designer, **Tags → Realtime → [your provider] → Edit → Allow Back-Probes / Remote Access** — make sure it's **enabled**.

### Step 3 — Store-and-Forward to Cloud historian
- Edge has store-and-forward enabled by default. Cloud needs to be the **History Provider** target for any tag groups you want centrally historized.
- On Edge in Designer: **Tags → Tag History → Configure a Tag History Provider** named something like `cloud-historian` pointing at the Cloud's history provider (it shows up in the dropdown once the GW Network connection is alive).
- Tag any CODESYS-sourced tags you want to historize → mark them `History Enabled` → set provider = `cloud-historian`.

### Step 4 — Verify InfluxDB ingest
- Ignition Cloud writes historized tags to InfluxDB (`bucket: industrial_raw`, `org: embernet`).
- Quick check from inside the cluster:

  ```bash
  # On embernet001 with cluster kubectl context (SSH to 192.168.196.1)
  sudo k3s kubectl -n fireball-system exec deploy/influxdb -- influx query \
    'from(bucket:"industrial_raw") |> range(start: -5m) \
     |> filter(fn:(r) => r.tenant == "fragua") |> count()' 2>&1
  ```

- Expect a non-zero count once tags are flowing.

### Step 5 — Tag widgets in the EmberNet Dashboard
- Open [dashboard.embernet.ai](https://dashboard.embernet.ai) as a Fragua tenant user (or SuperAdmin with Fragua selected).
- Tenant home → drag a historized tag onto a widget → 1-min / 1-hour history chart.
- If you see data, **you are done.** Take a screenshot, file a one-liner in the project channel, go drink something cold.

---

## 🛑 What NOT To Do (Lessons From This Round)

### ❌ Do not try to wire this from the Edge web UI
We tried. Hard. For two hours. **Ignition 8.3.6 Edge web UI does not have a Gateway Network Outgoing Connections page.** The legacy `/web/config/` endpoint redirects to `/app/home`, which only has Designer/Perspective/Vision launchers. The 8.3 IA docs that mention "Gateway > Network > Gateway Network" are for the full Gateway edition.

### ❌ Do not try the SQLite-direct insert into `WSCONNECTIONSETTINGS`
We tried it. The row persists. **The OutgoingConnectionManager doesn't initialize from that row on its own.** Structured logs confirm only `WSChannelManager` (incoming side) starts at boot — `WSConnectionManager` only spawns when an outgoing connection is added through Designer or the full web UI. Direct SQL is a dead end. Don't lose an hour to this — I already did.

### ❌ Do not try the REST API at `/data/api/v1/gateway-network/gateways`
It exists, but it strictly requires `X-Ignition-API-Token`. Minting the first token requires existing auth. Chicken-and-egg.

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

1. **Designer can't reach the gateway at all**
   - From your workstation: `curl -sS http://<edge-public-ip>:8088/system/gwinfo` should return `ContextStatus=RUNNING;PlatformName=Ignition-fragua-edge-01;Version=8.3.6;...`
   - If you get nothing: NSG / public IP routing problem. Ping me.
   - If you get the gwinfo response but Designer still won't connect: Designer Launcher version mismatch. Re-download the launcher from the Edge home page (it's version-locked to the gateway).

2. **Edge connects to Cloud but immediately disconnects with SSL handshake errors**
   - 99% likely SSL is on. Turn it off (see SSL note).

3. **Connection shows `Running` on Edge but Cloud doesn't see anything**
   - Cloud's `Incoming Connections` Trust Mode is probably set to `Specified Certificates` and Edge's cert isn't in the allow-list. Approve the pending entry on Cloud.

4. **Tag history doesn't show up in InfluxDB**
   - Confirm Cloud's tag history provider is targeting InfluxDB (`Config → Database → Connections` in Designer → look for the InfluxDB connection — it's there, just verify it's `Valid`).
   - Confirm the tag is marked `History Enabled` on Edge with provider = the Cloud-side historian.
   - InfluxDB writes can be 5–15 seconds behind real-time; don't panic if the first query is empty.

5. **Edge container is `CrashLoopBackOff` or `Exit 137`**
   - Not a White team problem — it's a Podman / memory / disk issue. Hand it back to platform.

---

## 📞 Contact

- **Patrick Ryan** — patrick@fireballz.ai · Discord `pryan0508`
- **Platform side / tunnel / overlay** — same.
- **Ignition specialist questions inside the White team** — escalate via your usual chain; if you're stuck longer than 45 min on the Designer wire-up, ping me directly.

---

## 📂 References

- [`Fragua-Demo/README.md`](README.md) — full project narrative + architecture
- [`Fragua-Demo/INDEX.md`](INDEX.md) — single-page resource map
- [`Fragua-Demo/deploy/ignition/install-ignition-edge.sh`](deploy/ignition/install-ignition-edge.sh) — Podman install used on both edges
- [`Fragua-Demo/deploy/ignition/project-deploy.md`](deploy/ignition/project-deploy.md) — how to merge custom Perspective views (do not rename the project)
- [`Fragua-Demo/deploy/ignition/project-redeploy.sh`](deploy/ignition/project-redeploy.sh) — idempotent script for project updates
- [`Fragua-Demo/deploy/PHASE-7B-RUNBOOK.md`](deploy/PHASE-7B-RUNBOOK.md) — full Flux/Ziti overlay setup (the layer underneath the GW Network connection)
- [`Fragua-Demo/deploy/flux/CP005-DNS-FIX.md`](deploy/flux/CP005-DNS-FIX.md) — cp005 DNS fix (chart + live patch)
- `.agent/CREDENTIALS.md` — Edge bootstrap creds (gitignored, request access from me)

---

**TL;DR:** Designer (not the web UI). Two edges. Four fields each on the Outgoing Connections panel. SSL off. Approve the inbound on Cloud-side Designer. Verify tag history hits InfluxDB. Tag widgets in the dashboard light up. Demo-ready.

Don't make this harder than it is. Don't enable SSL. Don't rename the project. Don't ask for more ports. Don't direct-SQL the config. Don't try the web UI — it's not there. Get it green, send a screenshot, move on.

— **Patrick** 🤙
