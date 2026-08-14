"""
Idempotent: create per-edge Ziti services so an EmberNET Endpoint user
with the `fragua-engineers` role attribute can dial each Fragua edge's
Ignition Gateway at port 8088 (Designer config session + Perspective).

Services land at:
  fragua-edge-01-designer  intercept 100.65.0.10:8088 -> localhost:8088 on edge-01
  fragua-edge-02-designer  intercept 100.65.0.11:8088 -> localhost:8088 on edge-02

Bind side is the corresponding Fragua node's own Ziti identity:
  Fragua-Embernode-0001 (id 199XSfc7B1) binds edge-01-designer
  Fragua-Embernode-0002 (id XSuRSfc2B1) binds edge-02-designer

A single dial policy grants `#fragua-engineers` (role attribute) access
to both. Tag any engineer's enrolled identity with that attribute and
they get Designer access to both edges. To revoke, untag.

Run inside the embernet-provisioner pod -- ZITI_CONTROLLER_URL,
ZITI_ADMIN_USER, ZITI_ADMIN_PASSWORD are pre-populated there.
"""
import os
import sys
import json
import httpx

ZITI_URL = os.environ["ZITI_CONTROLLER_URL"].rstrip("/")
ADMIN_U = os.environ["ZITI_ADMIN_USER"]
ADMIN_P = os.environ["ZITI_ADMIN_PASSWORD"]

# Bind stays 1:1 per edge — an edge must host ONLY its own Designer, so unlike
# the dial side this cannot become a shared role. But the identity is named,
# not pinned by id, and resolved against the controller at runtime.
#
# It used to carry "bind_ident_ref": "@199XSfc7B1" / "@XSuRSfc2B1". Checked
# against the live controller 2026-08-08, NEITHER ID EXISTS ANY MORE, while the
# live policies correctly bind @8wIhMwE9I (fragua-edge-01) and @9cO634E9T
# (fragua-edge-02). Since ensure_service_policy() PATCHes an existing policy,
# re-running this script would have repointed both designer binds at
# nonexistent identities — no node could host its Designer, and nothing would
# have said so. A pinned id does not survive a re-enrollment; a name does.
# WHAT ACTUALLY HAPPENED, 2026-08-14. The reasoning above was right and the
# implementation still pinned ids, so both designers sat at ZERO TERMINATORS —
# nothing had hosted either one since the 2.x identity change. @8wIhMwE9I and
# @9cO634E9T are fragua-edge-01/-02, which now report api=False edgeRouter=False;
# the identities actually attached on those hosts are fragua-edge-0N-router.
#
# identityRoles will NOT accept a name. Verified against the controller:
# "@fragua-edge-01-router" -> 400, "no identities found with the given ids". So
# "resolve the name at runtime" can only ever produce an id, and an id is the
# thing that just rotted.
#
# The durable form is a PER-NODE ROLE ATTRIBUTE on the identity that runs on
# that node. Precise like an id, survives re-enrollment like a name. It must be
# per-node and must NOT be the existing shared #hosts-fragua: each host config
# points at ITS OWN localhost:8088, so a shared role would let edge-02 host
# edge-01's Designer and serve the wrong gateway to an engineer who believes
# they are on edge-01 — silent, and worse than being down.
EDGES = [
    {
        "name": "fragua-edge-01-designer",
        # Moved off 100.65.0.10, which sat INSIDE the router's DNS intercept
        # pool 100.65.0.0/16 and could be handed to another service at any time
        # — the collision that put fragua-k3s-api.flux.internal on top of
        # anvilmq-mqtt. 100.64.65.x matches embernet-dashboard (.1),
        # fragua-k3s-api (.2) and anvilmq-mqtt (.3).
        "intercept": "100.64.65.10",
        "port": 8088,
        "terminator_host": "localhost",
        "bind_identity_name": "fragua-edge-01-router",
        "bind_role": "hosts-fragua-edge-01-designer",
    },
    {
        "name": "fragua-edge-02-designer",
        "intercept": "100.64.65.11",
        "port": 8088,
        "terminator_host": "localhost",
        "bind_identity_name": "fragua-edge-02-router",
        "bind_role": "hosts-fragua-edge-02-designer",
    },
]

# The host config must be the THREE-FIELD form: protocol, address, port. Do not
# add allowedAddresses / allowedPortRanges / allowedProtocols — those are the
# companions of forwardAddress/forwardPort/forwardProtocol, none of which are
# set here, so including them makes the config say both "always dial
# localhost:8088" and "let the caller choose". Both designer host configs
# carried them and were reduced on 2026-08-14.

# AFTER RUNNING THIS, RESTART THE TUNNELER ON EACH EDGE. Editing a host config
# drops the terminator and the tunnelers do not re-bind on their own:
#     systemctl restart embernet.service
# Then confirm terminators are non-zero. A TCP connect proves nothing — the
# kernel owns the intercept address whether or not anything is hosting.

# WHO MAY DIAL A DESIGNER (Patrick, 2026-08-14).
#
# Engineer, Admin and Super Admin may. AN OPERATOR MAY NOT — Designer access
# changes the plant, and that is not an Operator capability.
#
# Operator exclusion is enforced BY ABSENCE. Ziti policies only ever grant;
# there is no deny. So "Operator cannot" is expressed by never stamping an
# Operator identity with one of these attributes, and adding an operator
# attribute to the dial policy would be the bug, not the fix.
#
# The authorization gate is the enrolled EmberNet Endpoint for Windows: a person
# with one connected is authorized, and the attribute is granted BY HAND when
# their endpoint is enrolled. Deliberately NOT driven from AAD groups — this is
# a standing decision, not a gap waiting to be automated.
#
# Three attributes rather than one, because they are different grants that
# merely coincide today. Collapsing them to #fragua-designer-access would make
# it impossible to revoke Engineers without also revoking Admins, and would lose
# the record of why each identity has the access.
#
# AnyOf: holding any one suffices. A Super Admin does not also need #engineers.
DIAL_ROLES = [
    "fragua-engineers",
    "fragua-admins",
    "fragua-superadmins",
]

# Kept for backwards compatibility with anything importing it. The dial policy
# is built from DIAL_ROLES above; this alone is no longer the whole grant.
ENGINEER_ROLE = DIAL_ROLES[0]
SHARED_DIAL_POLICY = "fragua-designer-dial"

c = httpx.Client(verify=False, timeout=15)


def auth():
    r = c.post(
        f"{ZITI_URL}/edge/management/v1/authenticate?method=password",
        json={"username": ADMIN_U, "password": ADMIN_P},
    )
    r.raise_for_status()
    c.headers.update({"zt-session": r.json()["data"]["token"]})


def find_by_name(coll, name):
    r = c.get(f"{ZITI_URL}/edge/management/v1/{coll}?filter=" + f'name="{name}"')
    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data else None


def identity_ref(identity_name):
    """Resolve an identity NAME to the '@<id>' form a policy references.

    Fails loudly and stops. The alternative — carrying on with a name the
    controller does not know — writes a Bind policy that selects nobody, which
    looks identical to a working one until somebody tries to open Designer.
    Never guess-bind: a wrong identity here means one edge can host another's
    Designer.
    """
    ident = find_by_name("identities", identity_name)
    if not ident:
        sys.exit(
            f"FATAL: no Ziti identity named {identity_name!r}. Refusing to write a "
            f"Bind policy that would select nobody. Check the identity name (it is "
            f"the device name used at provisioning) and re-run."
        )
    return f"@{ident['id']}"


def get_config_type_id(name):
    r = c.get(f"{ZITI_URL}/edge/management/v1/config-types?filter=" + f'name="{name}"')
    r.raise_for_status()
    return r.json()["data"][0]["id"]


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
        "roleAttributes": [name],
    }
    r = c.post(f"{ZITI_URL}/edge/management/v1/services", json=body)
    r.raise_for_status()
    sid = r.json()["data"]["id"]
    print(f"  created: service {name} ({sid})")
    return sid


def ensure_identity_role(identity_name, role):
    """Ensure `identity_name` carries role attribute `role`, additively.

    This is what makes the bind policy stable. The policy references #role, and
    the role lives on whichever identity currently runs on that node — so a
    re-enrollment that changes the identity's id (which is exactly what left
    both designers with zero terminators) only requires re-stamping the
    attribute, not editing every policy that referenced the old id.

    ADDITIVE ON PURPOSE. These identities carry fragua-edge-dial and
    hosts-fragua, and those grant access elsewhere; replacing the list instead
    of appending would silently revoke it.
    """
    ident = find_by_name("identities", identity_name)
    if not ident:
        raise SystemExit(
            f"!! identity {identity_name!r} not found. Bind cannot be wired, and "
            f"the service would sit with no terminator while looking healthy."
        )
    attrs = list(ident.get("roleAttributes") or [])
    if role in attrs:
        print(f"  ok: identity {identity_name} already has #{role}")
        return
    r = c.patch(
        f"{ZITI_URL}/edge/management/v1/identities/{ident['id']}",
        json={"roleAttributes": attrs + [role]},
    )
    r.raise_for_status()
    print(f"  updated: identity {identity_name} += #{role}")


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

    service_role_refs = []

    for edge in EDGES:
        name = edge["name"]
        print(f"\n== {name} ==")
        intercept_cfg = ensure_config(
            f"{name}-intercept",
            intercept_type,
            {
                "protocols": ["tcp"],
                "addresses": [edge["intercept"]],
                "portRanges": [{"low": edge["port"], "high": edge["port"]}],
            },
        )
        # Three fields only — see the note above EDGES. The allowed* keys that
        # used to be here contradict a fixed address/port and were removed live
        # on 2026-08-14.
        host_cfg = ensure_config(
            f"{name}-host",
            host_type,
            {
                "protocol": "tcp",
                "address": edge["terminator_host"],
                "port": edge["port"],
            },
        )
        ensure_service(name, [intercept_cfg, host_cfg])

        # Bind by PER-NODE ROLE ATTRIBUTE, not by identity reference. An id is
        # what rotted when the hosts re-enrolled as *-router and left both
        # designers with zero terminators; identityRoles rejects names outright
        # (400, "no identities found with the given ids"), so resolving the name
        # at runtime just produces another id. The attribute is put ON the
        # identity here so the policy can be stable.
        ensure_identity_role(edge["bind_identity_name"], edge["bind_role"])
        ensure_service_policy(
            f"{name}-bind",
            "Bind",
            [f"#{edge['bind_role']}"],
            [f"#{name}"],
        )
        service_role_refs.append(f"#{name}")

    print(f"\n== Shared dial policy: {SHARED_DIAL_POLICY} ==")
    ensure_service_policy(
        SHARED_DIAL_POLICY,
        "Dial",
        [f"#{role}" for role in DIAL_ROLES],
        service_role_refs,
    )

    print("\n== Done. Synthetic addresses for engineer to use ==")
    for edge in EDGES:
        print(f"  {edge['name']}:  tcp://{edge['intercept']}:{edge['port']}")
    print(
        "\nGrant Designer access by tagging an enrolled identity with ONE of:\n"
        + "".join(f"  #{role}\n" for role in DIAL_ROLES)
        + "The enrolled EmberNet Endpoint for Windows is the authorization gate;\n"
        "grant by hand when that endpoint is enrolled. NEVER tag an Operator —\n"
        "exclusion is by absence, because Ziti policies only grant.\n"
        "\nVerify with a real HTTP request expecting 302 (Ignition redirects):\n"
        f"  curl -s -o /dev/null -w '%{{http_code}}' http://{EDGES[0]['intercept']}:{EDGES[0]['port']}/\n"
        "A TCP connect proves NOTHING — the kernel owns the intercept address\n"
        "whether or not anything is hosting the service."
    )


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as e:
        print(f"!! HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        sys.exit(2)
