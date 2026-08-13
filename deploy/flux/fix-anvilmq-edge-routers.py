#!/usr/bin/env python3
"""Let the anvilmq-mqtt service use the tenant edge routers Fragua attaches to.

Idempotent. Run inside the embernet-provisioner pod, where ZITI_CONTROLLER_URL,
ZITI_ADMIN_USER and ZITI_ADMIN_PASSWORD are already set:

    POD=$(kubectl -n embernet-provisioner get pods \
      -l app.kubernetes.io/name=embernet-provisioner -o jsonpath='{.items[0].metadata.name}')
    kubectl -n embernet-provisioner cp fix-anvilmq-edge-routers.py $POD:/tmp/fix.py
    kubectl -n embernet-provisioner exec $POD -- python3 /tmp/fix.py

## What this fixes

`anvilmq-mqtt-routers` granted the service `#public` edge routers only. The
Fragua edges attach through their own `tenant:fragua` routers
(fragua-edge-01-router, fragua-edge-02-router), so the dialing identity and the
service shared no edge router and every circuit died with:

    failed to dial fabric  error="invalid edge router for session"  service=anvilmq-mqtt

## Why it costs an hour to diagnose

At the client this is a TCP connection that establishes and is then reset with
no MQTT CONNACK — identical in appearance to the broker rejecting the
credential. Everything you would check first looks healthy: the service exists,
the dial policy matches the identity's role, the broker user exists and is
enabled, `</dev/tcp/100.65.0.2/1883` succeeds (the local intercept accepts
before any circuit is attempted). The only place the truth appears is the
endpoint's own log on the edge:

    podman logs --tail=50 embernet | grep -i anvilmq

The two Fragua services that already worked (`fragua-k3s-api-routers`,
`fragua-designer-routers`) both carry `['#public', '#tenant:fragua']`. This
makes `anvilmq-mqtt-routers` match that pattern, which is also what any future
service dialed from a tenant edge will need.
"""
import os
import sys

import httpx

ZITI = os.environ["ZITI_CONTROLLER_URL"].rstrip("/")
POLICY = "anvilmq-mqtt-routers"
WANT = ["#public", "#tenant:fragua"]

c = httpx.Client(verify=False, timeout=20)
tok = c.post(f"{ZITI}/edge/management/v1/authenticate?method=password",
             json={"username": os.environ["ZITI_ADMIN_USER"],
                   "password": os.environ["ZITI_ADMIN_PASSWORD"]}).json()["data"]["token"]
H = {"zt-session": tok}

# Ziti's list endpoints default to 10 results. Without an explicit limit a
# lookup silently reports that an object does not exist when it is simply on
# the second page — which sends you off fixing the wrong thing.
serps = c.get(f"{ZITI}/edge/management/v1/service-edge-router-policies",
              headers=H, params={"limit": 500}).json()["data"]

target = next((p for p in serps if p["name"] == POLICY), None)
if target is None:
    sys.exit(f"{POLICY} not found — has the anvilmq-mqtt service been created?")

print(f"before: serviceRoles={target['serviceRoles']} edgeRouterRoles={target['edgeRouterRoles']}")

if set(target["edgeRouterRoles"]) >= set(WANT):
    print("already correct, nothing to do")
else:
    c.patch(f"{ZITI}/edge/management/v1/service-edge-router-policies/{target['id']}",
            headers=H, json={"edgeRouterRoles": WANT}).raise_for_status()
    after = c.get(f"{ZITI}/edge/management/v1/service-edge-router-policies/{target['id']}",
                  headers=H).json()["data"]
    print(f"after:  serviceRoles={after['serviceRoles']} edgeRouterRoles={after['edgeRouterRoles']}")
