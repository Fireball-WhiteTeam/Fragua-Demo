# Fragua Edge Tuning Notes

Applied 2026-05-19 / 20 to stabilize Rancher `Connected/Ready` state.

## Root cause: disk IOPS, not RAM

Initial diagnosis assumed RAM pressure. Actually:
- 4 GB RAM is fine for the full stack (~1.7-1.9 GB always available)
- Bottleneck was **Premium SSD P4 tier** (120 IOPS / 25 MB/s) on the 30 GB OS disk

Under load (Longhorn CSI churn + Ignition writes + Rancher agent), etcd's
fsync queue saturated, `apply request took too long` errors hit 2-3 seconds,
and Rancher's 45s synchronous health probe timed out.

## Fixes applied (in order of impact)

1. **OS disk resize 30 → 128 GB on fragua-edge-01** (P4 → P10 tier)
   ```
   az vm deallocate -g rg-fragua-demo -n fragua-edge-01
   az disk update -g rg-fragua-demo -n <disk-name> --size-gb 128
   az vm start -g rg-fragua-demo -n fragua-edge-01
   ```
   Result: 90× lower write latency (149 ms → 1.59 ms).

2. **Disable Microsoft Defender (mdatp) on both edges**
   ```
   systemctl stop mdatp && systemctl disable mdatp && systemctl mask mdatp
   ```
   Result: ~200 MB RAM freed per edge.

3. **Lower Ignition Edge JVM heap 1024 → 512 MB**
   Edit `/opt/embernet/ignition-edge/data/ignition.conf`:
   ```
   wrapper.java.maxmemory=512
   ```
   `podman restart ignition-edge` to apply.
   Result: ~500 MB headroom; Edge edition runs comfortably with 512 MB heap.

## Why edge-02 wasn't resized

edge-02 is a K3s agent (no etcd, no apiserver) — its disk I/O is much lower
than edge-01. Kept at 30 GB P4 to save cost. If we add more PVCs that land
on edge-02, may need to revisit.

## What's still on the table

- Phase 7b: Ziti service def for `ignition-cloud.fireball-system.svc` —
  admin task on EmberNet controller.
- Phase 7c: strip Vision module from FRAGUAV2 project (Edge edition
  doesn't support Vision).

(`embernet-ai/network-controller` skipped for Fragua — it's designed for
physical edge appliances with downstream PLC/HMI devices behind a
macvlan'd OpenWrt firewall. Not applicable to Azure VMs with no
downstream LAN segment.)
