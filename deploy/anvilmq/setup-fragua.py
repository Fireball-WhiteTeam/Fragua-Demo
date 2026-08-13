#!/usr/bin/env python3
"""Wire Fragua's Sparkplug data into AnvilMQ: decode it, then archive it.

Two objects, both created through AnvilMQ's GraphQL API and both idempotent:

1. A **SparkplugB decoder**. Without it, all 25 of Fragua's metrics arrive as a
   single protobuf payload on one topic, so the dashboard's tag tree shows one
   entry and `broker-tag-count` reads 1. The decoder expands each metric into
   its own topic, rebuilding the UNS hierarchy the tag names carry.

   The destination topic MUST start with the tenant slug. The dashboard derives
   the tenant from the second segment for `spBv1.0/` topics and the FIRST
   segment for everything else (internal/broker/broker.go). Decoded topics are
   no longer under `spBv1.0/`, so a prefix that is not `fragua` makes every tag
   invisible to Fragua users while looking fine to a super admin.

2. An **archive group**, which is what puts the tags in the database: current
   value and history into Postgres (`anvilmq-postgres`), so the data outlives
   the broker's memory and can be queried.

Run it from anywhere that can reach the broker API — e.g. on the EmberNet
control plane:

    kubectl -n fireball-system port-forward svc/anvilmq 4000:4000
    python setup-fragua.py --url http://127.0.0.1:4000/graphql

If the broker has user management enabled, pass credentials; the script logs in
and uses the returned token:

    python setup-fragua.py --username admin --password "$BROKER_PASS"

`--dry-run` prints what would be created and changes nothing.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

TENANT = "fragua"
DECODER_NAME = "fragua-sparkplug"

# NO HYPHEN. AnvilMQ derives Postgres table names from the archive group name
# and does not quote them, so `fragua-tags` becomes the identifier
# `fragua-tagsLastval` and every table creation dies with:
#
#   Error in creating table [fragua-tagsLastval]: ERROR: syntax error at or near "-"
#
# The group still reports enabled=true, deployed=false, and archives nothing —
# so from the dashboard it looks exactly like the decoder not producing data.
ARCHIVE_GROUP = "fragua_tags"

# Decoded topics land at `fragua/<edge node>/<device>/<metric path>`, e.g.
# fragua/fragua-edge-01/fragua-hq-guadalajara/Refrigeration/ColdRoom_LT_01/AirTemperature
DESTINATION_TOPIC = f"{TENANT}/$nodeId/$deviceId"


class Broker:
    def __init__(self, url, token=None):
        self.url = url
        self.token = token

    def query(self, document, variables=None, timeout=30):
        payload = {"query": document, "variables": variables or {}}
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{self.url} returned HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")
        except urllib.error.URLError as e:
            raise SystemExit(f"Cannot reach {self.url}: {e.reason}")

        if body.get("errors"):
            raise SystemExit("GraphQL error: " + json.dumps(body["errors"], indent=2))
        return body["data"]

    def login(self, username, password):
        data = self.query(
            "mutation($u:String!,$p:String!){ login(username:$u, password:$p){ success token message } }",
            {"u": username, "p": password},
        )["login"]
        if not data.get("success"):
            raise SystemExit(f"Broker login failed: {data.get('message')}")
        self.token = data["token"]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:4000/graphql",
                        help="AnvilMQ GraphQL endpoint (default: %(default)s)")
    parser.add_argument("--username", default=None, help="Broker username, if user management is on")
    parser.add_argument("--password", default=None, help="Broker password")
    parser.add_argument("--archive-retention", default="90d", help="History retention (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    broker = Broker(args.url)

    config = broker.query("{ brokerConfig { version userManagementEnabled } }")["brokerConfig"]
    print(f"broker {config['version']}, userManagement={config['userManagementEnabled']}")

    if args.username:
        broker.login(args.username, args.password or "")
        print(f"authenticated as {args.username}")
    elif config["userManagementEnabled"]:
        # Queries stay open even with user management on; the mutations below
        # are the part that needs a token. Say so now rather than after a
        # confusing permission error three calls later.
        print("WARNING: user management is enabled and no credentials were given — "
              "mutations may be rejected", file=sys.stderr)

    # A decoder is assigned to a cluster node, so find out which one we are.
    node_id = broker.query("{ broker { nodeId isCurrent } }")["broker"]["nodeId"]
    print(f"cluster node {node_id}")

    existing = {d["name"] for d in broker.query(
        "{ sparkplugBDecoders { name enabled } }")["sparkplugBDecoders"]}

    decoder_input = {
        "name": DECODER_NAME,
        "namespace": TENANT,          # the Sparkplug Group ID to subscribe to
        "nodeId": node_id,
        "enabled": True,
        "config": {
            "sourceNamespace": "spBv1.0",
            "rules": [{
                "name": "fragua-edges",
                "nodeIdRegex": "^fragua-edge-.*$",
                "deviceIdRegex": ".*",
                "destinationTopic": DESTINATION_TOPIC,
            }],
        },
    }

    archive_input = {
        "name": ARCHIVE_GROUP,
        "topicFilter": [f"{TENANT}/#"],
        "retainedOnly": False,
        "lastValType": "POSTGRES",
        "archiveType": "POSTGRES",
        "payloadFormat": "DEFAULT",
        "lastValRetention": "30d",
        "archiveRetention": args.archive_retention,
        "purgeInterval": "1h",
    }

    if args.dry_run:
        print("\n--- decoder ---")
        print(json.dumps(decoder_input, indent=2))
        print("\n--- archive group ---")
        print(json.dumps(archive_input, indent=2))
        return 0

    # ── decoder ──────────────────────────────────────────────────────────
    if DECODER_NAME in existing:
        result = broker.query(
            """mutation($n:String!,$i:SparkplugBDecoderInput!){
                 sparkplugBDecoder { update(name:$n, input:$i){ success errors decoder { name enabled } } } }""",
            {"n": DECODER_NAME, "i": decoder_input},
        )["sparkplugBDecoder"]["update"]
        action = "updated"
    else:
        result = broker.query(
            """mutation($i:SparkplugBDecoderInput!){
                 sparkplugBDecoder { create(input:$i){ success errors decoder { name enabled } } } }""",
            {"i": decoder_input},
        )["sparkplugBDecoder"]["create"]
        action = "created"

    if not result.get("success"):
        print(f"decoder FAILED: {result.get('errors')}", file=sys.stderr)
        return 1
    print(f"decoder {action}: {DECODER_NAME} -> {DESTINATION_TOPIC}/<metric>")

    # ── archive group ────────────────────────────────────────────────────
    groups = {g["name"] for g in broker.query(
        "{ archiveGroups { name enabled } }")["archiveGroups"]}

    if ARCHIVE_GROUP in groups:
        print(f"archive group {ARCHIVE_GROUP} already exists")
    else:
        result = broker.query(
            """mutation($i:CreateArchiveGroupInput!){
                 archiveGroup { create(input:$i){ success message archiveGroup { name enabled deployed } } } }""",
            {"i": archive_input},
        )["archiveGroup"]["create"]
        if not result.get("success"):
            print(f"archive group FAILED: {result.get('message')}", file=sys.stderr)
            return 1
        print(f"archive group created: {ARCHIVE_GROUP} <- {TENANT}/# into Postgres")

    # `CreateArchiveGroupInput` has no `enabled` field, so a freshly created
    # group lands enabled=false / deployed=false and archives nothing. Nothing
    # errors — currentValues simply stays empty, which reads exactly like the
    # decoder not working.
    state = next((g for g in broker.query(
        "{ archiveGroups { name enabled deployed } }")["archiveGroups"]
        if g["name"] == ARCHIVE_GROUP), None)
    if state and not state.get("enabled"):
        result = broker.query(
            'mutation($n:String!){ archiveGroup { enable(name:$n){ success message archiveGroup { name enabled deployed } } } }',
            {"n": ARCHIVE_GROUP}, timeout=120,
        )["archiveGroup"]["enable"]
        if not result.get("success"):
            print(f"archive group enable FAILED: {result.get('message')}", file=sys.stderr)
            return 1
        print(f"archive group enabled: {result.get('archiveGroup')}")
    else:
        print(f"archive group already enabled: {state}")

    # ── confirm ──────────────────────────────────────────────────────────
    topics = broker.query(
        'query($f:String!){ currentValues(topicFilter:$f, limit:500){ topic } }',
        {"f": f"{TENANT}/#"},
    )["currentValues"]
    print(f"\n{len(topics)} decoded topic(s) under {TENANT}/ right now")
    for t in sorted(x["topic"] for x in topics)[:10]:
        print(f"  {t}")
    if not topics:
        print("  (none yet — expected until EmberBurn publishes its first DDATA)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
