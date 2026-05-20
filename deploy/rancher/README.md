# Phase 8 — Rancher Cluster Import (Fragua)

## Summary
The Fragua K3s cluster is imported into Rancher at `https://clusters.embernet.ai`.

| Field | Value |
|---|---|
| Cluster ID (Rancher) | `c-j7gtg` |
| Display Name | `Fragua` |
| Provisioning CR | `cluster.provisioning.cattle.io/c-j7gtg` (in `fleet-default`) |
| Mgmt CR | `cluster.management.cattle.io/c-j7gtg` |
| Labels | `embernet.ai/tenant=fragua-demo`, `embernet.ai/site=fragua` |
| K8s version detected | `v1.35.4+k3s1` |
| Nodes registered | 2 (fragua-edge-01, fragua-edge-02) |

## Manifest URL (per-cluster, do not commit if sensitive)
The import manifest URL contains a single-cluster registration token. Treat as a secret.
```
https://clusters.embernet.ai/v3/import/td9n7mcx6k99fwqsc9qr8wvzkmcxq5tzcc8h5vcptbxxbf5r5n6xbh_c-j7gtg.yaml
```

Apply on the Fragua cluster with:
```bash
kubectl apply -f <manifest-url>
```

## Required Rancher-side setting

K3s does NOT ship `/etc/kubernetes/ssl/certs/serverca` (Rancher's default
strict-CA-verify expects it). Two paths to make the cattle-cluster-agent boot
on K3s imports:

- **Global (applied here):** flip `agent-tls-mode` to `system-store`
  ```bash
  kubectl patch setting.management.cattle.io agent-tls-mode --type=merge \
    -p '{"value":"system-store"}'
  ```
  This means the agent uses the host OS trust store to validate Rancher's TLS
  cert — which is fine because `clusters.embernet.ai` has a real cert.

- **Per-cluster alternative:** `cluster.spec.agentEnvVars: [{name: STRICT_VERIFY, value: "false"}]`.

## Known runtime issue: Connected state flapping

The cluster intermittently flips between `Connected=True` and `Connected=False`
because the K3s apiserver on `fragua-edge-01` (Standard_B2s, 4 GB RAM) is under
memory pressure from the full stack (Ignition Java 1 GB heap + Longhorn +
CODESYS + cattle-cluster-agent + flux-edge-tunnel + Microsoft Defender).

**Fix:** resize `fragua-edge-01` to Standard_B2ms (8 GB) or D2s_v5 (8 GB).

```bash
az vm resize -g rg-fragua-demo -n fragua-edge-01 --size Standard_B2ms
```

The VM will reboot. After it comes back, `wg-quick@wg0` is enabled at boot
(with Restart=on-failure drop-in), K3s server auto-starts, and Rancher should
report `Connected=True Ready=True` stably.

## TLS / agent re-entry on a clean install

If for any reason the cattle-cluster-agent deployment needs to be reinstalled
on Fragua (e.g., after a teardown), re-apply the same manifest URL — the token
remains valid for the lifetime of the cluster CR.
