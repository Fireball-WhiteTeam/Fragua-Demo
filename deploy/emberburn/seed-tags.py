#!/usr/bin/env python3
"""Push the Fragua tag list into a running EmberBurn.

The tag list lives in `fragua-tags.json` as data. This pushes it into the
application through its own API, where EmberBurn persists it to its data
volume. Nothing lands in the Helm chart or the container image, so changing a
tag is an edit here and a re-run, or a few clicks in the EmberBurn web UI.

Idempotent: re-running redefines the same tags in place rather than duplicating
them, so it is safe to run after every deploy.

Usage, from a host that can reach the pod (or through `kubectl port-forward`):

    python seed-tags.py --url http://localhost:5000
    python seed-tags.py --url http://localhost:5000 --file fragua-tags.json

On fragua-edge-01 the pod is host-networked, so from the node itself:

    python3 seed-tags.py --url http://127.0.0.1:5000

If EmberBurn was deployed with an API key (`security.apiKey`), pass it — the
mutating endpoints reject the request without it:

    python seed-tags.py --url http://127.0.0.1:5000 --api-key "$EMBERBURN_API_KEY"
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_tags(path):
    """Read the tag list, tolerating either a {"tags": [...]} document or a bare list."""
    with open(path, "r", encoding="utf-8") as f:
        document = json.load(f)

    tags = document["tags"] if isinstance(document, dict) else document

    unnamed = [t for t in tags if not t.get("name")]
    if unnamed:
        raise SystemExit(f"{len(unnamed)} tag(s) in {path} have no name")

    return tags


def post(url, payload, api_key=None, timeout=30):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if api_key:
        request.add_header("X-EmberBurn-Token", api_key)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit(f"{url} returned HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach {url}: {e.reason}")


def get(url, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:5000",
                        help="EmberBurn REST API base URL (default: %(default)s)")
    parser.add_argument("--file", default=None,
                        help="Tag list JSON (default: fragua-tags.json beside this script)")
    parser.add_argument("--api-key", default=None,
                        help="Value for the X-EmberBurn-Token header, if the deploy sets one")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be sent and exit")
    args = parser.parse_args()

    path = Path(args.file) if args.file else Path(__file__).with_name("fragua-tags.json")
    tags = load_tags(path)
    base = args.url.rstrip("/")

    print(f"{len(tags)} tag(s) from {path.name}")
    if args.dry_run:
        for tag in tags:
            model = tag.get("simulation_type") if tag.get("simulate") else "static"
            print(f"  {tag['name']:52s} {tag.get('type', 'float'):6s} {model}")
        return 0

    result = post(f"{base}/api/tags/bulk", {"tags": tags}, args.api_key)

    created = result.get("created", 0)
    errors = result.get("error_details") or []
    print(f"defined {created}/{len(tags)}")
    for error in errors:
        print(f"  FAILED {error.get('name', '?')}: {error.get('error')}", file=sys.stderr)

    # Confirm against the server rather than trusting the response: the point of
    # the exercise is that these tags are really in the running gateway.
    # /api/tags answers {"count": N, "tags": {...}} — the names are one level in.
    response = get(f"{base}/api/tags")
    live = (response or {}).get("tags", {})
    if live:
        missing = [t["name"] for t in tags if t["name"] not in live]
        print(f"{len(live)} tag(s) live in the gateway")
        if missing:
            print(f"  MISSING after seed: {', '.join(missing)}", file=sys.stderr)
            return 1

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
