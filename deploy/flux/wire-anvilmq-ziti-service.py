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

INTERCEPT_ADDR = "100.65.0.2"
PORT = 1883
SERVICE_NAME = "anvilmq-mqtt"
INTERCEPT_CFG_NAME = f"{SERVICE_NAME}-intercept"
HOST_CFG_NAME = f"{SERVICE_NAME}-host"
BIND_POLICY_NAME = f"{SERVICE_NAME}-bind"
DIAL_POLICY_NAME = f"fragua-{SERVICE_NAME}-dial"
TERMINATOR_HOST = "anvilmq.fireball-system.svc.cluster.local"
BIND_IDENT_ROLES = ["#embernet-control-plane"]
DIAL_IDENT_REFS = ["@199XSfc7B1", "@XSuRSfc2B1"]  # Fragua-Embernode-0001/-0002 ids

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
    host_cfg = ensure_config(
        HOST_CFG_NAME,
        host_type,
        {
            "protocol": "tcp",
            "address": TERMINATOR_HOST,
            "port": PORT,
            "allowedProtocols": ["tcp"],
            "allowedAddresses": [TERMINATOR_HOST],
            "allowedPortRanges": [{"low": PORT, "high": PORT}],
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
