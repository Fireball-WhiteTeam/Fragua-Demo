"""
Idempotent: create the Ziti service `anvilmq-mqtt` (and the matching bind/dial
policies + configs) so Fragua edges can dial the EmberNet AnvilMQ broker at
synthetic IP 100.65.0.2:1883.

Mirrors the existing `ignition-cloud` service pattern (id 6H5U38Lo55M5aY9Oforg3,
synthetic IP 100.65.0.1:8060). Reuses the existing `#all` edge-router policies.

Run inside the embernet-provisioner pod — env vars ZITI_CONTROLLER_URL,
ZITI_ADMIN_USER, ZITI_ADMIN_PASSWORD are pre-populated there.
"""
import os
import sys
import json
import httpx

ZITI_URL = os.environ["ZITI_CONTROLLER_URL"].rstrip("/")
ADMIN_U = os.environ["ZITI_ADMIN_USER"]
ADMIN_P = os.environ["ZITI_ADMIN_PASSWORD"]

# MOVED 2026-08-14 AND MUST NOT MOVE BACK. This was 100.65.0.2, which is INSIDE
# the Flux router's own DNS intercept pool (100.65.0.0/16) — the range the router
# allocates from dynamically for name-based services. A static address in there
# is a collision by construction, and it fired: when fragua-k3s-api gained a
# `.flux.internal` name the router handed it exactly 100.65.0.2, and the two then
# coexisted only because they happened to use different ports (1883 vs 6443).
#
# 100.64.65.3 matches the convention now used across the estate —
# embernet-dashboard is 100.64.65.1, fragua-k3s-api is 100.64.65.2. Inside
# 100.64.0.0/10 but outside both the DNS pool and every node /24, so nothing can
# be allocated onto it and no real machine is shadowed by it.
#
# Consumers must agree: deploy/emberburn/values-fragua.yaml `broker:`.
INTERCEPT_ADDR = "100.64.65.3"
PORT = 1883
SERVICE_NAME = "anvilmq-mqtt"
INTERCEPT_CFG_NAME = f"{SERVICE_NAME}-intercept"
HOST_CFG_NAME = f"{SERVICE_NAME}-host"
BIND_POLICY_NAME = f"{SERVICE_NAME}-bind"
DIAL_POLICY_NAME = f"fragua-{SERVICE_NAME}-dial"
TERMINATOR_HOST = "anvilmq.fireball-system.svc.cluster.local"

# WHAT THE HOST CONFIG ACTUALLY DIALS. This is the ClusterIP, NOT
# TERMINATOR_HOST, and that is an empirical result rather than a preference.
#
# Tested on 2026-08-14 with terminators confirmed present in both arms, so
# neither result is confounded by a missing bind:
#
#   address = anvilmq.fireball-system.svc.cluster.local  -> connection reset
#   address = 10.43.74.142                               -> MQTT CONNACK 0x00
#
# ignition-cloud-host.v1 uses the DNS form and works, so this is specific to
# this service and not a general "names do not resolve" rule. Root cause not
# established; the working configuration is recorded here so it is not
# rediscovered the hard way.
#
# THE FRAGILITY IS REAL AND UNRESOLVED: a ClusterIP is reassignable. If the
# anvilmq Service is ever recreated this value goes stale and the broker dies
# silently. Verify with:
#     kubectl -n fireball-system get svc anvilmq -o jsonpath='{.spec.clusterIP}'
TERMINATOR_ADDR = "10.43.74.142"

# CHANGING EITHER CONFIG DROPS THE TERMINATOR, AND IT DOES NOT COME BACK BY
# ITSELF. Observed repeatedly on 2026-08-14: any PUT to anvilmq-mqtt-host takes
# terminators to 0, and the hosting tunnelers do not re-bind on their own — they
# have to be restarted:
#
#     kubectl -n flux-system rollout restart ds/flux-tunnel-embernet-cp005-flux-edge-tunnel
#
# This is almost certainly why the service sat at ZERO terminators with every
# policy, SERP and identity checking out perfectly: something edited the config
# once, and nothing ever restarted the tunnelers. AFTER RUNNING THIS SCRIPT,
# RESTART THEM AND CONFIRM A CONNACK, or you have published a broker that
# accepts TCP and resets every MQTT session:
#
#     exec 3<>/dev/tcp/100.64.65.3/1883
#     printf '\x10\x0c\x00\x04MQTT\x04\x02\x00\x3c\x00\x00' >&3; head -c 4 <&3 | xxd
#     # expect: 2002 0000  (CONNACK, return code 0 = accepted)
BIND_IDENT_ROLES = ["#embernet-control-plane"]
# Dial is granted by ROLE, never by identity id.
#
# This used to be DIAL_IDENT_REFS = ["@199XSfc7B1", "@XSuRSfc2B1"], the ids of
# two edges enrolled in May 2026. Three things were wrong with that, and all
# three had already bitten by 2026-08-08:
#
#   1. A third node gets nothing. embernode-fragua-0002 joined Fragua Ready,
#      reported ONLINE, and could not dial the broker, because its id was not
#      in this list and nothing said so.
#   2. ensure_service_policy() PATCHes an existing policy. The live
#      fragua-anvilmq-mqtt-dial had since been corrected to
#      identityRoles ['#fragua-edge-dial'], so re-running this script would
#      have silently REVERTED it and cut off every node not in the list.
#   3. Both ids are now dangling — no identity with either id exists on the
#      controller. Pinning to an id survives neither a re-enrollment nor a
#      rebuild, and the failure is silent because a policy that selects nobody
#      is indistinguishable from one nobody has tripped yet.
#
# `#fragua-edge-dial` is the role the provisioner assigns from
# TENANT_ROLE_ATTRIBUTES_JSON and the-reconciler keeps repaired, so membership
# is automatic for every current and future Fragua node.
DIAL_IDENT_REFS = ["#fragua-edge-dial"]

c = httpx.Client(verify=False, timeout=15)


def auth():
    r = c.post(
        f"{ZITI_URL}/edge/management/v1/authenticate?method=password",
        json={"username": ADMIN_U, "password": ADMIN_P},
    )
    r.raise_for_status()
    tok = r.json()["data"]["token"]
    c.headers.update({"zt-session": tok})


def find_by_name(coll, name):
    r = c.get(
        f"{ZITI_URL}/edge/management/v1/{coll}?filter=" + f'name="{name}"'
    )
    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data else None


def ensure_config(name, cfg_type_id, data):
    existing = find_by_name("configs", name)
    if existing:
        print(f"  exists: config {name} ({existing['id']})")
        return existing["id"]
    body = {"name": name, "configTypeId": cfg_type_id, "data": data}
    r = c.post(f"{ZITI_URL}/edge/management/v1/configs", json=body)
    r.raise_for_status()
    cid = r.json()["data"]["id"]
    print(f"  created: config {name} ({cid})")
    return cid


def get_config_type_id(name):
    r = c.get(f"{ZITI_URL}/edge/management/v1/config-types?filter=" + f'name="{name}"')
    r.raise_for_status()
    return r.json()["data"][0]["id"]


def ensure_service(name, config_ids):
    existing = find_by_name("services", name)
    if existing:
        print(f"  exists: service {name} ({existing['id']})")
        return existing["id"]
    body = {
        "name": name,
        "encryptionRequired": True,
        "terminatorStrategy": "smartrouting",
        "configs": config_ids,
        "roleAttributes": [SERVICE_NAME],
    }
    r = c.post(f"{ZITI_URL}/edge/management/v1/services", json=body)
    r.raise_for_status()
    sid = r.json()["data"]["id"]
    print(f"  created: service {name} ({sid})")
    return sid


def ensure_service_policy(name, policy_type, identity_roles, service_roles):
    existing = find_by_name("service-policies", name)
    body = {
        "name": name,
        "type": policy_type,
        "identityRoles": identity_roles,
        "serviceRoles": service_roles,
        "semantic": "AnyOf",
        "postureCheckRoles": [],
    }
    if existing:
        r = c.patch(
            f"{ZITI_URL}/edge/management/v1/service-policies/{existing['id']}",
            json=body,
        )
        r.raise_for_status()
        print(f"  patched: service-policy {name} ({existing['id']})")
        return existing["id"]
    r = c.post(f"{ZITI_URL}/edge/management/v1/service-policies", json=body)
    r.raise_for_status()
    pid = r.json()["data"]["id"]
    print(f"  created: service-policy {name} ({pid})")
    return pid


def main():
    print(f"== Authenticating to {ZITI_URL} ==")
    auth()

    intercept_type = get_config_type_id("intercept.v1")
    host_type = get_config_type_id("host.v1")
    print(f"  config types: intercept.v1={intercept_type}  host.v1={host_type}")

    print("\n== Configs ==")
    intercept_cfg = ensure_config(
        INTERCEPT_CFG_NAME,
        intercept_type,
        {
            "protocols": ["tcp"],
            "addresses": [INTERCEPT_ADDR],
            "portRanges": [{"low": PORT, "high": PORT}],
        },
    )
    # THE THREE FIELDS ONLY. Do not re-add allowedProtocols /
    # allowedAddresses / allowedPortRanges here.
    #
    # Those describe what a CLIENT may ask for when the host is in forwarding
    # mode — they are the companions of forwardAddress / forwardPort /
    # forwardProtocol. None of those are set, so including them made this config
    # say two contradictory things at once: "always dial TERMINATOR_HOST:1883"
    # and "let the caller pick from this allow-list".
    #
    # anvilmq-mqtt sat with ZERO terminators while ignition-cloud — same bind
    # role, same routers, same fabric — had eight. The hosting tunnelers logged
    # nothing about it at all, which is what a service they never accept as
    # hostable looks like. Fixed live on 2026-08-14 by reducing this to the
    # exact shape ignition-cloud-host.v1 uses, plus a restart of the cp tunnel
    # DaemonSets so they re-enumerated; terminators went 0 -> 2 and an MQTT
    # CONNACK (0x20 0x02 0x00 0x00, accepted) came back over the circuit.
    #
    # Whether the malformed config or the stale enumeration was load-bearing was
    # never isolated — a restart against the OLD config was not tested. So this
    # stays clean regardless: re-running the script must not be able to put the
    # broker back in a state we spent a night diagnosing.
    host_cfg = ensure_config(
        HOST_CFG_NAME,
        host_type,
        {
            "protocol": "tcp",
            "address": TERMINATOR_ADDR,
            "port": PORT,
        },
    )

    print("\n== Service ==")
    ensure_service(SERVICE_NAME, [intercept_cfg, host_cfg])

    print("\n== Service Policies ==")
    ensure_service_policy(
        BIND_POLICY_NAME,
        "Bind",
        BIND_IDENT_ROLES,
        [f"#{SERVICE_NAME}"],
    )
    ensure_service_policy(
        DIAL_POLICY_NAME,
        "Dial",
        DIAL_IDENT_REFS,
        [f"#{SERVICE_NAME}"],
    )

    print("\n== Final state for the new service ==")
    svc = find_by_name("services", SERVICE_NAME)
    print(json.dumps({"id": svc["id"], "name": svc["name"], "configs": svc.get("configs")}, indent=2))
    print(
        f"\nDial from Fragua edges:  tcp://{INTERCEPT_ADDR}:{PORT}   "
        f"(forwards to {TERMINATOR_HOST}:{PORT})"
    )


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as e:
        print(f"!! HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        sys.exit(2)
