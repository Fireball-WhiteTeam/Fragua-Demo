#!/bin/bash
# Ignition Edge 8.3.6 install for Fragua edges.
# Mirrors install_ignition_edge() from Fireball-Red-Team/deployment:deploy-ut3-cp02.sh.
# Run as root.

set -e

log()  { echo "[$(date -Is)] $*"; }
warn() { echo "[$(date -Is)] WARN: $*" >&2; }

IGNITION_EDGE_VERSION="8.3.6"
IGNITION_EDGE_IMAGE="ghcr.io/embernet-ai/ignition-edge@sha256:c0be0e41d302f1509036a71fd82ce33c09e56396153f68a5836152690538b760"
DATA_DIR="/opt/embernet/ignition-edge/data"
IGNITION_ADMIN_PASSWORD="${IGNITION_ADMIN_PASSWORD:-GreatBallzFire01}"

mkdir -p "${DATA_DIR}"

# Tear down stale 'ignition' container if present (legacy Standard-era name)
if podman container exists ignition 2>/dev/null; then
  log "Removing legacy 'ignition' container (replaced by ignition-edge)"
  podman rm -f ignition >/dev/null 2>&1 || true
fi

# Gate: skip if ignition-edge already running
if podman container exists ignition-edge 2>/dev/null; then
  ign_state=$(podman inspect ignition-edge --format '{{.State.Status}}' 2>/dev/null || echo unknown)
  if [[ "${ign_state}" == "running" ]]; then
    log "ignition-edge already running on $(hostname) — skipping"
    exit 0
  fi
  log "ignition-edge exists but ${ign_state} — recreating"
  podman rm -f ignition-edge >/dev/null 2>&1 || true
fi

# Seed data dir on first run (avoids cold-start config crash)
if [[ ! -f "${DATA_DIR}/.embernet-seeded" ]]; then
  log "First-run: seeding ${DATA_DIR} from image..."
  podman pull "${IGNITION_EDGE_IMAGE}" || true
  if podman run --rm \
       --network=none \
       --entrypoint /bin/bash \
       -v "${DATA_DIR}:/seed" \
       "${IGNITION_EDGE_IMAGE}" \
       -c 'cp -an /usr/local/bin/ignition/data/. /seed/ 2>/dev/null && chmod +x /seed/*.sh /seed/**/*.sh 2>/dev/null; touch /seed/.embernet-seeded'; then
    find "${DATA_DIR}" -name '*.sh' -exec chmod +x {} + 2>/dev/null || true
    log "  seeded $(du -sh "${DATA_DIR}" 2>/dev/null | cut -f1)"
  else
    warn "Seed step failed — first start may crash. Manual seed:"
    warn "  podman run --rm --network=none -v ${DATA_DIR}:/seed --entrypoint /bin/bash ${IGNITION_EDGE_IMAGE} -c 'cp -an /usr/local/bin/ignition/data/. /seed/'"
  fi
else
  log "Data dir already seeded — preserving operator state"
fi

log "Starting ignition-edge container"
podman run -d \
  --name ignition-edge \
  --restart=always \
  --network=host \
  -e ACCEPT_IGNITION_EULA=Y \
  -e GATEWAY_ADMIN_PASSWORD="${IGNITION_ADMIN_PASSWORD}" \
  -e IGNITION_EDITION=edge \
  -v "${DATA_DIR}:/usr/local/bin/ignition/data" \
  "${IGNITION_EDGE_IMAGE}"

mkdir -p /etc/systemd/system
podman generate systemd --name ignition-edge --new --restart-policy=always \
  > /etc/systemd/system/container-ignition-edge.service 2>/dev/null || true
systemctl daemon-reload
systemctl enable container-ignition-edge.service 2>/dev/null || true

# Wait for Edge to bind 8088
log "Waiting for :8088 to bind (up to 120s)..."
ign_wait=0
while [[ ${ign_wait} -lt 120 ]]; do
  if ss -tlnp 2>/dev/null | grep -q ':8088 '; then
    log "Ignition Edge: active — http://$(hostname):8088"
    exit 0
  fi
  sleep 2
  ign_wait=$((ign_wait + 2))
done

warn "Ignition Edge started but not bound :8088 after 120s. Check: podman logs ignition-edge"
exit 1
