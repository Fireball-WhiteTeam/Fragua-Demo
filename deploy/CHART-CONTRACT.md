# EmberNET Helm Chart Contract: Pod Spec, Networking, Storage, Multi-Cluster

_Authoritative spec for aligning every `embernet-ai` app chart._
_Written 2026-07-21, grounded in live verification on the Fragua cluster._

This document exists because three separate classes of breakage have now been
traced to charts disagreeing about the same four things: **discovery labels**,
**overlay reachability**, **elevated networking**, and **storage durability**.
Every chart in the App Store catalog should be auditable against §9.

---

## 1. The two app classes (do not confuse them)

| | Tool Card apps | App Store apps |
|---|---|---|
| Where defined | hardcoded `portal-card` divs in dashboard HTML | Helm charts, deployed to EmberNODEs |
| Addressing | hardcoded subdomains | discovered via labels, proxied by the dashboard |
| This document | ❌ not applicable | ✅ this is the contract |

Everything below concerns **App Store apps only**.

---

## 2. Deploy paths: what actually creates the workload

### 2.1 Local / k3s tenant → `HelmChart` CRD

The dashboard never runs `helm` or `kubectl` against the workload. It writes a
K3s `HelmChart` CRD and k3s's helm-controller does the install:

```yaml
apiVersion: helm.cattle.io/v1
kind: HelmChart
metadata:
  name: <chart>-<node>              # release name; MUST be unique per node
  namespace: kube-system            # where the CRD lives (helm-controller watches here)
  labels:
    embernet.ai/store-app: "true"
    embernet.ai/app-id: "helm-<chart-name>"   # dedup key, see §3.3
    embernet.ai/app-name: "<display>"
spec:
  chart: <chart-name>
  repo: https://embernet-ai.github.io/<Repo>/
  version: "1.2.3"                  # ALWAYS pin; see §8.1
  targetNamespace: default          # where the workload lands
  valuesContent: |
    ...
```

> **Gotcha:** the CRD's own `namespace` and the release's `targetNamespace` are
> different fields. Any Secret the chart references (e.g. the provisioner shared
> secret, §5.3) must exist in **`targetNamespace`**, not in the CRD's namespace.

### 2.2 External tenant → Rancher Fleet

For tenants with `ClusterMode == "external"` and a Rancher cluster ID, the
dashboard writes a **Fleet Bundle** targeting the downstream cluster instead.
Consequences a chart author must respect:

- **No k3s helm-controller downstream.** Do not rely on `helm.cattle.io`
  behaviour, `HelmChart` CRDs, or k3s-specific StorageClasses (`local-path`).
- **The chart is rendered against a cluster you do not control.** Never assume
  Longhorn, Traefik, or a specific ingress class exists. Gate them behind values.
- **Namespaces may be pre-created and RBAC-restricted.** Do not create
  cluster-scoped resources unless the chart is explicitly an infra chart.
- **Image pull secrets differ.** Reference `imagePullSecrets` by value, never
  hardcode `ghcr-secret`.

Rule of thumb: **if a chart only works on k3s, it is not App-Store-ready.**

---

## 3. Discovery contract: how the dashboard sees the app

This is non-negotiable and the single most common cause of "deployed but
invisible".

### 3.1 Labels: on **both** the pod template and the Service

```yaml
labels:
  embernet.ai/store-app: "true"            # MANDATORY — discovery gate
  embernet.ai/app-name: "Ignition Edge"    # display name
  embernet.ai/gui-type: "web"              # web | shell | vm | none
  embernet.ai/gui-port: "8088"             # string, not int
  app.kubernetes.io/instance: {{ .Release.Name }}   # REQUIRED for proxy FQDN
```

### 3.2 Service naming

The Service **must be named exactly `{{ .Release.Name }}`**. It is both the
in-cluster DNS hostname and the identity used by the Running Apps tab. A
mismatch produces a 502 in the proxy overlay with no other symptom.

### 3.3 Annotations

```yaml
annotations:
  embernet.ai/app-icon: "https://…"        # Service
  embernet.ai/display-name: "…"            # Service, for multi-instance
```

`embernet.ai/app-id` is set by the dashboard on the **HelmChart CRD** (not the
pod) and drives `deduplicateApps()` so builtin / helm-repo / rancher entries
collapse to one tile. Charts should not set it themselves.

`embernet.ai/visibility: "infra"` in **Chart.yaml annotations** hides the app
from non-super-admins (v4.1.01+).

### 3.4 Label value legality

Label values must match `(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?`. **`+` is
illegal**: this is why `gui-type: "web+shell"` was removed; kube-apiserver
rejects the whole object. Use `web`; the POD SHELL button is added automatically
for Admin+.

### 3.5 Failure modes

| Missing | Effect |
|---|---|
| `embernet.ai/store-app=true` | never discovered; invisible everywhere |
| `app.kubernetes.io/instance` | falls back to `app`, then pod name (ephemeral, breaks on restart) |
| `embernet.ai/gui-port` | dashboard scans container ports, takes the first, often wrong |
| `embernet.ai/gui-type` | defaults to `web` if any port found |
| Service ≠ Release.Name | proxy targets wrong host → 502 |

---

## 4. Networking tiers: pick exactly one

**This is the section most charts get wrong.** Verified empirically on
`fragua-edge-01`, 2026-07-21.

### Tier 0: Local only (default; most apps)

Talks only to things inside the cluster. Needs nothing special. Node-RED,
Grafana, InfluxDB, Postgres, CODESYS all sit here.

```yaml
spec:
  # no hostNetwork, no capabilities, standard CNI
```

### Tier 1: Dials a REMOTE EmberNET service over the Flux/Ziti overlay

**The critical fact:** `embernet-endpoint` intercepts the overlay by binding
`100.65.0.0/16` on the **host's loopback** and proxying in userspace. That is
**host-scoped**. A pod in its own netns cannot reach it. Measured:

| From | resolve `ignition-cloud.…svc.cluster.local` | TCP `100.65.0.1:8060` |
|---|---|---|
| host | ✅ `100.65.0.1` | ✅ OK |
| normal pod | ❌ NXDOMAIN (CoreDNS) | ❌ **FAILED** |
| hostNetwork pod | — | ✅ OK |

> This is a **scope regression** from the June cutover. The old
> `flux-edge-tunnel` DaemonSet ran a node-scoped tproxy that served pods too.
> The endpoint replaced its host function but not its cluster function.

**Correct fix: per-pod Ziti identity.** Do NOT reach for `hostNetwork` here.

```yaml
provisioner:
  enabled: true
  url: "https://provisioner.embernet.ai/api/v1/provision"
  sharedSecretRef:
    name: "embernet-provisioner-credentials"   # must exist in targetNamespace
    key: "shared_secret"
```

Two init containers run before the app:

1. `provisioner-fetch`: POSTs the shared secret, receives a Ziti enrollment
   JWT, writes `/shared/flux.jwt`
2. `ziti-enroll`: `ziti edge enroll --jwt /shared/flux.jwt --out
   /shared/identity.json`

The app mounts the shared `emptyDir` at `/etc/openziti` and dials the overlay
itself. Reference implementations: `Ignition-Edge-Pod` 1.0.11+,
`embernet-probe` 1.2.1.

> **Known flake: handle it.** `ziti-cli` sometimes retries the enroll POST
> internally. The first attempt succeeds and writes `identity.json`; the second
> hits `INVALID_ENROLLMENT_TOKEN` and the CLI **exits non-zero anyway**. Under
> `set -e` the init container fails and the pod CrashLoops with a perfectly good
> identity on disk. Treat a non-empty `identity.json` containing the `ztAPI` key
> as success regardless of exit code: see
> `deploy/charts/embernet-probe-1.2.1/templates/deployment.yaml`.

> **Idempotency.** The provisioner deletes existing authenticators before
> minting a new OTT, so re-enrollment across pod restarts is safe. An OTT can
> only be redeemed against an identity with no active authenticator.

### Tier 2: Elevated networking (VPN / tunnel / raw sockets / L2)

Only for workloads that genuinely manipulate host networking, an endpoint
daemon, a router, a WireGuard peer, a packet probe.

```yaml
spec:
  hostNetwork: true
  dnsPolicy: Default          # NOT ClusterFirstWithHostNet — see below
  containers:
  - name: app
    securityContext:
      capabilities:
        add: ["NET_ADMIN", "NET_RAW"]   # never `privileged: true` if caps suffice
    volumeMounts:
    - { name: tun, mountPath: /dev/net/tun }
  volumes:
  - name: tun
    hostPath: { path: /dev/net/tun, type: CharDevice }
```

**`dnsPolicy` matters and is a real trap.** With `hostNetwork: true`:

- `ClusterFirstWithHostNet` → DNS goes to **CoreDNS**, which returns NXDOMAIN
  for remote-cluster FQDNs like `ignition-cloud.fireball-system.svc.cluster.local`
- `Default` → DNS uses the **host's** `/etc/resolv.conf`, which routes
  `~cluster.local` / `~flux.internal` to the endpoint resolver on `127.0.0.1:53`
  and resolves them to `100.65.x.x`

If a hostNetwork pod must resolve overlay names, use `dnsPolicy: Default`.
Alternative for a single fixed name: `hostAliases` mapping the FQDN to its
synthetic IP.

**Host DNS prerequisite** (node-level, not chart-level), required for Tier 2
name resolution:

```ini
# /etc/systemd/resolved.conf.d/embernet-ziti.conf
[Resolve]
DNS=127.0.0.1
Domains=~cluster.local ~flux.internal
```
plus `/etc/resolv.conf` → `/run/systemd/resolve/stub-resolv.conf`. If resolv.conf
is a static file ("foreign" mode), resolved's routing is bypassed entirely and
overlay names will not resolve. Also ensure no stale drop-in lists an upstream
resolver ahead of `127.0.0.1` for `~cluster.local`, an authoritative NXDOMAIN
from Azure ends the lookup before the endpoint is asked.

**Port conflicts.** `hostNetwork` binds the real node port. Two Tier-2 pods
wanting :8088 cannot co-schedule. Always pair with a `nodeSelector` and treat
the app as a singleton per node.

### Tier selection

| Need | Tier |
|---|---|
| in-cluster only | 0 |
| reach ignition-cloud / anvilmq / any remote EmberNET svc | **1** |
| create interfaces, tproxy, raw sockets, VPN | 2 |

Reaching for Tier 2 when Tier 1 suffices is the most common over-privilege
mistake. `hostNetwork` is not a substitute for a Ziti identity.

---

## 5. Storage contract: must survive reboots, reschedules, upgrades

### 5.1 Always name the StorageClass

```yaml
persistence:
  enabled: true
  storageClass: "longhorn"    # explicit, never "" and never omitted
  size: 20Gi
  accessMode: ReadWriteOnce
```

Never rely on the cluster default. The Fragua cluster shipped with **two**
StorageClasses both flagged default (`local-path` and `longhorn`), which makes
binding for a class-less PVC ambiguous. Fixed 2026-07-21 by demoting
`local-path`; charts should not depend on that having been done.

### 5.2 local-path vs longhorn: this decides reboot survival

| | `local-path` | `longhorn` |
|---|---|---|
| Backing | node-local dir | replicated distributed volume |
| Survives node reboot | yes (same node) | yes |
| Survives **reschedule to another node** | ❌ **data stranded** | ✅ follows the pod |
| Expandable | ❌ | ✅ |
| Binding mode | WaitForFirstConsumer | Immediate |

**Any stateful app must use `longhorn`.** `local-path` silently pins a workload
to one node forever, the pod reschedules and comes up empty rather than failing
loudly, which is the worst failure mode.

### 5.3 Secrets are namespace-local

A chart referencing `embernet-provisioner-credentials` needs it in the release's
`targetNamespace`. It is **not** replicated automatically:

```bash
kubectl -n fireball-system get secret embernet-provisioner-credentials -o json \
  | jq '.metadata={name:"embernet-provisioner-credentials",namespace:"default"}' \
  | kubectl apply -f -
```

Same applies to `ghcr-secret` for private images.

### 5.4 Separate PVCs by lifecycle

Do not put everything in one volume. Ignition Edge is the model:

| PVC | Size | Why separate |
|---|---|---|
| `<release>-data` | 20Gi | gateway db/config, the thing you must not lose |
| `<release>-backup` | 50Gi | `.gwbk` archives, large, regenerable |
| `<release>-modules` | 5Gi | modules, replaceable from image |

This lets you resize/reclaim independently and keeps a backup blowout from
filling the config volume.

### 5.5 Deployment vs StatefulSet

Use a **StatefulSet** when the app has per-replica identity or must not have two
instances touching one RWO volume during rollout. A `Deployment` with
`RollingUpdate` + an RWO PVC will deadlock: the new pod cannot attach the volume
until the old one releases it. Either use `strategy: Recreate` or a StatefulSet.
Most singleton industrial apps want `Recreate`.

---

## 6. Resources, probes, scheduling

```yaml
resources:
  requests: { cpu: 250m, memory: 512Mi }
  limits:   { cpu: 2,    memory: 2Gi }

startupProbe:                 # JVM apps need this or liveness kills them mid-boot
  httpGet: { path: /StatusPing, port: 8088 }
  failureThreshold: 30
  periodSeconds: 10
livenessProbe:
  httpGet: { path: /StatusPing, port: 8088 }
  periodSeconds: 30
readinessProbe:
  httpGet: { path: /StatusPing, port: 8088 }

nodeSelector:
  kubernetes.io/hostname: "<node>"    # required for Tier 2; recommended for Tier 1
```

**Sizing reality check.** Edge nodes are small: Fragua runs 2 vCPU with 8 GB
(edge-01) and 4 GB (edge-02). An Ignition JVM defaulting to a 1 GB heap on a
4 GB node alongside Longhorn and etcd will cause disk/memory pressure. Set
`gateway.heap.max` explicitly (512 MB on edge nodes). Requests that exceed a
small node's allocatable make the pod permanently `Pending`.

**Disk IOPS is a real constraint**, not a theoretical one: a 30 GB Premium SSD
(P4) delivers 120 IOPS, which is below what etcd + Longhorn + a JVM need
together. That combination previously caused multi-second etcd apply latency and
Rancher connectivity flapping. Prefer ≥128 GB (P10) on any node running stateful
App Store workloads.

---

## 7. Canonical pod spec (Tier 1, stateful, dashboard-visible)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}
spec:
  strategy: { type: Recreate }          # RWO volume — see §5.5
  selector:
    matchLabels:
      app.kubernetes.io/instance: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app.kubernetes.io/instance: {{ .Release.Name }}
        embernet.ai/store-app: "true"
        embernet.ai/app-name: {{ .Values.embernet.appName | quote }}
        embernet.ai/gui-type: "web"
        embernet.ai/gui-port: "8088"
    spec:
      nodeSelector:
        kubernetes.io/hostname: {{ .Values.nodeSelector.hostname | quote }}
      imagePullSecrets:
        - name: {{ .Values.imagePullSecret | default "ghcr-secret" }}

      initContainers:
      {{- if .Values.provisioner.enabled }}
        - name: provisioner-fetch          # → /shared/flux.jwt
          image: "{{ .Values.provisioner.initImage.repository }}:{{ .Values.provisioner.initImage.tag }}"
          env:
            - name: SHARED_SECRET
              valueFrom:
                secretKeyRef:
                  name: {{ .Values.provisioner.sharedSecretRef.name }}
                  key:  {{ .Values.provisioner.sharedSecretRef.key }}
          volumeMounts: [{ name: ziti, mountPath: /shared }]
        - name: ziti-enroll                # → /shared/identity.json
          image: "{{ .Values.provisioner.initImage.repository }}:{{ .Values.provisioner.initImage.tag }}"
          command: ["sh","-c"]
          args:
            - |
              # Tolerate the ziti-cli internal-retry flake: a non-zero exit with
              # a valid identity on disk is success. See §4 Tier 1.
              ziti edge enroll --jwt /shared/flux.jwt --out /shared/identity.json || true
              grep -q ztAPI /shared/identity.json
          volumeMounts: [{ name: ziti, mountPath: /shared }]
      {{- end }}

      containers:
        - name: app
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          ports: [{ name: http, containerPort: 8088 }]
          volumeMounts:
            - { name: data,    mountPath: /usr/local/bin/ignition/data }
            - { name: backup,  mountPath: /restore }
            {{- if .Values.provisioner.enabled }}
            - { name: ziti,    mountPath: /etc/openziti }
            {{- end }}
          resources: {{- toYaml .Values.resources | nindent 12 }}
          startupProbe:
            httpGet: { path: /StatusPing, port: http }
            failureThreshold: 30
            periodSeconds: 10

      volumes:
        - name: data
          persistentVolumeClaim: { claimName: {{ .Release.Name }}-data }
        - name: backup
          persistentVolumeClaim: { claimName: {{ .Release.Name }}-backup }
        {{- if .Values.provisioner.enabled }}
        - name: ziti
          emptyDir: {}        # identity is re-enrolled each start; NOT persisted
        {{- end }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}              # MUST equal release name — §3.2
  labels:
    embernet.ai/store-app: "true"
    embernet.ai/app-name: {{ .Values.embernet.appName | quote }}
    embernet.ai/gui-type: "web"
    embernet.ai/gui-port: "8088"
  annotations:
    embernet.ai/app-icon: {{ .Values.embernet.icon | quote }}
spec:
  selector:
    app.kubernetes.io/instance: {{ .Release.Name }}
  ports: [{ name: http, port: 8088, targetPort: http }]
```

> The Ziti identity lives in an `emptyDir`, deliberately. It is re-enrolled on
> every start, and the provisioner is idempotent (§4). Persisting it would
> eventually strand a stale certificate against a rotated identity.

---

## 8. Versioning and release hygiene

1. **Always pin `spec.version`.** An unpinned HelmChart CRD silently upgrades on
   the next reconcile, that is how you get an unplanned production change.
2. **Release name = `<chart>-<node>`.** Enforces the one-per-node singleton model
   and keeps the Service FQDN stable.
3. **Deleting workloads without `helm uninstall` orphans the release.** The
   Fragua cluster carried two `flux-tunnel-*` releases reporting `deployed` with
   zero backing resources for weeks; a later `helm upgrade --install` would have
   tried to upgrade a phantom instead of installing clean. Always
   `helm uninstall`, or delete the `HelmChart` CRD and let the controller clean up.
4. **Singleton apps** (`Singleton == true`) are blocked from a second install
   with the same release name, by design.

---

## 9. Audit checklist

Run this against every chart in `HelmRepoURLs`:

- [ ] `embernet.ai/store-app: "true"` on **pod template AND Service**
- [ ] `app.kubernetes.io/instance: {{ .Release.Name }}` on pod template
- [ ] `gui-type` / `gui-port` present and correct; no `+` in any label value
- [ ] Service named exactly `{{ .Release.Name }}`
- [ ] `persistence.storageClass` explicit, `longhorn` for anything stateful
- [ ] Separate PVCs by lifecycle (data / backup / modules)
- [ ] `Recreate` strategy or StatefulSet if an RWO volume is mounted
- [ ] Networking tier chosen deliberately; `hostNetwork` only for Tier 2
- [ ] If it dials a remote EmberNET service → `provisioner.enabled` supported
- [ ] `ziti-enroll` tolerates the non-zero-exit-with-valid-identity flake
- [ ] `dnsPolicy: Default` on any hostNetwork pod resolving overlay names
- [ ] Capabilities are `NET_ADMIN`/`NET_RAW`, not blanket `privileged: true`
- [ ] Resource requests fit a 4 GB / 2 vCPU node; JVM heap pinned
- [ ] `startupProbe` on slow-booting apps
- [ ] `imagePullSecrets` configurable, not hardcoded
- [ ] No k3s-only assumptions (works under Fleet on an external cluster)
- [ ] `spec.version` pinned at deploy time

---

## 10. Known-good references

| Concern | Reference |
|---|---|
| Per-pod Ziti identity | `embernet-probe` 1.2.1, `Ignition-Edge-Pod` 1.0.11+ |
| `ziti-enroll` flake tolerance | `deploy/charts/embernet-probe-1.2.1/templates/deployment.yaml` |
| Multi-PVC lifecycle split | `Ignition-Edge-Pod` (data / backup / modules) |
| Host-level overlay + elevated net | `embernet-endpoint` container contract, `.agent/CREDENTIALS.md` |
| Dashboard discovery internals | `industrial-dashboard/.agent/APP_STORE_DEPLOYMENT_FLOW.md` |
