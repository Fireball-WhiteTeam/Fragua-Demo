"""Idempotent source of truth for the two *-k3s-api Ziti services.

WHY THIS FILE EXISTS. Until 2026-08-14 `fragua-k3s-api` and `trane-ut3-k3s-api`
existed ONLY in the controller database. No file in any repo created them, and
the reconciler refuses to touch `*-k3s-api` on purpose
(the_reconciler/config.py:49) — so a controller rebuild would have lost the k3s
control path for both tenants with nothing to restore from. Everything below was
read back off the live controller, not reconstructed from memory.

These carry the k3s API. Read the whole file before running it.

WHAT EACH SERVICE IS
    fragua-k3s-api      intercept fragua-k3s-api.flux.internal + 100.64.65.2
                        host      127.0.0.1:6443 on the Fragua control plane
    trane-ut3-k3s-api   intercept trane-ut3-k3s-api.flux.internal
                        host      127.0.0.1:6443 on trane-ut3-cp-02

Two addresses on Fragua is deliberate. The name is for anything that resolves;
the static 100.64.65.2 is what K3S_URL uses, because it needs no working
resolver at k3s start and, being outside the router's 100.65.0.0/16 DNS pool,
cannot be allocated over. Both are SANs on the apiserver serving cert, declared
in embernet-iac as server_addons.fragua.tls_san.

ADDRESSING RULES, each learned the hard way
  * NEVER put an intercept inside 100.65.0.0/16. That is the router's DNS pool
    and it allocates from it: fragua-k3s-api was pinned at 100.65.1.1, and once
    it gained a .flux.internal name the router handed it 100.65.0.2 — the
    address anvilmq-mqtt was already pinned to. They coexisted only because the
    ports differed.
  * NEVER intercept a real node address. That installs the machine's own IP on
    `lo` on every dialer and severs the direct path to it.
  * host.v1 is THREE FIELDS: protocol, address, port. allowedAddresses /
    allowedPortRanges / allowedProtocols belong with forwardAddress/forwardPort/
    forwardProtocol; adding them without those makes the config say both "always
    dial this" and "let the caller choose".

BIND BY ROLE ATTRIBUTE, NEVER BY IDENTITY ID. Both live policies pinned two ids
apiece and in BOTH cases one was already dead — @8wIhMwE9I (fragua-edge-01) and
@uF2sAGUQuC (Embernode-UT3-CP02) are detached, so the apparent redundancy was
one live binder and one corpse. The same pattern left both Designers with ZERO
terminators after the 2.x identity change. identityRoles will not accept a name
(400, "no identities found with the given ids"), so an attribute is the only
durable reference.

The attribute is per-service, NOT the existing #hosts-<tenant>. #hosts-fragua is
held by six identities including embernode-fragua-0002, and the host config is
127.0.0.1:6443 — "the apiserver on MY host". Let a non-control-plane node bind
it and dials terminate on a k3s AGENT's port 6443, which is not the apiserver.
Broad roles are wrong here for the same reason they were wrong for the
Designers.

AFTER ANY CHANGE, RESTART THE BINDING TUNNELER. Editing a config or a bind
policy DROPS the terminator and the tunnelers do not re-bind on their own —
established repeatedly on 2026-08-14. Then verify with a real TLS request, not a
TCP connect:

    curl -sk -o /dev/null -w '%{http_code}' https://100.64.65.2:6443/readyz
    # 401 = healthy. The apiserver is answering and refusing anonymous access.
    # A bare TCP connect proves NOTHING: the kernel owns the intercept address
    # whether or not anything is hosting the service.

Run inside the embernet-provisioner pod, which has httpx and ZITI_* in env.
"""
import os
import sys
import json

import httpx

ZITI_URL = os.environ["ZITI_CONTROLLER_URL"].rstrip("/")
ADMIN_U = os.environ["ZITI_ADMIN_USER"]
ADMIN_P = os.environ["ZITI_ADMIN_PASSWORD"]

# The router's DNS intercept pool. Nothing static may live in here.
DNS_POOL_PREFIX = "100.65."

SERVICES = [
    {
        "name": "fragua-k3s-api",
        "intercept_addresses": ["fragua-k3s-api.flux.internal", "100.64.65.2"],
        "port": 6443,
        "terminator_host": "127.0.0.1",
        "role_attributes": ["fragua-k3s-api", "tenant:fragua"],
        # Stamped on the identity that runs on the CONTROL PLANE node only.
        "bind_identity_name": "fragua-edge-01-router",
        "bind_role": "hosts-fragua-k3s-api",
        "dial_roles": ["fragua-edge-dial"],
        "serp_name": "fragua-k3s-api-routers",
        "edge_router_roles": ["#public", "#tenant:fragua"],
    },
    {
        "name": "trane-ut3-k3s-api",
        # Was 100.64.1.3 — trane-ut3-cp-02's REAL overlay address. Intercepting
        # a live machine's own IP shadows it on every dialer in intercept mode;
        # ut3 survived only because its nodes run proxy mode, which consumes no
        # intercept address at all. One env var from an outage.
        "intercept_addresses": ["trane-ut3-k3s-api.flux.internal"],
        "port": 6443,
        "terminator_host": "127.0.0.1",
        "role_attributes": ["tenant:tranetech-ut3"],
        "bind_identity_name": "Trane-UT3-CP-02-router",
        "bind_role": "hosts-trane-ut3-k3s-api",
        "dial_roles": ["trane-ut3"],
        "serp_name": "trane-ut3-k3s-api-routers",
        "edge_router_roles": ["#public", "#tenant:tranetech-ut3"],
    },
]

APPLY = "--apply" in sys.argv

# --only <service> restricts the run to one service.
#
# Converting a bind policy DROPS that service's terminator until the binder is
# restarted, and these two are the k3s control path for two different tenants on
# two different clusters. Doing both in one run means both control planes are
# degraded simultaneously and a failure in the second leaves you diagnosing
# under load. One tenant, verify, then the next.
ONLY = None
if "--only" in sys.argv:
    i = sys.argv.index("--only")
    if i + 1 >= len(sys.argv):
        raise SystemExit("!! --only needs a service name")
    ONLY = sys.argv[i + 1]

c = httpx.Client(verify=False, timeout=30)


def auth():
    r = c.post(f"{ZITI_URL}/edge/management/v1/authenticate?method=password",
               json={"username": ADMIN_U, "password": ADMIN_P})
    r.raise_for_status()
    c.headers.update({"zt-session": r.json()["data"]["token"]})


def find_by_name(coll, name):
    r = c.get(f"{ZITI_URL}/edge/management/v1/{coll}",
              params={"filter": f'name="{name}"', "limit": 10})
    r.raise_for_status()
    data = r.json()["data"]
    return data[0] if data else None


def config_type_id(name):
    ct = find_by_name("config-types", name)
    if not ct:
        raise SystemExit(f"!! config-type {name!r} not found")
    return ct["id"]


def assert_outside_pool(name, addresses):
    """Refuse to write an intercept into the router's own DNS pool.

    The provisioner enforces this too (the_reconciler/ziti.py), but these
    services are hand-made and never pass through it, which is precisely how
    fragua-k3s-api ended up at 100.65.1.1 in the first place.
    """
    for a in addresses:
        if a.replace(".", "").isdigit() or a[:1].isdigit():
            if a.startswith(DNS_POOL_PREFIX):
                raise SystemExit(
                    f"!! REFUSING: {name} would pin {a}, inside the router's DNS "
                    f"pool {DNS_POOL_PREFIX}0.0/16. The router allocates from "
                    f"there; this collides by construction.")


def ensure_config(name, type_id, data):
    existing = find_by_name("configs", name)
    if existing:
        if existing.get("data") == data:
            print(f"  ok      config {name}")
            return existing["id"]
        print(f"  UPDATE  config {name}")
        print(f"            from {json.dumps(existing.get('data'), sort_keys=True)}")
        print(f"            to   {json.dumps(data, sort_keys=True)}")
        if APPLY:
            r = c.put(f"{ZITI_URL}/edge/management/v1/configs/{existing['id']}",
                      json={"name": name, "data": data})
            r.raise_for_status()
        return existing["id"]
    print(f"  CREATE  config {name} = {json.dumps(data, sort_keys=True)}")
    if not APPLY:
        return None
    r = c.post(f"{ZITI_URL}/edge/management/v1/configs",
               json={"name": name, "configTypeId": type_id, "data": data})
    r.raise_for_status()
    return r.json()["data"]["id"]


def ensure_identity_role(identity_name, role):
    """Additively stamp `role` on `identity_name`.

    Additive because these identities carry other grants (#hosts-<tenant>,
    #<tenant>) that other policies depend on; replacing the list would silently
    revoke them.
    """
    ident = find_by_name("identities", identity_name)
    if not ident:
        raise SystemExit(
            f"!! identity {identity_name!r} not found. Bind cannot be wired and "
            f"the service would sit with no terminator while looking healthy.")
    attached = bool(ident.get("hasApiSession") or ident.get("hasEdgeRouterConnection"))
    if not attached:
        print(f"  WARN    {identity_name} is NOT attached; it cannot bind until "
              f"it connects")
    attrs = list(ident.get("roleAttributes") or [])
    if role in attrs:
        print(f"  ok      identity {identity_name} has #{role}")
        return
    print(f"  UPDATE  identity {identity_name} += #{role}")
    if APPLY:
        r = c.patch(f"{ZITI_URL}/edge/management/v1/identities/{ident['id']}",
                    json={"roleAttributes": attrs + [role]})
        r.raise_for_status()


def ensure_service(name, config_ids, role_attributes):
    existing = find_by_name("services", name)
    body = {
        "name": name,
        "configs": [x for x in config_ids if x],
        "roleAttributes": role_attributes,
        "encryptionRequired": True,
    }
    if existing:
        print(f"  ok      service {name} ({existing['id']})")
        return existing["id"]
    print(f"  CREATE  service {name}")
    if not APPLY:
        return None
    r = c.post(f"{ZITI_URL}/edge/management/v1/services", json=body)
    r.raise_for_status()
    return r.json()["data"]["id"]


def ensure_policy(name, ptype, identity_roles, service_roles):
    existing = find_by_name("service-policies", name)
    body = {
        "name": name,
        "type": ptype,
        "identityRoles": identity_roles,
        "serviceRoles": service_roles,
        "semantic": "AnyOf",
        "postureCheckRoles": [],
    }
    if existing:
        if existing.get("identityRoles") == identity_roles:
            print(f"  ok      policy {name}")
            return
        print(f"  UPDATE  policy {name}")
        print(f"            from {existing.get('identityRoles')}")
        print(f"            to   {identity_roles}")
        if APPLY:
            r = c.patch(
                f"{ZITI_URL}/edge/management/v1/service-policies/{existing['id']}",
                json={"identityRoles": identity_roles})
            r.raise_for_status()
        return
    print(f"  CREATE  policy {name} {ptype} {identity_roles} -> {service_roles}")
    if APPLY:
        r = c.post(f"{ZITI_URL}/edge/management/v1/service-policies", json=body)
        r.raise_for_status()


def ensure_serp(name, service_roles, edge_router_roles):
    existing = find_by_name("service-edge-router-policies", name)
    if existing:
        print(f"  ok      serp {name}")
        return
    print(f"  CREATE  serp {name} {edge_router_roles} -> {service_roles}")
    if APPLY:
        r = c.post(f"{ZITI_URL}/edge/management/v1/service-edge-router-policies",
                   json={"name": name, "serviceRoles": service_roles,
                         "edgeRouterRoles": edge_router_roles, "semantic": "AnyOf"})
        r.raise_for_status()


def main():
    auth()
    intercept_type = config_type_id("intercept.v1")
    host_type = config_type_id("host.v1")

    selected = [s for s in SERVICES if ONLY is None or s["name"] == ONLY]
    if ONLY and not selected:
        raise SystemExit(
            f"!! --only {ONLY!r} matches nothing. Known: "
            + ", ".join(s["name"] for s in SERVICES))
    if ONLY:
        print(f"(restricted to {ONLY})")

    for svc in selected:
        name = svc["name"]
        print(f"\n== {name} ==")
        assert_outside_pool(name, svc["intercept_addresses"])

        icfg = ensure_config(
            f"{name}-intercept", intercept_type,
            {"protocols": ["tcp"],
             "addresses": svc["intercept_addresses"],
             "portRanges": [{"low": svc["port"], "high": svc["port"]}]})
        hcfg = ensure_config(
            f"{name}-host", host_type,
            {"protocol": "tcp",
             "address": svc["terminator_host"],
             "port": svc["port"]})

        sid = ensure_service(name, [icfg, hcfg], svc["role_attributes"])
        svc_ref = [f"@{sid}"] if sid else [f"#{name}"]

        ensure_identity_role(svc["bind_identity_name"], svc["bind_role"])
        ensure_policy(f"{name}-bind", "Bind",
                      [f"#{svc['bind_role']}"], svc_ref)
        ensure_policy(f"{name}-dial", "Dial",
                      [f"#{r}" for r in svc["dial_roles"]], svc_ref)
        ensure_serp(svc["serp_name"], svc_ref, svc["edge_router_roles"])

    if not APPLY:
        print("\nDRY RUN — pass --apply to write.")
        return

    print("\nDone. A bind-policy or config change DROPS the terminator and the")
    print("tunnelers do not re-bind on their own. Restart the binder, then")
    print("verify with a real TLS request (401 = healthy), never a TCP connect:")
    print("  curl -sk -o /dev/null -w '%{http_code}' https://100.64.65.2:6443/readyz")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as e:
        print(f"!! HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        sys.exit(2)
