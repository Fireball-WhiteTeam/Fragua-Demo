#!/bin/bash
# flux-edge-tunnel Helm install for Fragua cluster
# Reference: team-operations-manual/runbooks/FLUX_EDGE_TUNNEL_DEPLOYMENT.md
#
# Run as root on fragua-edge-01 with KUBECONFIG=/etc/rancher/k3s/k3s.yaml.
# JWT files expected at /tmp/Fragua-Embernode-0001.jwt and 0002.jwt.

set -e
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

CHART_REPO=https://embernet-ai.github.io/flux-helm-charts
CHART_VERSION=2.0.8
CHART=flux-edge-tunnel
NS=flux-system

helm repo add embernet "$CHART_REPO" 2>/dev/null || true
helm repo update

deploy_node() {
  local NODE_HOSTNAME=$1     # e.g. fragua-edge-01
  local JWT_PATH=$2          # e.g. /tmp/Fragua-Embernode-0001.jwt
  local RELEASE=flux-tunnel-${NODE_HOSTNAME}

  local JWT
  JWT="$(tr -d '[:space:]' < "$JWT_PATH")"

  # Sanity check signature segment length
  local SIG="${JWT##*.}"
  if [ "${#SIG}" -ne 683 ]; then
    echo "WARN: signature length ${#SIG} != 683 (RS256 RSA-4096). JWT may be truncated."
  fi

  helm upgrade --install "$RELEASE" "$CHART" \
    --repo "$CHART_REPO" \
    --version "$CHART_VERSION" \
    -n "$NS" --create-namespace \
    --set zitiEnrollToken="$JWT" \
    --set secret.existingSecretName="" \
    --set pvc.storageClass="longhorn" \
    --set nodeSelector."kubernetes\.io/hostname"="$NODE_HOSTNAME" \
    --set serviceRegistration.enabled=false
}

deploy_node fragua-edge-01 /tmp/Fragua-Embernode-0001.jwt
deploy_node fragua-edge-02 /tmp/Fragua-Embernode-0002.jwt

kubectl -n "$NS" get pods -l app.kubernetes.io/name=flux-edge-tunnel -o wide
