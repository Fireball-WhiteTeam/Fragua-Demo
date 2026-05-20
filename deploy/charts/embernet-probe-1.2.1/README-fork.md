# embernet-probe 1.2.1 (Fragua fork)

Local fork of `embernet-ai/Network-Probe` chart, version-bumped from 1.2.0 → 1.2.1.

## What changed vs upstream 1.2.0

**One feature added: `provisioner.enabled` auto-enrollment via `embernet-provisioner`.**

When enabled, the probe pod runs an `initContainer` that:
1. Reads `EMBERNET_SHARED_SECRET` from a K8s Secret (`embernet-provisioner-credentials.shared_secret` by default).
2. POSTs to `https://provisioner.embernet.ai/api/v1/provision` with `{shared_secret, device_name, display_name}`.
3. Receives `{flux_jwt, ...}` in the response.
4. Runs `ziti edge enroll --jwt <jwt> --out /shared/identity.json`.
5. Main container mounts the emptyDir at `/etc/openziti` and reads the identity from there.

This replaces the upstream pattern where the operator/Dashboard had to manually pre-create a Ziti identity, enroll it, base64 the JSON, and pass it via `--set probe.zitiIdentityBase64=...` at install time.

## Files touched
- `Chart.yaml` — bumped to 1.2.1
- `values.yaml` — added `provisioner.*` block + `serviceAccount.imagePullSecrets`
- `templates/deployment.yaml` — conditional `initContainers:` + emptyDir volume swap
- `templates/secret.yaml` — omit `ziti-identity.json` data key when `provisioner.enabled=true`
- `templates/serviceaccount.yaml` — propagate `serviceAccount.imagePullSecrets`

## Backwards-compat
`provisioner.enabled` defaults to `false`. Existing 1.2.0 installs that pass `probe.zitiIdentityBase64` keep working unchanged.

## How to deploy on Fragua

```bash
# 1. Operator creates the shared-secret Secret once per cluster (out of band)
kubectl -n fireball-system create secret generic embernet-provisioner-credentials \
  --from-literal=shared_secret="<EMBERNET_SHARED_SECRET>"

# 2. Install the fork from this directory (path is relative to repo root)
helm upgrade --install embernet-probe ./deploy/charts/embernet-probe-1.2.1 \
  -n fireball-system \
  --set provisioner.enabled=true \
  --set probe.tenant=fragua-demo \
  --set probe.site=fragua \
  --set nodeSelector."kubernetes\.io/hostname"=fragua-edge-01 \
  --set serviceAccount.imagePullSecrets[0].name=ghcr-secret
```

## Upstream PR
Once this is proven on Fragua, the chart changes should land back into `embernet-ai/Network-Probe` as v1.2.1. Same template pattern can be lifted into other in-cluster charts (e.g. `ignition-edge-x86`) that also need Ziti identities.

## Why this matters
Today, every helm-deployed app that needs Ziti requires:
1. Engineer-side manual `ziti edge create identity` per node
2. Engineer-side service-policy + edge-router-policy per service
3. Operator-side base64 of identity.json into helm `--set` at install time

With `provisioner.enabled=true`:
- The provisioner already creates identities with the right role attributes
- One service-policy per role attribute covers an entire fleet of pods
- Operators just deploy. No ziti CLI. No base64. No manual coordination.
