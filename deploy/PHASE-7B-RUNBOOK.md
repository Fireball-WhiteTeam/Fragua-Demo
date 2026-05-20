# Phase 7b — Ziti Service for `ignition-cloud` (Fragua Edge → Cloud GW)

**Purpose.** Authorize the two Fragua edge nodes to dial Ignition Cloud through
the Flux/Ziti zero-trust overlay, so each Edge gateway's "outgoing Gateway
Network connection" lands on `ignition-cloud.fireball-system.svc.cluster.local`
via the Ziti tunnel (not the public internet).

**Run this on a machine that already has Ziti CLI access to
`flux.embernet.ai` AND `kubectl` context to the EmberNet K3s cluster.**

---

## Inputs you'll need

| Var | Value |
|---|---|
| Ziti controller URL | `https://flux.embernet.ai:443` (via `kubectl port-forward` to `localhost:1280` if dialing from in-cluster) |
| Admin user | `admin` |
| Admin password | `GFTJcmyp4RntNjwgPE4Ntdqz2gJkpjfj` |
| Fragua identity #1 | `Fragua-Embernode-0001` (already enrolled on `fragua-edge-01`) |
| Fragua identity #2 | `Fragua-Embernode-0002` (already enrolled on `fragua-edge-02`) |
| Service hostname | `ignition-cloud.fireball-system.svc.cluster.local` |
| Service port | `8060` (Gateway Network) — or `8088` if your Ignition Cloud frontends GW over HTTP |
| Intercept range on edges | `100.65.0.0/16` (flux-edge-tunnel default — DON'T overlap WG 100.64) |

---

## Step 1 — Port-forward to the controller (in-cluster admin API)

The management API is only reachable cluster-internal because of TLS SAN
restrictions. Open the forward from a machine with kubectl context to the
**EmberNet** cluster (i.e. an embernet001 shell or any node with the right kubeconfig):

```bash
kubectl port-forward -n flux-system svc/flux-controller-client 1280:443 &>/dev/null &
sleep 2
```

Confirm reachable:
```bash
curl -sk https://localhost:1280/edge/management/v1/version | jq .data.version
# expect: "2.0.2" (OpenZiti 2.0.2)
```

---

## Step 2 — Login to Ziti

```bash
ziti edge login https://localhost:1280 \
  -u admin -p 'GFTJcmyp4RntNjwgPE4Ntdqz2gJkpjfj' --yes
```

Token is written to `~/.config/ziti/ziti-cli.json`. Valid until next admin
password change.

---

## Step 3 — Verify Fragua identities are enrolled

```bash
ziti edge list identities 'name contains "Fragua-Embernode"'
```

You should see two entries with `IsAdmin=false`. Both are already enrolled
(`hasEdgeRouterConnection=true`) — flux-edge-tunnel pods on the Fragua cluster
brought them online on 2026-05-19.

Capture their IDs into shell vars (the CLI accepts either ID or name as
`@<name>` in role refs, but explicit IDs are safer for scripting):

```bash
ID_01=$(ziti edge list identities 'name="Fragua-Embernode-0001"' --output-json | jq -r '.data[0].id')
ID_02=$(ziti edge list identities 'name="Fragua-Embernode-0002"' --output-json | jq -r '.data[0].id')
echo "ID_01=$ID_01  ID_02=$ID_02"
```

---

## Step 4 — Check whether an `ignition-cloud` service already exists

```bash
ziti edge list services 'name contains "ignition"'
ziti edge list configs  'name contains "ignition"'
```

If a service named `ignition-cloud` (or close) is already configured for the
EmberNet cp-001/004/005 identities, you only need to add a service-policy
authorizing the Fragua identities to dial it (skip to Step 6).

If nothing exists, create configs + service (Steps 4a–4c).

### 4a — Intercept config (what the dialing side sees as a hostname)

This is what the tunnel pod on each Fragua edge intercepts. DNS for the
hostname resolves to a synthetic IP in `100.65.0.0/16`, traffic is
captured, and Ziti routes it to whatever the bind side is.

```bash
ziti edge create config "ignition-cloud-intercept.v1" intercept.v1 '{
  "protocols": ["tcp"],
  "addresses": ["ignition-cloud.fireball-system.svc.cluster.local"],
  "portRanges": [{"low": 8060, "high": 8060}]
}'
```

### 4b — Host config (where the receiving side terminates)

This says: in the EmberNet cluster, terminate the tunnel by dialing the
real K8s service `ignition-cloud.fireball-system.svc.cluster.local:8060`.

```bash
ziti edge create config "ignition-cloud-host.v1" host.v1 '{
  "protocol": "tcp",
  "address":  "ignition-cloud.fireball-system.svc.cluster.local",
  "port":     8060
}'
```

### 4c — Service binding the two configs

```bash
ziti edge create service "ignition-cloud" \
  --configs ignition-cloud-intercept.v1,ignition-cloud-host.v1
```

---

## Step 5 — Bind side: which identity hosts the service?

The host side runs inside the EmberNet cluster (so it can dial the K8s
service). Typically a tunnel identity on `embernet-cp-001` (or 004/005)
is the binder. Verify:

```bash
ziti edge list service-policies 'name contains "ignition-cloud-bind"'
```

If no bind policy exists, create one targeting your existing cluster-side
identity (e.g. `embernet-cp-001`):

```bash
ziti edge create service-policy "ignition-cloud-bind" Bind \
  --service-roles "@ignition-cloud" \
  --identity-roles "@embernet-cp-001"
```

(Adjust the identity-role if your cluster-internal binder identity has a
different name.)

---

## Step 6 — Dial side: authorize Fragua identities

This is the new policy that lets the Fragua edges dial the service:

```bash
ziti edge create service-policy "fragua-ignition-cloud-dial" Dial \
  --service-roles  "@ignition-cloud" \
  --identity-roles "@Fragua-Embernode-0001,@Fragua-Embernode-0002"
```

Confirm:

```bash
ziti edge list service-policies 'name contains "fragua-ignition-cloud"'
```

---

## Step 7 — Verify from a Fragua edge

SSH to `fragua-edge-01` (or -02) and resolve the hostname. The
flux-edge-tunnel pod intercepts DNS for any Ziti service the identity is
authorized to dial:

```bash
ssh emberadmin@20.80.241.221       # password: GreatBallzFire01
getent hosts ignition-cloud.fireball-system.svc.cluster.local
# expect a 100.65.x.x address (NOT SERVFAIL)

nc -w 3 -zv ignition-cloud.fireball-system.svc.cluster.local 8060
# expect: succeeded
```

The first time you query, the tunnel pod fetches the service definition
from the controller — give it ~30 seconds after the policy lands.

---

## Step 8 — Wire up the Ignition Edge "Outgoing GW Connection"

On each Fragua Edge gateway web UI (`http://<edge>:8088/web/config/gateway-network`):

1. Add **Outgoing Connection**
2. **Host:** `ignition-cloud.fireball-system.svc.cluster.local`
3. **Port:** `8060`
4. **SSL:** No (the Ziti tunnel handles confidentiality)
5. Save → reload

Within ~60s the Edge gateway should show the connection as `Connected` and
project-tag history starts flowing toward Ignition Cloud.

Repeat for both edges. Same hostname/port from both — Ziti picks the
right edge router automatically.

---

## Cleanup (when done with the port-forward)

```bash
pkill -f "kubectl port-forward.*1280"
```

---

## Quick paste-anywhere block (all of Steps 2–6 in one go)

```bash
# Run from a host with kubectl context to EmberNet cluster + ziti CLI in PATH
kubectl port-forward -n flux-system svc/flux-controller-client 1280:443 &>/dev/null &
sleep 3
ziti edge login https://localhost:1280 -u admin -p 'GFTJcmyp4RntNjwgPE4Ntdqz2gJkpjfj' --yes

# Confirm Fragua identities exist
ziti edge list identities 'name contains "Fragua-Embernode"'

# Create service (skip if it already exists)
ziti edge create config 'ignition-cloud-intercept.v1' intercept.v1 \
  '{"protocols":["tcp"],"addresses":["ignition-cloud.fireball-system.svc.cluster.local"],"portRanges":[{"low":8060,"high":8060}]}'
ziti edge create config 'ignition-cloud-host.v1' host.v1 \
  '{"protocol":"tcp","address":"ignition-cloud.fireball-system.svc.cluster.local","port":8060}'
ziti edge create service 'ignition-cloud' --configs ignition-cloud-intercept.v1,ignition-cloud-host.v1

# Bind policy (use an existing cluster-internal tunnel identity)
ziti edge create service-policy 'ignition-cloud-bind' Bind \
  --service-roles '@ignition-cloud' --identity-roles '@embernet-cp-001'

# Dial policy for Fragua
ziti edge create service-policy 'fragua-ignition-cloud-dial' Dial \
  --service-roles '@ignition-cloud' --identity-roles '@Fragua-Embernode-0001,@Fragua-Embernode-0002'

pkill -f "kubectl port-forward.*1280"
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `getent hosts ...` returns SERVFAIL on edge | Service exists but identity isn't authorized | Re-check `ziti edge list service-policies` includes `Fragua-Embernode-*` |
| `nc -zv` succeeds but Edge GW won't connect | Wrong port (try 8088 if 8060 is HTTP-fronted) | Check Ignition Cloud's GW listen port; adjust both intercept + host configs |
| `getent` resolves but `nc` times out | No bind-side identity claims the service | Create the `Bind` policy (Step 5) |
| Service appears but Edge gateway tunnel reports "no path" | flux-edge-tunnel pod hasn't refreshed | `kubectl rollout restart ds -n flux-system -l app.kubernetes.io/name=flux-edge-tunnel` on the Fragua cluster |

---

*Auto-generated 2026-05-19 by Claude (Opus 4.7). Copy this file to the
machine that has Ziti + EmberNet kubectl context, then execute Steps 2–6
verbatim. ~5 minutes start to finish.*
