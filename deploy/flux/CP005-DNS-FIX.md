# cp005 Flux-Edge-Tunnel DNS Fix — Engineer Reference

> **Status (2026-05-28):** Chart fix shipped in `Embernet-ai/flux-helm-charts` `flux-edge-tunnel v2.1.1`. Live cp005 DaemonSet patched directly via kubectl (revert risk on next un-chart-aware helm upgrade — see §4).
> **Affected node:** `embernet-cp-005` (AWS, `eu-east-2` / `us-east-2`)
> **Affected workload:** `flux-tunnel-embernet-cp005-flux-edge-tunnel` DaemonSet in `flux-system`
> **Surface symptom:** Ziti dials terminated at cp005 fail with `lookup <K8s service FQDN> on 172.31.0.2:53: no such host` when the service's `host.v1` config dials by FQDN instead of ClusterIP.
> **Long-term owner:** flux-edge-tunnel / flux-helm-charts.

---

## 1. Root cause

The flux-edge-tunnel DaemonSet runs `hostNetwork: true`. The default `dnsPolicy: Default` makes the pod inherit the **node's** `/etc/resolv.conf` rather than getting K8s CoreDNS injected.

On AWS-hosted K3s nodes the node's resolv.conf points at the **AWS instance resolver** (`172.31.0.2` on us-east-2, or the equivalent for the VPC's DHCP option set). That resolver doesn't know `*.svc.cluster.local`, so any service FQDN lookup from the tunnel fails NXDOMAIN.

cp001 (Azure) didn't hit this because Azure's instance resolver behaves differently and/or the chart was historically installed with `dnsPolicy: ClusterFirstWithHostNet` on Azure nodes — only cp005 was on `Default`. Verify on your other AWS nodes (`cp004` if present, future `cp006`+) before assuming they're fine.

---

## 2. Why this matters for Fragua + future tenants

When a tenant's Ziti `host.v1` config dials the upstream service by **FQDN** (e.g. `embernet-dashboard.fireball-system.svc.cluster.local`), cp005's flux-edge-tunnel does the DNS lookup and fails. Symptom on the client (probe) side looks like:

```
unable to dial service 'embernet-dashboard.fireball-system.svc.cluster.local'
(dial failed: ... error: (dial tcp: lookup embernet-dashboard.fireball-system.svc.cluster.local on 172.31.0.2:53: no such host))
```

Today's workaround for Fragua's probe-callback Ziti service: the `host.v1` is configured with the dashboard alias **ClusterIP** (`10.43.187.123`) instead of the FQDN. That's brittle — ClusterIPs change if the Service is recreated, and it's not the documented pattern in the External Tenant Guide (which expects FQDNs).

With this fix, FQDN-based `host.v1` configs work cleanly from cp005.

---

## 3. The fix (two layers)

### 3.1 Chart layer — permanent (`flux-edge-tunnel v2.1.1`)

`Embernet-ai/flux-helm-charts` `main` now has a new values block:

```yaml
# values.yaml
dnsConfig:
  enabled: false           # opt-in
  nameservers: []          # e.g. [10.43.0.10] — cluster CoreDNS ClusterIP
  searches: []
  options: []
```

`templates/daemonset.yaml` renders `spec.dnsConfig` from these values when `enabled: true`. Backwards-compatible (default off = no change for existing installs).

`Chart.yaml`: `2.1.0 → 2.1.1`. AppVersion unchanged.

### 3.2 Live cluster — DaemonSet directly patched

Until the next helm-managed upgrade of `flux-tunnel-embernet-cp005-flux-edge-tunnel` pulls the new chart, the live DS is patched via kubectl:

```yaml
spec:
  template:
    spec:
      dnsPolicy: ClusterFirstWithHostNet           # was Default
      dnsConfig:
        nameservers:
          - 10.43.0.10                              # K3s CoreDNS ClusterIP
```

Verified resolv.conf inside the rolled pod:

```
search flux-system.svc.cluster.local svc.cluster.local cluster.local us-east-2.compute.internal
nameserver 10.43.0.10
options ndots:5
```

CoreDNS is the only nameserver. The AWS resolver (172.31.0.2) was removed by `ClusterFirstWithHostNet` semantics.

---

## 4. Upgrade caveat — patch reverts on helm un-aware upgrade

Anyone running `helm upgrade flux-tunnel-embernet-cp005-flux-edge-tunnel ...` from an OLDER chart (pre-2.1.1) or from `2.1.1` WITHOUT `--set dnsConfig.enabled=true` etc. will **revert the live patch back to `dnsPolicy: Default`** and the bug returns.

To make the fix survive future helm-managed upgrades:

```bash
helm upgrade flux-tunnel-embernet-cp005 embernet/flux-edge-tunnel \
  --version 2.1.1 \
  --reuse-values \
  --set dnsPolicy=ClusterFirstWithHostNet \
  --set dnsConfig.enabled=true \
  --set 'dnsConfig.nameservers={10.43.0.10}'
```

(`helm` `--set` parses commas as separators — use the `{a,b}` list syntax above, or `-f` an overrides file.)

Audit-recommended: do the same for every other AWS-hosted flux-edge-tunnel DaemonSet in the fleet. To list candidates:

```bash
sudo k3s kubectl get ds -A -l app.kubernetes.io/name=flux-edge-tunnel \
  -o custom-columns="NS:.metadata.namespace,NAME:.metadata.name,NODE:.spec.template.spec.nodeName,DNS:.spec.template.spec.dnsPolicy"
```

Any DS with `DNS=Default` on an AWS-hosted node is at risk.

---

## 5. Verification

After applying:

```bash
# 1. resolv.conf inside the tunnel pod has CoreDNS only
sudo k3s kubectl -n flux-system exec <tunnel-pod> -- cat /etc/resolv.conf | head -3

# 2. A dial from a Ziti client identity that terminates on this tunnel
#    succeeds for an FQDN-based host.v1 config. Quickest live test:
#    re-point a known service's host.v1 from ClusterIP → FQDN and
#    watch the next dial succeed.

# 3. No `lookup ... on 172.31.0.2:53: no such host` errors in the tunnel
#    log since the rollout.
```

---

## 6. Related context

- Fragua probe currently relies on `host.v1` using ClusterIP `10.43.187.123` for the dashboard alias Service. With this fix in place, that can be safely switched back to the FQDN `embernet-dashboard.fireball-system.svc.cluster.local`. Not switching it back today — the ClusterIP path is verified working, and the FQDN switch is a separate cleanup.
- Upstream OpenZiti behavior with `dnsPolicy: ClusterFirstWithHostNet` matches what cp001 (Azure) was already doing successfully — this fix brings cp005 (AWS) into parity.
- Backup of the original cp005 DaemonSet YAML is on embernet001 at `/tmp/cp005-ds-backup-<timestamp>.yaml`. Restore with `kubectl apply -f` if rollback is needed.

---

*Filed by Patrick Ryan during Fragua-Demo bring-up, 2026-05-28. Chart commit: `Embernet-ai/flux-helm-charts@1eef8a33` "flux-edge-tunnel v2.1.1: dnsConfig override for hostNetwork pods that need CoreDNS".*
