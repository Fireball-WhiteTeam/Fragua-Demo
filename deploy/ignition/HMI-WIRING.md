# Lighting up the FRAGUAV3 HMI from EmberBurn

The FRAGUAV3 Perspective views (`low`, `med`, `HVAC`, `Luces`, `Page/Charts`,
`Page/Alarms`, `fragua`) bind to **65 tags at `[default]PLC_PRG/*`** — the
CODESYS tag structure. This is how EmberBurn drives them without editing a
single view.

Two independent paths run off the same EmberBurn instance:

```
                     ┌─ OPC-UA :4840 ─→ Ignition Edge [default]PLC_PRG/* ─→ FRAGUAV3 HMI
EmberBurn (edge-01) ─┤
                     └─ Sparkplug B ──→ AnvilMQ ─→ Postgres ─→ EmberNet dashboard
```

The HMI path is OPC-UA and stays on the edge. The dashboard path is Sparkplug
and goes to the cloud. Neither depends on the other.

---

## 1. Tags exist first

The 65 PLC_PRG tags are pushed into EmberBurn, not into a chart:

```bash
KEY=$(kubectl -n default get secret emberburn-fragua-api -o jsonpath='{.data.apiKey}' | base64 -d)
python deploy/emberburn/seed-tags.py --url http://127.0.0.1:5000 --api-key "$KEY" \
  --file deploy/emberburn/fragua-hmi-tags.json
```

Confirm they are on the OPC UA server before touching Ignition — everything
below binds to node ids, and there is nothing to bind to until this is done.

## 2. OPC UA connection in Ignition Edge

Gateway web UI → **Config → OPC Client → OPC Connections → Create new OPC UA
Connection**:

| Field | Value |
|---|---|
| Name | `EmberBurn` |
| Endpoint URL | `opc.tcp://emberburn-fragua-edge-01-opcua.default.svc.cluster.local:4840/freeopcua/server/` |
| Security Policy | `None` |
| Message Security Mode | `None` |
| Authentication | Anonymous |

**The name must be exactly `EmberBurn`.** The tag import file references the
connection by name (`"opcServer": "EmberBurn"`); a different name imports 65
tags that are all permanently `Bad_Stale`.

Security is `None` because EmberBurn ships `OPC_SECURITY_ENABLED=false` /
`OPC_ALLOW_ANONYMOUS=true`. Turning security on in Ignition without also
enabling it in EmberBurn's values fails the handshake with no useful error.

EmberBurn runs `hostNetwork: true` on this edge, so the ClusterIP Service
resolves to the node. A pod reaches it through the Service name above; from the
node itself it is `opc.tcp://127.0.0.1:4840/freeopcua/server/`.

## 3. Import the tags

Designer → **Tag Browser → default provider → ⋮ → Import Tags** →
`fragua-hmi-tags-import.json`, mode **Overwrite and Merge**.

That creates `[default]PLC_PRG/` with all 65 tags already bound to their OPC
item paths — the exact paths the views expect. No view is edited.

Item paths are stable string node ids derived from the tag name
(`ns=2;s=PLC_PRG/Baja_Temp`). Before EmberBurn 4.1.17 they were numeric and
assigned in creation order, so re-seeding the tag set in a different order
would have silently re-pointed every binding at a different tag. Pin
`image.tag` at 4.1.17 or later.

Regenerate the import file whenever the HMI tag list changes — it is derived
from `deploy/emberburn/fragua-hmi-tags.json`, which is the source of truth.

## 4. Verify

1. **Config → OPC Client → OPC Connections** → `EmberBurn` shows `Connected`.
2. **OPC Browser** under `EmberBurn` lists `EdgeDevice` with 90 variables (65
   HMI + the 25-point site set — both live on the same server).
3. Tag Browser → `[default]PLC_PRG` → values changing, quality `Good`.
4. Open the HMI. Things that should be true within a minute:
   - `low` / `med`: room temperature sits at its setpoint and the compressor
     cycles — the two agree, because the compressor is under hysteresis control
     off that same temperature.
   - `HVAC`: output modulates with deviation and reads `0` when the unit is off.
   - `Luces`: the 8 zones switch independently.
   - `fragua`: `KW` equals the sum of the sub-meters plus base load, `KVA` is
     `KW / FP`, and `KWH` climbs faster when `KW` is higher.
   - The clock (`Hora_H` / `Hora_M` / `Hora_S`) shows the actual time.

If tags are `Bad_NotFound`, the connection name or an item path is wrong. If
they are `Good` but frozen, EmberBurn is up but not simulating — check that the
tags were seeded with `simulate: true` rather than created bare.

## Troubleshooting

**Everything `Bad_Stale` right after import.** The OPC connection name does not
match `opcServer` in the import file. Rename the connection to `EmberBurn`
rather than re-importing.

**A handful of tags `Bad_NotFound`.** The tag exists in Ignition but not on the
OPC server — re-run the seeder; it is idempotent and redefines in place.

**Values move but the HMI does not.** The views bind to `[default]PLC_PRG/…`.
Check the tags landed in the **default** provider and under `PLC_PRG`, not at
the provider root.
