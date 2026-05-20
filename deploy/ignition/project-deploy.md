# FRAGUAV2 Project Deployment on Ignition Edge

## Key gotcha: Edge accepts ONLY a project named `Edge`

The Edge gateway's `EdgeProjectManager` rejects any project whose directory
name (and `project.json` "title") differs from the default project shipped
with the image — log line:

```
W [EdgeProjectManager] Invalid project for this platform edition: 'FRAGUAV2'.
```

Verified by testing: renaming the project directory to `TestProj` still
got rejected. **The name `Edge` is hard-coded** (or pinned to whatever the
shipped project was called).

This is independent of view count, Vision module presence, component types,
session-props content, etc. The rejection happens *before* module init
based on a name/identity check.

## How to deploy custom Perspective content

**Merge into the existing `Edge` project directory** instead of creating a
new project. Keep `Edge/project.json` and `Edge/ignition/global-props/`
untouched (or merge our `data.bin` if we need our project-level props).

Steps (executed on each Fragua edge during Phase 7c):

```bash
PROJ=/opt/embernet/ignition-edge/data/projects

# 1. Back up Edge's identity files
cp $PROJ/Edge/project.json $PROJ/Edge/project.json.bak

# 2. Overlay our Perspective views + global props
cp -r FRAGUAV2/com.inductiveautomation.perspective $PROJ/Edge/
cp FRAGUAV2/ignition/global-props/data.bin $PROJ/Edge/ignition/global-props/
cp FRAGUAV2/ignition/global-props/resource.json $PROJ/Edge/ignition/global-props/

# 3. Fix ownership (tar may preserve numeric UID from source)
chown -R root:root $PROJ/Edge

# 4. Restart Ignition Edge to pick up new resources
podman restart ignition-edge
```

## What we keep / what we lose

- ✅ All 10 Perspective views (Docks, Framework, Header, Page/*, fragua)
- ✅ Project-level global-props (data.bin)
- ✅ Page navigation (`/`, `/alarms`, `/charts` map to the right views)
- 🗑 `com.inductiveautomation.vision/client-tags/` was already stripped from
  the local repo (orphan tag definitions, ~700 bytes; no actual Vision
  windows existed)
- ⚠️ Project name displayed in Gateway UI shows as "Edge Project" (not
  FRAGUAV2). The views are all there; only the project label differs.

## Verification

Each edge should answer HTTP 200 on these endpoints (8088 = Edge gateway):
- `http://<edge>:8088/` — Ignition Gateway landing page (redirects to /Start)
- `http://<edge>:8088/data/perspective/client/Edge/` — homepage (fragua view)
- `http://<edge>:8088/data/perspective/client/Edge/charts` — Page/Charts
- `http://<edge>:8088/data/perspective/client/Edge/alarms` — Page/Alarms

## Re-deploy script

When the project changes, re-run `deploy/ignition/project-redeploy.sh`
which (a) tars the local `FRAGUAV2/` directory, (b) scps it to both edges,
(c) overlays into each edge's `data/projects/Edge/`, (d) restarts the
ignition-edge container.
