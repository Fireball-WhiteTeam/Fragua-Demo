#!/bin/bash
# CODESYS Control SL v4.20.0.0 install for Fragua edges.
# Mirrors install_codesys() from Fireball-Red-Team/deployment:deploy-ut3-cp02.sh.
# Run as root.

set -e

log()  { echo "[$(date -Is)] $*"; }
warn() { echo "[$(date -Is)] WARN: $*" >&2; }

CODESYS_URL="https://github.com/Embernet-ai/codesys-linux-x86/releases/download/v4.20.0.0/CODESYS.Control.for.Linux.SL.4.20.0.0.package"
BUILD_DIR="/tmp/codesys-build"
IMAGE_TAG="localhost/embernet/codesys-sl:4.20.0.0-hardened"
DATA_DIR="/opt/embernet/codesys/data"

# Gate: skip if already running
if podman container exists codesys 2>/dev/null; then
  cd_state=$(podman inspect codesys --format '{{.State.Status}}' 2>/dev/null || echo "unknown")
  if [[ "${cd_state}" == "running" ]]; then
    log "Codesys already running on $(hostname) — skipping"
    exit 0
  else
    log "Codesys container exists but ${cd_state} — recreating"
    podman rm -f codesys >/dev/null 2>&1 || true
  fi
fi

# Tear down legacy unit
if [[ -f /etc/systemd/system/container-codesys.service ]]; then
  log "Removing legacy container-codesys.service"
  systemctl disable --now container-codesys.service 2>/dev/null || true
  rm -f /etc/systemd/system/container-codesys.service
fi

# Build image if not present
if ! podman image exists "${IMAGE_TAG}" 2>/dev/null; then
  log "Building Codesys container image on $(hostname)..."
  apt-get install -y unzip file wget 2>/dev/null
  mkdir -p "${BUILD_DIR}"

  if ! wget -q --show-progress -O "${BUILD_DIR}/codesys.pkg" "${CODESYS_URL}"; then
    warn "Codesys download failed from ${CODESYS_URL}"
    rm -rf "${BUILD_DIR}"
    exit 1
  fi

  pkg_size=$(stat -c%s "${BUILD_DIR}/codesys.pkg" 2>/dev/null || echo 0)
  if [[ ${pkg_size} -lt 1024 ]]; then
    warn "Codesys download truncated (${pkg_size} bytes)"
    rm -rf "${BUILD_DIR}"
    exit 1
  fi
  log "Downloaded codesys.pkg: ${pkg_size} bytes"

  # equivs control file for the codemeter-lite shim — written to BUILD_DIR and COPY'd in
  cat > "${BUILD_DIR}/codemeter-lite.ctrl" <<'CTRL'
Section: misc
Priority: optional
Standards-Version: 3.9.2
Package: codemeter-lite
Version: 99.0-fragua-demo-shim
Maintainer: Fragua Demo <noreply@embernet.ai>
Architecture: amd64
Description: Empty shim satisfying codesyscontrol's codemeter|codemeter-lite dep in demo mode.
 No CodeMeter daemon is provided. CODESYS Linux SL falls back to 30-min demo cycles.
CTRL

  cat > "${BUILD_DIR}/Containerfile" <<'DOCKERFILE'
FROM docker.io/library/debian:bookworm-slim
RUN apt-get update && apt-get install -y file unzip procps libc6 equivs && rm -rf /var/lib/apt/lists/*
COPY codemeter-lite.ctrl /tmp/codemeter-lite.ctrl
COPY codesys.pkg /tmp/codesys.pkg

# Build empty codemeter-lite shim so dpkg's `Depends: codemeter | codemeter-lite` is satisfied.
# Demo mode does NOT require the actual CodeMeter daemon.
RUN cd /tmp && equivs-build codemeter-lite.ctrl && \
    dpkg -i codemeter-lite_99.0-fragua-demo-shim_amd64.deb && \
    rm -f codemeter-lite.ctrl codemeter-lite_*

# CODESYS .package zip layout: .deb lives at Delivery/linux/codesyscontrol_*_amd64.deb
RUN set -e; FILETYPE=$(file -b /tmp/codesys.pkg); \
    if echo "${FILETYPE}" | grep -qi 'zip'; then \
      unzip -q /tmp/codesys.pkg -d /tmp/codesys && \
      DEB=$(find /tmp/codesys -name 'codesyscontrol_*_amd64.deb' -print -quit) && \
      echo "Installing $DEB" && \
      dpkg -i "$DEB" && \
      apt-get update && apt-get -f install -y; \
    elif echo "${FILETYPE}" | grep -qi 'debian'; then \
      dpkg -i /tmp/codesys.pkg && apt-get update && apt-get -f install -y; \
    else \
      chmod +x /tmp/codesys.pkg && bash /tmp/codesys.pkg; \
    fi && rm -rf /tmp/codesys*

# Sanity check: CODESYS .deb installs binaries under /opt/codesys (Wibu convention)
RUN ls -la /opt/codesys/ 2>/dev/null | head -20 && \
    test -x /opt/codesys/bin/codesyscontrol.bin || \
    (echo "BINARY MISSING — searching..." && find / -name 'codesyscontrol*' -type f 2>/dev/null | head -20 && exit 1)

# Entrypoint: launch the runtime. No license → demo mode (30-min cycles).
# Path + workdir verbatim from /etc/init.d/codesyscontrol:17,20,65 of
# the shipped .deb:
#   EXEC=/opt/codesys/bin/codesyscontrol.bin
#   WORKDIR=/var/opt/codesys
#   CONFIGFILE=/etc/codesyscontrol/CODESYSControl.cfg
#   cd $WORKDIR && ( $DAEMON $DAEMON_ARGS $EXEC $CONFIGFILE $ARGS ... )
# The prior entrypoint pointed at /etc/CODESYSControl.cfg (wrong path,
# never existed) AND had an `exec sleep infinity` fallback — that's
# exactly how cp02 ran "Up" with no PLC runtime for 9 days. Removed
# the fallback so missing binary = crash-loop = visible Failed status.
WORKDIR /var/opt/codesys
ENTRYPOINT ["/opt/codesys/bin/codesyscontrol.bin", "/etc/codesyscontrol/CODESYSControl.cfg"]
DOCKERFILE

  if ! podman build -t "${IMAGE_TAG}" "${BUILD_DIR}"; then
    warn "Codesys image build failed"
    rm -rf "${BUILD_DIR}"
    exit 1
  fi
  rm -rf "${BUILD_DIR}"
fi

mkdir -p "${DATA_DIR}"

log "Starting codesys container"
podman run -d \
  --name codesys \
  --restart=always \
  --network=host \
  --privileged \
  -v "${DATA_DIR}:/var/opt/codesys" \
  "${IMAGE_TAG}"

podman generate systemd --name codesys --new --restart-policy=always \
  > /etc/systemd/system/container-codesys.service 2>/dev/null || true
systemctl daemon-reload
systemctl enable container-codesys.service 2>/dev/null || true

log "Codesys: $(podman ps --filter name=codesys --format '{{.Status}}')"
