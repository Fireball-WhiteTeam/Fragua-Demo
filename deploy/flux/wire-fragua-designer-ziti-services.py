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

EDGES = [
    {
        "name": "fragua-edge-01-designer",
        "intercept": "100.65.0.10",
        "port": 8088,
        "terminator_host": "localhost",
        "bind_ident_ref": "@199XSfc7B1",  # Fragua-Embernode-0001
    },
    {
        "name": "fragua-edge-02-designer",
        "intercept": "100.65.0.11",
        "port": 8088,
        "terminator_host": "localhost",
        "bind_ident_ref": "@XSuRSfc2B1",  # Fragua-Embernode-0002
    },
]

ENGINEER_ROLE = "fragua-engineers"
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
        host_cfg = ensure_config(
            f"{name}-host",
            host_type,
            {
                "protocol": "tcp",
                "address": edge["terminator_host"],
                "port": edge["port"],
                "allowedProtocols": ["tcp"],
                "allowedAddresses": [edge["terminator_host"]],
                "allowedPortRanges": [{"low": edge["port"], "high": edge["port"]}],
            },
        )
        ensure_service(name, [intercept_cfg, host_cfg])
        ensure_service_policy(
            f"{name}-bind",
            "Bind",
            [edge["bind_ident_ref"]],
            [f"#{name}"],
        )
        service_role_refs.append(f"#{name}")

    print(f"\n== Shared dial policy: {SHARED_DIAL_POLICY} ==")
    ensure_service_policy(
        SHARED_DIAL_POLICY,
        "Dial",
        [f"#{ENGINEER_ROLE}"],
        service_role_refs,
    )

    print("\n== Done. Synthetic addresses for engineer to use ==")
    for edge in EDGES:
        print(f"  {edge['name']}:  tcp://{edge['intercept']}:{edge['port']}")
    print(
        f"\nTag any enrolled identity with role attribute "
        f"'{ENGINEER_ROLE}' to grant Designer access to both edges."
    )


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as e:
        print(f"!! HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        sys.exit(2)
