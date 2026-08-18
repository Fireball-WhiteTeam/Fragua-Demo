#!/usr/bin/env python3
"""Enforce the app-template invariants against chart templates and manifests.

Every rule here corresponds to an outage, and every one of them is SILENT: the
workload comes up, reports healthy, and moves no traffic. That is precisely why
this runs in CI instead of living in a README — the README already said some of
this and the fleet drifted anyway.

Usage:
    check-app-invariants.py <path> [<path> ...]

Paths may be chart directories, manifest directories, or single files.
Exit 0 = clean, 1 = violations found, 2 = the checker refuses to answer.

Helm templating is handled by ignoring lines that are pure Go-template control
flow and by treating `{{ ... }}` values as unknown rather than as literals: the
goal is to catch hardcoded mistakes, not to render the chart. A value that comes
from `.Values` is the operator's business and is skipped with a note.

Scripts are scanned too (`.py`, `.sh`), because the Ziti services on this estate
are wired by scripts rather than manifests — a YAML-only checker had never once
looked at the two intercept literals it was written to catch.

Two behaviours worth knowing before you trust a green:

  * Exit 2 means UNTRUSTWORTHY, not clean. It fires when any named path matched
    zero files, so a stale argument in a multi-path sweep fails loudly instead
    of quietly reporting success over a tree nobody read.
  * `privileged: true` is waivable IN WRITING and every waiver is counted in the
    output (see WAIVER_RE). Some workloads genuinely need it; an exception that
    stops being visible becomes the new default.

The rules are covered both ways — must-fire and must-not-cry-wolf — by
test-check-app-invariants.py, which lives beside this file and runs in CI. Run
it after any edit here; three of this checker's first four findings across the
fleet were false positives, and a check that cries wolf gets switched off.
"""
import os
import re
import sys

# The router's DNS intercept pool. Anything static in here collides with what
# the tunneler allocates dynamically. Keep in step with
# fluxrouter.DefaultDNSInterceptCIDR and the_reconciler config.flux_intercept_pool.
#
# The `(?!/\d)` tail matters: an address carrying a prefix length is a RANGE
# DECLARATION, not an allocated address. `--dnsSvcIpRange 100.65.0.0/16` IS the
# pool; it is not an intercept inside the pool. Without this, flux-edge-tunnel
# could never document its own CLI argument, and a rule that punishes accurate
# documentation gets the documentation deleted.
#
# Known narrowing, stated rather than hidden: an intercept written as
# `100.65.1.1/32` is now missed. That is a deliberate trade against three false
# positives on the chart most likely to be read.
DNS_POOL_RE = re.compile(r"\b100\.65\.\d{1,3}\.\d{1,3}\b(?!/\d)")

# WireGuard node ranges: a real machine lives at these, and intercepting one
# shadows it on every dialer.
NODE_RANGE_RE = re.compile(r"\b100\.64\.[012]\.\d{1,3}\b(?!/\d)")

TEMPLATED = re.compile(r"\{\{.*?\}\}")

# Extensions worth reading. `.py` and `.sh` are here because the Ziti services
# on this estate are wired by SCRIPTS, not manifests — fragua-edge-01-designer
# and fragua-edge-02-designer carry their intercept literals in
# Fragua-Demo/deploy/flux/wire-fragua-designer-ziti-services.py. A checker that
# reads only YAML has never looked at the two offenders it was written for. The
# line-scan rules below work unchanged on script source; nothing else does.
SCAN_EXTS = (".yaml", ".yml", ".tpl", ".py", ".sh")

# A values file is an INPUT to a chart, not an object spec. Whether the pod ends
# up with the right dnsPolicy is decided by the TEMPLATE, and the good charts
# DERIVE it — node-exporter, alert-manager and postgresql all emit
#
#     {{- if .Values.network.hostNetwork }}
#     hostNetwork: true
#     dnsPolicy: ClusterFirstWithHostNet
#
# and expose no dnsPolicy key at all. That is the STRONGEST form of the
# invariant: an input that cannot be set wrong beats an input plus a guard. So
# "no dnsPolicy key here" does NOT mean "no dnsPolicy on the pod", and the
# ABSENCE rule is undecidable on a values file. Skip rather than guess — the
# same call already made for the RWO/Recreate rule below.
#
# A values file that NAMES a wrong policy is still decidable and still fails.
# That is flux-l2-bridge (dnsPolicy: Default with hostNetwork: true) and it must
# keep failing, so only the absence case is skipped.
VALUES_FILE_RE = re.compile(
    r"(?:^|[\\/])values[^\\/]*\.ya?ml$|(?:^|[\\/])examples[\\/]", re.I)

# An explicit, reasoned waiver for `privileged: true`.
#
# Some workloads genuinely need it and a capability list cannot replace it: the
# flux tunnelers do tproxy, install nft rules, open raw sockets and want their
# own tun device, and CAP_* grants no /dev access. Pretending otherwise would
# mean either a permanently red check or a chart that lies.
#
# So the rule is not switched off — it is waivable, and only in writing. The
# marker must carry a reason after the em-dash, and every waiver is COUNTED in
# the summary, so exceptions stay greppable instead of dissolving into silence.
#
#     securityContext:
#       # app-invariants: allow-privileged — tunneler does tproxy + nft + raw
#       # sockets and creates its own tun device.
#       privileged: true
WAIVER_RE = re.compile(
    r"#\s*app-invariants:\s*allow-privileged\s*[-—:]\s*\S", re.I)

# A file declaring itself as fixture data. Test suites for these very rules must
# contain known-bad addresses on purpose — this checker's own regression suite
# does, and so does the_reconciler/tests/test_intercept_guard.py, which lists
# the designer literals precisely to assert that the guard rejects them. Without
# an opt-out, every such test file is a permanent violation and the only way to
# get a green is to delete the tests.
#
# Deliberately a written marker rather than a path heuristic like "tests/": the
# declaration is visible in the file it applies to, and it is greppable.
FIXTURES_RE = re.compile(r"#\s*app-invariants:\s*fixtures\b", re.I)

# Keys whose VALUES are certificate names, not addressing declarations.
#
# A tls-san list is the set of names a serving cert must cover, and during an
# address migration it legitimately holds BOTH the old and new addresses — that
# is the entire point of it. templates/k3s/k3s-config.server.fragua.yaml lists
# fragua-k3s-api.flux.internal, its current 100.64.65.2, its retired 100.65.1.1
# and two node addresses, so that agents dialing any of them still get valid
# TLS. Reading those as intercepts produced three findings on a file that is
# correct, and the only way to "fix" it would be to break every agent's TLS.
#
# The node-range rule was already hedged behind `"intercept" in text`, but that
# matched the WORD in a prose comment, which is how this fired at all.
SAN_KEYS = {
    "tls-san", "tls-sans", "cert-san", "cert-sans", "certsans",
    "apiservercertsans", "subjectaltnames", "dnsnames", "ipaddresses",
}

KEY_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+):\s*(?:#.*)?$|^\s*([A-Za-z0-9_.-]+):\s+\S")


class Finding:
    def __init__(self, path, line, rule, detail):
        self.path, self.line, self.rule, self.detail = path, line, rule, detail

    def __str__(self):
        loc = "%s:%d" % (self.path, self.line) if self.line else self.path
        return "  [%s] %s\n      %s" % (self.rule, loc, self.detail)


def scan_files(root):
    """Yield every file worth checking under `root`.

    The `charts` prune is deliberately NARROW. It exists to skip Helm's vendored
    dependency directory — the `charts/` that sits INSIDE a chart, next to its
    Chart.yaml, holding somebody else's subcharts. Pruning every directory that
    happens to be named `charts` also skipped the top-level `charts/` that most
    of our app repos keep their own charts in, and the effect was a confident,
    permanent green over code nobody had opened. Measured before the fix:

        check-app-invariants.py Codesys-AMD-64-x86-live         -> 0 violations
        check-app-invariants.py .../charts/codesys-pod          -> 1 violation

    The scanned==0 guard in main() did not catch it either, because six
    unrelated docs at the repo root were scanned. That is the same class of bug
    as the guard itself was written to prevent, one level up.
    """
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        vendored = os.path.isfile(os.path.join(dirpath, "Chart.yaml"))
        dirnames[:] = [
            d for d in dirnames
            if d not in (".git", "node_modules", "archive")
            and not (d == "charts" and vendored)
        ]
        for fn in filenames:
            if fn.endswith(SCAN_EXTS):
                yield os.path.join(dirpath, fn)


def check_file(path):
    """Return (findings, waivers). A waiver is a privileged block excused in
    writing — counted and reported, never silently dropped."""
    out = []
    waived = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        return [Finding(path, 0, "unreadable", str(e))], [], False

    # A file that declares itself fixture data is skipped whole. Its bad values
    # are the assertions. It still counts as scanned, so the zero-files guard
    # cannot be defeated by marking everything.
    if FIXTURES_RE.search("\n".join(lines[:40])):
        return [], [], True

    text = "\n".join(lines)
    host_network = re.search(r"^\s*hostNetwork:\s*true\b", text, re.M)
    dns_policy = re.search(r"^\s*dnsPolicy:\s*(\S+)", text, re.M)
    pol = dns_policy.group(1).strip() if dns_policy else None
    pol_known = pol is not None and not TEMPLATED.search(pol)

    # --- Type 3: hostNetwork demands ClusterFirstWithHostNet -----------------
    if host_network:
        ln = text[:host_network.start()].count("\n") + 1
        if pol is None and not VALUES_FILE_RE.search(path):
            out.append(Finding(
                path, ln, "hostnet-dns-missing",
                "hostNetwork: true with no dnsPolicy. The pod inherits the "
                "NODE's resolv.conf and cannot resolve *.svc.cluster.local. "
                "Set dnsPolicy: ClusterFirstWithHostNet."))
        elif pol_known and pol != "ClusterFirstWithHostNet":
            extra = ""
            if pol == "ClusterFirst":
                extra = (" ClusterFirst is SILENTLY IGNORED when hostNetwork is "
                         "true — it reads correct and does nothing.")
            out.append(Finding(
                path, ln, "hostnet-dns-wrong",
                "hostNetwork: true with dnsPolicy: %s. Cluster service names "
                "will not resolve, which breaks any Ziti host.v1 targeting a "
                "service FQDN — invisibly, because the terminator still "
                "registers and TCP still connects.%s" % (pol, extra)))

    # --- Type 3: privileged instead of explicit caps -------------------------
    #
    # Kubernetes spells these WITHOUT the CAP_ prefix in capabilities.add, so a
    # test for the literal "CAP_NET_BIND_SERVICE" could never be satisfied by a
    # CORRECT manifest — only by prose that happened to contain the magic
    # string. Matching the bare name accepts the manifest spelling and the CAP_
    # form used in prose, so a chart can clear this rule by BEING right rather
    # than by adding a comment.
    m = re.search(r"^\s*privileged:\s*true\b", text, re.M)
    if m and "NET_BIND_SERVICE" not in text:
        ln = text[:m.start()].count("\n") + 1
        # The waiver has to be attached to the thing it waives. Scanning the
        # whole file would let one reasoned exception silence every other
        # privileged block in a values.yaml that has several.
        window = "\n".join(lines[max(0, ln - 7):ln])
        if WAIVER_RE.search(window):
            waived.append((path, ln))
        else:
            out.append(Finding(
                path, ln, "privileged-no-caps",
                "privileged: true and no explicit capability list. Grant "
                "CAP_NET_ADMIN, CAP_NET_RAW and CAP_NET_BIND_SERVICE instead — "
                "CAP_NET_BIND_SERVICE is the one that gets forgotten, and without "
                "it the tunneler cannot bind its resolver on :53 and comes up with "
                "working interception and no name resolution. If this workload "
                "genuinely needs privileged (the flux tunnelers do — they create "
                "their own tun device, which no capability grants), waive it in "
                "writing directly above the key:\n"
                "        # app-invariants: allow-privileged — <reason>"))

    # --- Type 4: RWO PVC demands Recreate ------------------------------------
    #
    # PER DOCUMENT, AND ONLY IN REAL MANIFESTS. The first version of this rule
    # matched ReadWriteOnce anywhere in a file against RollingUpdate anywhere in
    # the same file, and immediately produced a false positive on
    # charts/fireball-site/values.yaml — which has ReadWriteMany for the main
    # workload, a ReadWriteOnce belonging to a NESTED component, and a top-level
    # strategy that has nothing to do with it.
    #
    # A values.yaml has no document structure tying a volume to a strategy, so
    # the rule cannot be decided there and is skipped rather than guessed. It is
    # decidable on a rendered manifest, where both live in one object. A check
    # that cries wolf gets switched off, which is worse than no check.
    for doc in re.split(r"^---\s*$", text, flags=re.M):
        if not re.search(r"^\s*kind:\s*(Deployment|StatefulSet)\b", doc, re.M):
            continue
        if "ReadWriteOnce" not in doc:
            continue
        strat = re.search(r"^\s*type:\s*RollingUpdate\b", doc, re.M)
        if strat:
            ln = text[:text.index(doc) + strat.start()].count("\n") + 1
            out.append(Finding(
                path, ln, "rwo-rollingupdate",
                "ReadWriteOnce PVC with strategy RollingUpdate in the same "
                "object. The surge pod can never attach the volume; the upgrade "
                "deadlocks on Multi-Attach and the release lands failed while "
                "the spec is fine. Use strategy: Recreate."))

    # --- Types 1/2: intercept addressing -------------------------------------
    # Python docstrings are prose, and prose is already exempt — whole-line `#`
    # comments are skipped just below, and a docstring is the same thing with
    # different punctuation. The rule is that this checker reads DECLARATIONS,
    # not narrative.
    #
    # It matters here because the good scripts explain the incidents that
    # produced these rules, and the explanation has to name the address to mean
    # anything: wire-k3s-api-services.py says "fragua-k3s-api was pinned at
    # 100.65.1.1, and once it gained a .flux.internal name the router handed it
    # 100.65.0.2". Flagging that is telling the author to delete the only record
    # of why the rule exists. An intercept in CODE is still caught — see the
    # must-fail fixtures in test-check-app-invariants.py.
    in_docstring = None
    is_py = path.endswith(".py")

    current_key = ""
    for i, line in enumerate(lines, 1):
        if is_py:
            rest = line
            while True:
                if in_docstring:
                    end = rest.find(in_docstring)
                    if end < 0:
                        rest = ""
                        break
                    rest = rest[end + 3:]
                    in_docstring = None
                    continue
                nxt = min((p for p in (rest.find('"""'), rest.find("'''")) if p >= 0),
                          default=-1)
                if nxt < 0:
                    break
                in_docstring = rest[nxt:nxt + 3]
                rest = rest[nxt + 3:]
            # `rest` is whatever on this line was OUTSIDE a docstring.
            if not rest.strip():
                continue
            line = rest
        if line.lstrip().startswith("#"):
            continue
        # Track the key a list item belongs to, so a certificate SAN list is not
        # read as an addressing declaration. Only mapping keys reset this; list
        # items ("- 100.64.2.2") inherit the key above them, which is exactly
        # the shape a tls-san block has.
        km = KEY_RE.match(line)
        if km:
            current_key = (km.group(1) or km.group(2) or "").lower()
        if current_key in SAN_KEYS:
            continue
        # Whole-line comments are skipped just above; strip INLINE comments here
        # for the same reason. Documenting the ranges you must avoid is not the
        # same as intercepting one — flux-edge-tunnel's values.yaml annotates
        # both 100.64.0.0/24 and 100.65.0.0/16 in a trailing comment, and
        # flagging that meant the chart could not describe its own arguments.
        stripped = TEMPLATED.sub("", re.sub(r"\s+#.*$", "", line))
        for m in DNS_POOL_RE.finditer(stripped):
            out.append(Finding(
                path, i, "intercept-in-dns-pool",
                "%s is inside the router's DNS intercept pool 100.65.0.0/16. "
                "The tunneler allocates from that range, so this address can be "
                "handed to another service and the path goes silently dead. Use "
                "a <svc>.flux.internal name and let the router allocate."
                % m.group(0)))
        for m in NODE_RANGE_RE.finditer(stripped):
            if "intercept" not in text.lower():
                continue
            out.append(Finding(
                path, i, "intercept-shadows-node",
                "%s lies in a node address range. Intercepting a real machine's "
                "address installs it on lo on every dialer and severs the direct "
                "path to that machine." % m.group(0)))

    return out, waived, False


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2

    findings = []
    waivers = []
    scanned = 0
    fixtures = 0
    per_root = []
    for root in argv[1:]:
        if not os.path.exists(root):
            print("no such path: %s" % root, file=sys.stderr)
            return 2
        n = 0
        for f in scan_files(root):
            n += 1
            f_out, f_waived, f_fixture = check_file(f)
            findings.extend(f_out)
            waivers.extend(f_waived)
            fixtures += 1 if f_fixture else 0
        per_root.append((root, n))
        scanned += n

    # A check that silently scans nothing is worse than no check — it reports
    # success forever. Same failure the dashboard's inline-JS gate hit.
    #
    # PER ROOT, not just in total. A sweep like `check ... cluster charts
    # templates` where ONE argument is stale still scans plenty of files, so a
    # total-only guard passes while a whole tree goes unread — which is exactly
    # how the `charts` prune stayed invisible. Name the empty root and fail.
    empty = [r for r, n in per_root if n == 0]
    if empty:
        print("app-invariants: these paths matched 0 files — wrong path? "
              "refusing to pass: %s" % ", ".join(empty), file=sys.stderr)
        return 2
    if scanned == 0:
        print("app-invariants: scanned 0 files — wrong path? refusing to pass.",
              file=sys.stderr)
        return 2

    # Waivers are REPORTED, always, pass or fail. An exception that stops being
    # visible stops being an exception and becomes the new default — which is
    # how `privileged: true` spread across the fleet in the first place.
    def report_waivers():
        if not waivers:
            return
        print("\napp-invariants: %d privileged waiver(s) in force:" % len(waivers))
        for p, ln in waivers:
            print("  %s:%d" % (p, ln))

    # Fixture files are declared, not inferred, so say how many were skipped.
    # A silent skip is how a whole tree stops being checked without anyone
    # noticing — the same shape as the `charts` prune bug.
    tally = "%d file(s) scanned" % scanned
    if fixtures:
        tally += " (%d declared fixtures, skipped)" % fixtures

    if not findings:
        print("app-invariants: %s; 0 violations" % tally)
        report_waivers()
        return 0

    print("app-invariants: %s; %d violation(s)\n" % (tally, len(findings)))
    for f in findings:
        print(f)
    report_waivers()
    print("\nSee templates/app/README.md for why each of these exists.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
