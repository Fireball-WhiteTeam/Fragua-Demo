"""Move Fragua's synthetic service addresses into Fragua's own /24.

WHY. Synthetic service addresses used to come out of one shared 100.64.65.0/24
regardless of tenant. The plan now gives every client cluster its own block,
mirroring the node layout so the tenant is readable straight off the address
(embernet-iac templates/app/flux-address-reservations.tsv):

    tenant N    nodes 100.64.N.0/24      services 100.64.(65+N).0/24

    fireball 0  nodes 100.64.0.0/24      services 100.64.65.0/24
    trane-ut3 1 nodes 100.64.1.0/24      services 100.64.66.0/24
    fragua   2  nodes 100.64.2.0/24      services 100.64.67.0/24

Fragua's three addresses were issued before that plan and sit in fireball's
block. This moves them.

ADDITIVE FIRST, AND THAT IS NOT OPTIONAL. `intercept.v1.addresses` is a LIST, so
the new address goes in ALONGSIDE the old one. Both work; nothing is cut over by
this script. Removing the old address is a SEPARATE, LATER step, and for
fragua-k3s-api it must not happen until:

  1. 100.64.67.2 is a SAN on the Fragua apiserver serving cert
     (embernet-iac server_addons.fragua.tls_san), AND the cert has actually been
     regenerated — adding the name does not rotate it. k3s restores the serving
     cert from the k3s-serving Secret and dynamic-cert.json, so both of those and
     the serving-kube-apiserver files must be removed before a restart picks up
     a new SAN.
  2. every Fragua agent's K3S_URL has moved.

Take the old address away before those and every k3s agent loses the API path,
silently: a Ziti dial SUCCEEDS against a service nothing is hosting, so nothing
reports an error anywhere.

TERMINATORS. Editing a HOST config drops terminators to 0 and the hosting
tunnelers do not re-bind on their own (established repeatedly 2026-08-14). This
script touches only INTERCEPT configs, which is the dial side — it reports the
terminator count before and after so the claim is measured rather than assumed.
If they do drop, restart the binding tunneler and re-check.

Usage (inside a pod with ZITI_* in env — the-reconciler or embernet-provisioner):
    python3 migrate-fragua-to-tenant-block.py            # plan only, writes nothing
    python3 migrate-fragua-to-tenant-block.py --apply
"""
import os
import sys
import json

import httpx

ZITI_URL = os.environ["ZITI_CONTROLLER_URL"].rstrip("/")
ADMIN_U = os.environ["ZITI_ADMIN_USER"]
ADMIN_P = os.environ["ZITI_ADMIN_PASSWORD"]
MGMT = "/edge/management/v1"

# service name -> (old address to keep for now, new address to add)
MOVES = {
    "fragua-k3s-api":          ("100.64.65.2",  "100.64.67.2"),
    "fragua-edge-01-designer": ("100.64.65.10", "100.64.67.10"),
    "fragua-edge-02-designer": ("100.64.65.11", "100.64.67.11"),
}

APPLY = "--apply" in sys.argv[1:]
c = httpx.Client(verify=False, timeout=30)


def auth():
    r = c.post(f"{ZITI_URL}{MGMT}/authenticate?method=password",
               json={"username": ADMIN_U, "password": ADMIN_P})
    r.raise_for_status()
    c.headers.update({"zt-session": r.json()["data"]["token"]})


def find(coll, name):
    r = c.get(f"{ZITI_URL}{MGMT}/{coll}", params={"filter": f'name="{name}"'})
    r.raise_for_status()
    d = r.json().get("data") or []
    return d[0] if d else None


def terminator_count(service_id):
    """Best effort. Reported, never used as a gate — if the endpoint shape is
    not what we expect, say so rather than invent a number."""
    try:
        r = c.get(f"{ZITI_URL}{MGMT}/terminators", params={"limit": 500})
        r.raise_for_status()
        rows = r.json().get("data") or []
        n = 0
        for t in rows:
            svc = t.get("service") or {}
            sid = svc.get("id") if isinstance(svc, dict) else t.get("serviceId")
            if sid == service_id:
                n += 1
        return n
    except Exception as e:
        return "unknown (%s)" % type(e).__name__


def main():
    auth()
    print(f"controller: {ZITI_URL}")
    print(f"mode: {'APPLY' if APPLY else 'PLAN ONLY (no writes)'}\n")

    changed = failed = 0
    for svc_name, (old, new) in MOVES.items():
        svc = find("services", svc_name)
        if not svc:
            print(f"!! {svc_name}: no such service — skipping")
            failed += 1
            continue

        cfg_name = f"{svc_name}-intercept"
        cfg = find("configs", cfg_name)
        if not cfg:
            print(f"!! {cfg_name}: no such config — skipping")
            failed += 1
            continue

        data = cfg.get("data") or {}
        addrs = list(data.get("addresses") or [])
        before = terminator_count(svc["id"])

        print(f"== {svc_name}")
        print(f"   addresses now : {addrs}")
        print(f"   terminators   : {before}")

        if new in addrs:
            print(f"   already carries {new} — nothing to do\n")
            continue
        if old not in addrs:
            print(f"   !! expected {old} in the list and it is not there.")
            print(f"      Refusing to guess: inspect this one by hand.\n")
            failed += 1
            continue

        wanted = addrs + [new]
        print(f"   -> will become : {wanted}")
        print(f"      ({old} stays; removing it is a separate, later step)")

        if not APPLY:
            print()
            continue

        body = {"name": cfg["name"], "data": dict(data, addresses=wanted)}
        r = c.patch(f"{ZITI_URL}{MGMT}/configs/{cfg['id']}", json=body)
        if r.status_code >= 300:
            print(f"   !! PATCH failed HTTP {r.status_code}: {r.text[:200]}\n")
            failed += 1
            continue

        after_cfg = find("configs", cfg_name)
        after_addrs = (after_cfg.get("data") or {}).get("addresses")
        after_term = terminator_count(svc["id"])
        print(f"   PATCHED. addresses now: {after_addrs}")
        print(f"   terminators after    : {after_term}"
              f"{'   <-- DROPPED, restart the binding tunneler' if isinstance(before, int) and isinstance(after_term, int) and after_term < before else ''}")
        print()
        changed += 1

    print(f"\n{changed} changed, {failed} problem(s).")
    if not APPLY:
        print("PLAN ONLY — nothing was written. Re-run with --apply.")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except httpx.HTTPStatusError as e:
        print(f"!! HTTP {e.response.status_code}: {e.response.text[:300]}", file=sys.stderr)
        sys.exit(2)
