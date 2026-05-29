# EmberNet WireGuard mesh — convention drift between hub and edges

> **For:** EmberNet network engineer
> **Reported by:** Patrick (CTO)
> **Status:** Partial outage tonight (2026-05-21) — etcd lost quorum because embernet001 ↔ hub WG handshake stuck. Demo with Gartner tomorrow morning. Need this aligned + verified before then.

## TL;DR — what's wrong

The hub (`embernet003`) was configured with `ListenPort = 443` (correct — it's the only fixed listener for the mesh). **Several peers were ALSO configured with `ListenPort = 443`** — that was the old convention. The new convention is **peers leave ListenPort unset** (kernel picks ephemeral), only the hub binds a fixed port.

You can see the split in `tcpdump -ni eth0 udp port 443` on the hub right now:

```
In  IP <peer>.443    > 10.0.0.6.443   ← old-convention peers (stuck — no responses back)
In  IP <peer>.54939  > 10.0.0.6.443   ← new-convention peers (kernel-ephemeral, handshakes complete)
Out IP 10.0.0.6.443  > <peer>.56417   ← hub responds to ephemeral source-port peers
```

**Old-convention peers I confirmed tonight that need updating:**

| Host | wg public IP | Current ListenPort | What to set |
|---|---|---|---|
| `fragua-edge-01` | `20.80.241.221` | `443` | **remove `ListenPort` line** |
| `fragua-edge-02` | `52.176.39.25` | `443` | **remove `ListenPort` line** |
| `embernet001` | (Azure egress, currently NAT IP `74.249.82.35`) | (was `443`, I removed via `systemctl restart wg-quick@wg0` tonight — it's on ephemeral 54939 now but conf may still have `ListenPort = 443`) | **remove `ListenPort` line in `/etc/wireguard/wg0.conf` so it survives reboot** |
| Any other Azure-side peer with `ListenPort = 443` | — | `443` | same fix |

The AWS-side peers (embernet004, embernet005, embernet006, anything in 3.x.x.x or 74.x range) appear to ALREADY be on the new convention — handshakes from them complete and hub responds.

## Why the old convention broke

When both endpoints of a WG flow listen on the same port (`:443 → :443`):

1. Peer A sends handshake init from `A:443` to `hub:443`.
2. Hub receives, processes, computes session keys, sends handshake response from `hub:443` to `A:443`.
3. The response packet leaves hub with `src=10.0.0.6:443, dst=A_public:443`. **Some intermediate (Azure outbound NAT pool, peering router, or stateful firewall) drops this** — most likely Azure SNAT treats UDP/443→UDP/443 as "no matching outbound flow" because A's original flow registered as `A:443 → hub:443` and the return is keyed by source-port mapping that's now ambiguous.
4. Peer A keeps retrying handshake init. Hub keeps responding. No state ever advances. `wg show` on peer A shows `0 B received`, hub shows `received some KiB` but no fresh handshake.

The fix (ephemeral source port on peers) means Azure's NAT pool sees the flow as a normal client→server pattern (random high port → 443), which is what stateful NAT is designed for. Responses route back cleanly.

## Recovery procedure (run on each old-convention peer)

```bash
# 1) Verify current state — handshake age should be > 3 min if stuck
sudo wg show wg0 | grep -E "latest handshake|transfer"

# 2) Back up the config
sudo cp /etc/wireguard/wg0.conf /etc/wireguard/wg0.conf.bak-$(date +%s)

# 3) Remove the ListenPort line from [Interface]
sudo sed -i '/^ListenPort *= *443/d' /etc/wireguard/wg0.conf

# 4) Confirm the file no longer has ListenPort
sudo grep -E "^ListenPort|^\[" /etc/wireguard/wg0.conf

# 5) Restart wg-quick — pod K3s flannel etc. will blip for ~3s
sudo systemctl restart wg-quick@wg0

# 6) Trigger a handshake — ping anything inside the mesh
ping -c 3 100.64.0.1

# 7) Verify
sudo wg show wg0 | grep -E "latest handshake|transfer"
# Expected: handshake age < 30s, transfer "received" counter > 0
```

## How to verify cluster recovery after applying to embernet001

```bash
# K3s API server should respond instead of "ServiceUnavailable"
sudo k3s kubectl get nodes
sudo k3s kubectl get pod -A | grep -v Running
```

`https://clusters.embernet.ai/dashboard/` should stop returning Bad Gateway once the Rancher pod on embernet005 is reachable through the (now healthy) etcd quorum.

## What changed in the hub tonight that you should be aware of

I touched embernet003 trying to debug the Fragua-edge-02 stuck handshake (same root cause):

1. `wg-quick down wg0 && wg-quick up wg0` — restarted the hub WG. The interface is up; `wg show` confirms peer entries are intact.
2. `wg syncconf wg0 <(wg-quick strip wg0)` — re-synced running state from `/etc/wireguard/wg0.conf`. This DELETED a stale peer entry that was in the running state but not in the conf file — pubkey `pKhk7516iKXZeaUEdUHrNpSeUch54hbYAtD0hxf8b1o=` had been mapped to `100.64.0.31` in the running state, with no corresponding wg0.conf entry. If that key belongs to anything you care about, it needs to be re-added to `wg0.conf` — but my reading is the file is the source of truth so it shouldn't.
3. Added then removed an `iptables-nft` NAT `REDIRECT udp dpt 443 → :51820` rule. I removed it — final state: not present.
4. Removed then re-added the `iptables-legacy` NAT REDIRECT rule of the same shape. Final state: present (legacy).

The legacy iptables NAT REDIRECT rule is a no-op since the kernel uses iptables-nft as the active backend AND the hub WG is bound directly to `0.0.0.0:443` (no NAT needed). It's been a confused configuration the whole time. Worth cleaning up after the demo, not before.

## What I did NOT touch

- `embernet001`'s wg0.conf file (only the running state via `systemctl restart`)
- Any Fragua edge wg0.conf files
- Any AWS-side node configs
- Rancher, k3s, etcd directly — they failed downstream of the WG issue

---

*— Generated 2026-05-21 22:0X UTC, in the middle of the outage. Patrick is hot under the collar and the Gartner demo is in <12h, so be quick.*
