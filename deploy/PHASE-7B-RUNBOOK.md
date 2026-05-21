# Phase 7b — Ziti `ignition-cloud` dial path (Fragua → EmberNet)

_Status: **OPERATIONAL** as of 2026-05-21._

The Fragua edge can now dial `ignition-cloud.fireball-system.svc.cluster.local:8060`
through the Flux/Ziti zero-trust overlay. Public-facing traffic stays on port
**443 only** (per the platform constraint), shaped to look like normal HTTPS
to `cdn.embernet.ai`.

This runbook documents the FINAL state so the same path can be reproduced or
extended (e.g. for `embernet-dashboard` callbacks, additional services, more
remote sites).

---

## End-to-end topology

```
   Fragua app   ─dial→  100.65.0.1:8060   (Ziti synthetic IP)
                            │
                            ▼  iptables -t mangle TPROXY
                       127.0.0.1:45609   (flux-edge-tunnel pod, hostNetwork)
                            │
                            ▼  TLS to controller-advertised endpoint
              cdn.embernet.ai:443  ← public DNS A record to 20.10.93.244
                            │
                            ▼  Azure LB :443
                       embernet005 :443   (klipper-lb hostNetwork)
                            │
                            ▼
                       Traefik websecure
                            │  IngressRouteTCP SNI=cdn.embernet.ai, passthrough
                            ▼
              flux-router-edge ClusterIP svc :443  (targetPort 3022)
                            │
                            ▼
              flux-router pod (hostNetwork on embernet005) :3022
                            │
                            ▼  Ziti fabric — terminator
              flux-tunnel-embernet-cp005 DaemonSet pod
                            │
                            ▼
              ignition-cloud.fireball-system.svc.cluster.local:8060
```

---

## Identity / policy state (verified against the controller on 2026-05-21)

| Object | Name | ID | Notes |
|---|---|---|---|
| Edge router | `flux-router-v2` | `P.csD4A7T1` | hostname `cdn.embernet.ai`, supportedProtocols `tls://cdn.embernet.ai:443`, isOnline=true |
| Edge router (legacy, retain for rollback) | `relay-us-east-1` | `nX3YHSQM5A` | offline, can be deleted once new relay is fully validated |
| Service | `ignition-cloud` | `6H5U38Lo55M5aY9Oforg3` | encryptionRequired=true; two configs attached |
| Intercept config | `ignition-cloud-intercept.v1` | `37EMSyG3qLwbwG8WGz8LRd` | tcp, address `ignition-cloud.fireball-system.svc.cluster.local`, port 8060 |
| Host config | `ignition-cloud-host.v1` | `3lyGWHe83UBuPDu9o1tyYU` | tcp, same address+port |
| Bind policy | `ignition-cloud-bind` | `3DUzRUoGjUIFYMrjSpVYzn` | identityRoles `#embernet-control-plane` |
| Dial policy | `fragua-ignition-cloud-dial` | `6BY7TgbusFLUuf2ahigSyV` | identityRoles `@Fragua-Embernode-0001, @Fragua-Embernode-0002` |
| Service-edge-router-policy | `ignition-cloud-routers` | `6xPIdtYrwqVYUk6Tau9np7` | serviceRoles `@ignition-cloud`, edgeRouterRoles `#all` |
| Edge-router-policy (cp side) | `embernet-cp-edge-routers` | `2UiCna1WPVwJtrrU07an8n` | identityRoles `#embernet-control-plane`, edgeRouterRoles `#all` |
| Edge-router-policy (Fragua side) | `fragua-edge-routers` | `4u67VxLW7XFW6aEJsY6Aq3` | identityRoles `@Fragua-Embernode-0001, @Fragua-Embernode-0002`, edgeRouterRoles `#all` |
| Edge-router-policy (probe side) | `embernet-probes-routers` | `5iYvH6W1htgC4fvcccs0Yg` | identityRoles `#embernet-probes`, edgeRouterRoles `#all` |

The two embernet-cp / Fragua identities (cp005 and Fragua-Embernode-0001) are
the load-bearing pair for this demo. cp005 binds, Fragua-Embernode-0001
dials. Both `hasEdgeRouterConnection: true` after the router fix.

---

## How to verify the dial path

```bash
# On any host running the Fragua flux-edge-tunnel:
timeout 10 bash -c '</dev/tcp/100.65.0.1/8060 && echo CONNECTED'
# → CONNECTED
```

That confirms:
1. The flux-edge-tunnel pod intercepted the TCP connection to the Ziti synthetic IP `100.65.0.1` (allocated from `100.65.0.0/16`)
2. The tunnel established an apiSession with the controller via `cdn.embernet.ai:443`
3. It got a circuit assigned, dialed the `ignition-cloud` service, reached the binder (cp005), and cp005 dialed the K8s service → returned a TCP handshake all the way back

If it hangs without printing CONNECTED, check (in order):
- `kubectl -n flux-system logs <tunnel-pod> --tail=50` — look for `dial tcp` errors. If `connection refused` to `cdn.embernet.ai:443`, the router record is wrong again or traefik routing broke
- The probe identity (or whichever dialing identity you're using) has `hasEdgeRouterConnection: true` in the controller
- The binder pod (`flux-tunnel-embernet-cp005-flux-edge-tunnel-*`) shows "hosting service" for ignition-cloud in its logs

---

## How to add another service over this path (e.g. `embernet-dashboard`)

Run the inventory + creation Python from inside the provisioner pod (it has
`httpx` + admin creds in env, see `.agent/CREDENTIALS.md`):

```bash
POD=$(kubectl -n embernet-provisioner get pods -l app.kubernetes.io/name=embernet-provisioner -o jsonpath='{.items[0].metadata.name}')
cat > /tmp/add-svc.py <<'PYEOF'
import os, httpx, json
zc=os.environ["ZITI_CONTROLLER_URL"]
c=httpx.Client(verify=False, base_url=zc, timeout=15.0)
tok=c.post("/edge/management/v1/authenticate", params={"method":"password"},
           json={"username":os.environ["ZITI_ADMIN_USER"],"password":os.environ["ZITI_ADMIN_PASSWORD"]}).json()["data"]["token"]
h={"zt-session":tok, "content-type":"application/json"}

# 1) Intercept config (what the dialing tunnel intercepts)
ic = {
    "name": "embernet-dashboard-intercept.v1",
    "configTypeId": "intercept.v1",
    "data": {
        "protocols": ["tcp"],
        "addresses": ["embernet-dashboard.fireball-system.svc.cluster.local"],
        "portRanges": [{"low": 8080, "high": 8080}],
    },
}
r=c.post("/edge/management/v1/configs", headers=h, content=json.dumps(ic))
intercept_id = r.json()["data"]["id"]; print("intercept:", intercept_id)

# 2) Host config (what the binder pod does with the inbound circuit)
hc = {
    "name": "embernet-dashboard-host.v1",
    "configTypeId": "host.v1",
    "data": {
        "protocol": "tcp",
        "address":  "embernet-dashboard.fireball-system.svc.cluster.local",
        "port":     8080,
    },
}
r=c.post("/edge/management/v1/configs", headers=h, content=json.dumps(hc))
host_id = r.json()["data"]["id"]; print("host:", host_id)

# 3) Service tying them together
sv = {
    "name": "embernet-dashboard",
    "configs": [intercept_id, host_id],
    "encryptionRequired": True,
    "roleAttributes": [],
}
r=c.post("/edge/management/v1/services", headers=h, content=json.dumps(sv))
svc_id = r.json()["data"]["id"]; print("service:", svc_id)

# 4) Bind policy — cp side hosts it
bp = {
    "name": "embernet-dashboard-bind",
    "type": "Bind",
    "serviceRoles":  [f"@{svc_id}"],
    "identityRoles": ["#embernet-control-plane"],
    "semantic":      "AnyOf",
}
r=c.post("/edge/management/v1/service-policies", headers=h, content=json.dumps(bp))
print("bind policy:", r.json()["data"]["id"])

# 5) Dial policy — probes (and anything else) can dial
dp = {
    "name": "embernet-probes-dashboard-dial",
    "type": "Dial",
    "serviceRoles":  [f"@{svc_id}"],
    "identityRoles": ["#embernet-probes"],
    "semantic":      "AnyOf",
}
r=c.post("/edge/management/v1/service-policies", headers=h, content=json.dumps(dp))
print("dial policy:", r.json()["data"]["id"])

# 6) Service-edge-router-policy — any router can carry this service
sep = {
    "name": "embernet-dashboard-routers",
    "serviceRoles":    [f"@{svc_id}"],
    "edgeRouterRoles": ["#all"],
    "semantic":        "AnyOf",
}
r=c.post("/edge/management/v1/service-edge-router-policies", headers=h, content=json.dumps(sep))
print("serp:", r.json()["data"]["id"])
PYEOF
kubectl -n embernet-provisioner cp /tmp/add-svc.py $POD:/tmp/add-svc.py
kubectl -n embernet-provisioner exec $POD -- python3 /tmp/add-svc.py
```

After the cp005 tunnel poll-cycle picks up the new service (~30s), the
probe — or any identity tagged `#embernet-probes` — can dial
`embernet-dashboard.fireball-system.svc.cluster.local:8080` and reach the
real K8s service.

---

## Why this works even though port 443 is "taken" on embernet005

The router doesn't bind 443 itself. It binds the default `:3022` (no
privileged-port issue, no klipper-lb conflict). The PUBLIC-facing 443 is
served by traefik's `websecure` entrypoint. Traefik does SNI-passthrough
based on the `IngressRouteTCP` CRDs:

```yaml
# flux-system/IngressRouteTCP flux-router-cdn (created in this work)
spec:
  entryPoints: [websecure]
  routes:
  - match: HostSNI(`cdn.embernet.ai`)
    services:
    - name: flux-router-edge
      port: 443       # ← svc port; ClusterIP svc forwards to pod's :3022
  tls:
    passthrough: true
```

The `flux-router-edge` Service's `targetPort` was patched from `443` to
`3022` to bridge `svc.port=443 → router.pod.port=3022`. Without that patch,
traefik would have routed to a closed port.

The router's `config.yml` was hand-edited (with `ZITI_BOOTSTRAP=false` to
prevent bootstrap regen on subsequent restarts) to advertise
`tls:cdn.embernet.ai:443` even though it BINDS on `0.0.0.0:3022`. That
split — bind on a high port internally, advertise on 443 externally — is
what makes the whole thing fit without touching the LB/firewall.

---

## Notes on cert SANs

The router cert needs `cdn.embernet.ai` as a SAN, or the dialing SDK
rejects the TLS handshake (SNI mismatch). The OpenZiti router bootstrap
populates CSR SANs from `ZITI_ROUTER_ADVERTISED_ADDRESS`. We set that env
on the Deployment to `cdn.embernet.ai`. After wiping the on-disk cert
files in `/var/lib/flux-router/` and restarting, the bootstrap regenerated
the CSR with the right SAN list and the controller signed a fresh cert.

**`ZITI_AUTO_RENEW_CERTS=true` reissues with the SAME SANs the original
CSR carried.** Auto-renew does NOT pick up SAN changes from a modified
config or env. To rotate SANs cleanly you must wipe the hostPath identity
files and re-enroll (see `/var/lib/flux-router/` wipe pattern in this
runbook's commit history).

---

## Rollback plan

If anything breaks:

1. Delete the new router record `P.csD4A7T1` (`flux-router-v2`):
   ```
   ziti edge delete edge-router P.csD4A7T1
   ```
2. Recreate `relay-us-east-1` (`nX3YHSQM5A`) — already exists, just re-enroll a fresh JWT
3. Restore the original `flux-router-enrollment` Secret with the legacy JWT
4. `kubectl -n flux-system rollout restart deploy flux-router`
5. Drop the `flux-router-cdn` IngressRouteTCP

You'll be back to the broken-but-known state where in-cluster cp tunnels
fail with `dial tcp 127.0.0.1:3022: connect: connection refused` but the
controller record looks "online". Useful only if the new path is causing
worse problems.

---

*Originally drafted 2026-05-19; rewritten 2026-05-21 after the actual root
cause was found and resolved. Previous version assumed an upstream Ziti
admin task — that turned out to be wrong; the work is doable end-to-end from
this repo's tooling.*
