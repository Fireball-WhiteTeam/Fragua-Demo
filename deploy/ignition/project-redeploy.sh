#!/bin/bash
# Redeploy FRAGUAV2/ Perspective content into the Edge gateway's `Edge` project
# on both Fragua edges. Edge rejects custom project names — see project-deploy.md.
#
# Run from the Fragua-Demo repo root. Requires plink + pscp in PATH (PuTTY suite).

set -euo pipefail

HK_E01="SHA256:uOYI68mywjEC5Am5Gi/w1ehmHfb1u3LLAxnP4BuvQ7c"
HK_E02="SHA256:/I/4sBCGiXvn9CbEdPR6Ar5N2/bFKjtL13B5J85aDPQ"
PW="GreatBallzFire01"   # emberadmin password — pull from credential store if integrating with CI

TAR=/tmp/FRAGUAV2-edge-$(date +%s).tar.gz
tar -czf "$TAR" FRAGUAV2/

merge_cmd='set -e
PROJ=/opt/embernet/ignition-edge/data/projects
[ -f $PROJ/Edge/project.json.bak ] || cp $PROJ/Edge/project.json $PROJ/Edge/project.json.bak
mkdir -p /tmp/fv2-extract
rm -rf /tmp/fv2-extract/*
tar -xzf /tmp/fv2.tar.gz -C /tmp/fv2-extract
rm -rf $PROJ/Edge/com.inductiveautomation.perspective
cp -r /tmp/fv2-extract/FRAGUAV2/com.inductiveautomation.perspective $PROJ/Edge/
cp /tmp/fv2-extract/FRAGUAV2/ignition/global-props/data.bin $PROJ/Edge/ignition/global-props/
cp /tmp/fv2-extract/FRAGUAV2/ignition/global-props/resource.json $PROJ/Edge/ignition/global-props/
chown -R root:root $PROJ/Edge
rm -rf /tmp/fv2-extract /tmp/fv2.tar.gz
podman restart ignition-edge >/dev/null
echo "redeployed on $(hostname)"
'

deploy_one() {
  local IP=$1 HK=$2
  pscp -batch -pw "$PW" -hostkey "$HK" "$TAR" emberadmin@$IP:/tmp/fv2.tar.gz
  plink -ssh -batch -pw "$PW" -hostkey "$HK" emberadmin@$IP "echo '$merge_cmd' | sudo bash"
}

echo "=== edge-01 ==="
deploy_one 20.80.241.221 "$HK_E01"
echo "=== edge-02 ==="
deploy_one 52.176.39.25 "$HK_E02"

rm -f "$TAR"
echo "Done. Both edges restarted; allow ~60s for STARTING → RUNNING."
