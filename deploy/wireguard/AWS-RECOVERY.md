# AWS-side WG mesh recovery — embernet004 & embernet005

> **Why:** Restoring etcd quorum on the K3s cluster after the hub's vestigial
> iptables-legacy REDIRECT rule was identified and removed (2026-05-21).
> embernet001 (Azure) is already back online and handshaking with the hub.
> Need at least ONE of embernet004 / embernet005 to also re-handshake for
> raft quorum (2 of 3 etcd voters).
>
> **Demo:** Gartner, tomorrow morning. Do this tonight.
>
> **Safe to do both** — they're symmetric.

---

## What we're fixing

1. The hub (`embernet003`) had a stale `iptables-legacy` NAT REDIRECT rule
   `udp dpt:443 → :51820`. WG was bound directly to `:443` (per
   `ListenPort = 443` in the hub's `wg0.conf`) — but the rule was redirecting
   incoming `:443` packets to `:51820` where nothing listens, generating
   `ICMP udp port unreachable` back to peers. That rule is now gone on
   the hub.

2. Peers that had `ListenPort = 443` in their own `wg0.conf` were sending
   WG packets with `src_port = 443` to `hub:443`. Some Linux kernel
   configurations (or NAT pools that preserve source-port mapping for
   static-IP peers — which AWS does for elastic IPs) treat
   `src=443 → dst=443` as a self-loop and silently drop them. The Fragua
   edges had this — both now fixed by removing `ListenPort` from their
   wg0.conf so the kernel picks ephemeral source ports.

**Outbound to `hub:443` is preserved** — that's set via the `Endpoint`
line in the peer block, NOT `ListenPort`. Firewall view from the AWS
side is unchanged (still outbound UDP/443 only).

---

## Recovery commands — run on embernet004 AND/OR embernet005

> Each block is independent. You can do just one to get etcd quorum back,
> or both. Both is cleaner.

```bash
# ─── 0. Sanity baseline ─────────────────────────────────────────────────
sudo wg show wg0 | grep -E "listening port|latest handshake|transfer"
# A stuck node will show "transfer: 0 B received, NNN KiB sent" or an
# ancient "latest handshake".

# ─── 1. Remove vestigial iptables-legacy REDIRECT (if present) ──────────
sudo iptables-legacy -t nat -L PREROUTING --line-numbers -n -v 2>/dev/null \
  | grep "udp dpt:443.*51820"
# If a line N is shown, delete it:
sudo iptables-legacy -t nat -D PREROUTING <N>
# (Skip this step if no such rule exists on the AWS side.)

# ─── 2. Back up the WG conf ─────────────────────────────────────────────
sudo cp /etc/wireguard/wg0.conf \
        /etc/wireguard/wg0.conf.bak-$(date +%s)

# ─── 3. Check current conf, then remove ListenPort = 443 if present ─────
sudo grep -E "^ListenPort|^\[" /etc/wireguard/wg0.conf
# If `ListenPort = 443` appears under [Interface], drop it:
sudo sed -i '/^ListenPort *= *443$/d' /etc/wireguard/wg0.conf
# Confirm it's gone:
sudo grep -E "^ListenPort|^\[" /etc/wireguard/wg0.conf

# ─── 4. Restart wg-quick + trigger handshake ────────────────────────────
sudo systemctl restart wg-quick@wg0
sleep 3
ping -c 3 100.64.0.1     # hub WG IP — triggers handshake init
sleep 3

# ─── 5. Persist iptables changes (so reboot survives) ───────────────────
sudo iptables-legacy-save > /etc/iptables/rules.v4 2>/dev/null \
  || sudo netfilter-persistent save

# ─── 6. Verify recovery ─────────────────────────────────────────────────
sudo wg show wg0 | grep -E "listening port|latest handshake|transfer"
# Expected:
#   listening port: <ephemeral, e.g. 41271> (NOT 443)
#   latest handshake: < 30 seconds ago
#   transfer: <some KiB> received, <some KiB> sent
```

---

## What "good" looks like on this node afterward

```
interface: wg0
  public key: <some-base64>
  private key: (hidden)
  listening port: 51234              ← random ephemeral, NOT 443
peer: x6X+F8V0UNbGxzXddhxri+Yp091Tu9biFqDyTmNDUUk=   ← hub pubkey
  endpoint: 20.186.57.136:443        ← still dialing 443 outbound ✓
  allowed ips: 100.64.0.0/24
  latest handshake: 12 seconds ago   ← fresh
  transfer: 18.4 KiB received, 42.1 KiB sent
```

---

## Cluster recovery check (run from any node with kubectl access)

```bash
# Should return without "ServiceUnavailable"
sudo k3s kubectl get nodes

# https://clusters.embernet.ai/dashboard/ should stop returning Bad Gateway
# within ~30s of the second voter coming back online.
```

---

## If something looks weird

1. **Handshake still 0:** check `sudo wg show wg0` shows `listening port`
   != 443. If it's still 443, the `sed` didn't catch the line — open
   `/etc/wireguard/wg0.conf` and remove it manually, then restart again.

2. **Egress firewall worry:** the `Endpoint = 20.186.57.136:443` line in
   the hub's peer block is unchanged. AWS security groups / firewall
   only need outbound UDP/443 — they don't need to allow `src=443`
   anywhere.

3. **Need to revert:** the backup file is at
   `/etc/wireguard/wg0.conf.bak-<timestamp>`. Restore with
   `sudo cp <backup> /etc/wireguard/wg0.conf && sudo systemctl restart wg-quick@wg0`.

---

## Reference: confirmed working state on the Azure side

| Host | Listening port | Endpoint | Handshake | Status |
|---|---|---|---|---|
| embernet003 (hub) | **443** (fixed listener) | n/a — accepts | n/a | ✅ |
| embernet001 (Azure, etcd voter) | 48668 (ephemeral) | hub:443 | 13s ago | ✅ |
| fragua-edge-01 (Azure) | 41271 (ephemeral) | hub:443 | 7s ago | ✅ |
| fragua-edge-02 (Azure) | 57417 (ephemeral) | hub:443 | 7s ago | ✅ |
| embernet004 (AWS, etcd voter) | needs fix | hub:443 | never | 🔧 |
| embernet005 (AWS, etcd voter, relay node) | needs fix | hub:443 | never | 🔧 |

---

*2026-05-21, mid-outage. Demo with Gartner in <12h.*
