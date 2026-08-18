#!/usr/bin/env python3
# app-invariants: fixtures
#
# ^ Required. Every known-bad address, dnsPolicy and strategy below is an
# ASSERTION, and without this marker the checker flags its own test suite and the
# only way to reach a green is to delete the tests.
"""Regression suite for check-app-invariants.py — the gate on the gate.

A checker nobody tests is a checker that silently stops working, and this one
already did: it shipped unable to read `.py` files (where every hand-wired Ziti
intercept on the estate lives) and pruning every directory named `charts` (where
most app repos keep their charts), so pointing it at a repo root scanned a few
stray docs and printed a confident green. Both bugs were invisible precisely
because the tool's output looked healthy. Measured before the fix:

    check-app-invariants.py Codesys-AMD-64-x86-live   -> 0 violations
    check-app-invariants.py .../charts/codesys-pod    -> 1 violation

So every rule here gets BOTH directions:

  * a MUST-FAIL fixture, proving the rule still fires, and
  * a MUST-PASS fixture, proving it does not cry wolf.

The must-pass half is not padding. Three of the checker's four original findings
across the fleet were false positives against charts that were doing the RIGHT
thing — deriving dnsPolicy in the template instead of exposing a settable key —
and a check that cries wolf gets switched off, which is worse than no check.

Usage:
    python3 test-check-app-invariants.py
    python3 test-check-app-invariants.py -v      # show each case

Exit 0 = the checker behaves. Non-zero = the checker regressed; read the diff
before touching any chart, because every downstream "passes" depends on it.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# Overridable so the suite can be pointed at another copy of the checker — used
# to prove the suite has teeth by running it against the PRE-FIX checker, which
# must fail it. A regression suite that has never been seen to fail is not
# evidence of anything.
CHECKER = os.environ.get(
    "APP_INVARIANTS_CHECKER", os.path.join(HERE, "check-app-invariants.py"))

FAIL = "fail"      # exit 1 — a violation was found
PASS = "pass"      # exit 0 — clean
UNTRUSTWORTHY = 2  # exit 2 — the checker refuses to answer

VERBOSE = "-v" in sys.argv[1:]


# --- fixtures ---------------------------------------------------------------
#
# Each case is (name, {filename: content}, expected, must_mention).
# `must_mention` guards against a fixture failing for the WRONG reason — a case
# that fails on an unrelated rule would otherwise look like a pass.

CASES = [
    # ---- Type 3: hostNetwork + dnsPolicy --------------------------------
    ("hostnet with dnsPolicy Default fails",
     {"ds.yaml": """
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: canary
spec:
  template:
    spec:
      hostNetwork: true
      dnsPolicy: Default
"""}, FAIL, "hostnet-dns-wrong"),

    ("hostnet with ClusterFirst fails, and says it is silently ignored",
     {"ds.yaml": """
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: canary
spec:
  template:
    spec:
      hostNetwork: true
      dnsPolicy: ClusterFirst
"""}, FAIL, "SILENTLY IGNORED"),

    ("hostnet with ClusterFirstWithHostNet passes",
     {"ds.yaml": """
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: canary
spec:
  template:
    spec:
      hostNetwork: true
      dnsPolicy: ClusterFirstWithHostNet
"""}, PASS, None),

    ("hostnet with NO dnsPolicy fails in a real manifest",
     {"ds.yaml": """
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: canary
spec:
  template:
    spec:
      hostNetwork: true
"""}, FAIL, "hostnet-dns-missing"),

    # The strong form: node-exporter, alert-manager and postgresql all derive
    # dnsPolicy from hostNetwork in the TEMPLATE and expose no key, so there is
    # nothing to set wrong. The checker used to flag exactly this.
    ("strong-form values file (hostNetwork, no dnsPolicy key) passes",
     {"values.yaml": """
network:
  hostNetwork: true
"""}, PASS, None),

    ("values file naming a WRONG policy still fails",
     {"values.yaml": """
hostNetwork: true
dnsPolicy: Default
"""}, FAIL, "hostnet-dns-wrong"),

    # ---- Type 3: privileged --------------------------------------------
    ("privileged with no caps and no waiver fails",
     {"values.yaml": """
securityContext:
  privileged: true
"""}, FAIL, "privileged-no-caps"),

    ("privileged cleared by the k8s capability spelling",
     {"values.yaml": """
securityContext:
  privileged: true
  capabilities:
    add:
      - NET_ADMIN
      - NET_RAW
      - NET_BIND_SERVICE
"""}, PASS, None),

    ("privileged cleared by a written waiver",
     {"values.yaml": """
securityContext:
  # app-invariants: allow-privileged — tunneler creates its own tun device,
  # which no capability list grants.
  privileged: true
"""}, PASS, None),

    ("a waiver with no reason does NOT count",
     {"values.yaml": """
securityContext:
  # app-invariants: allow-privileged
  privileged: true
"""}, FAIL, "privileged-no-caps"),

    # ---- Type 4: RWO + RollingUpdate ------------------------------------
    ("RWO with RollingUpdate in one Deployment fails",
     {"deploy.yaml": """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: canary
spec:
  strategy:
    type: RollingUpdate
  template:
    spec:
      volumes:
        - name: d
          persistentVolumeClaim:
            claimName: d
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: d
spec:
  accessModes: [ReadWriteOnce]
"""}, PASS, None),   # separate documents: not decidable, must NOT guess

    ("RWO and RollingUpdate in the SAME object fails",
     {"deploy.yaml": """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: canary
spec:
  strategy:
    type: RollingUpdate
  volumeClaimTemplates:
    - spec:
        accessModes: [ReadWriteOnce]
"""}, FAIL, "rwo-rollingupdate"),

    ("RWO with Recreate passes",
     {"deploy.yaml": """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: canary
spec:
  strategy:
    type: Recreate
  volumeClaimTemplates:
    - spec:
        accessModes: [ReadWriteOnce]
"""}, PASS, None),

    # ---- Types 1/2: intercept addressing --------------------------------
    #
    # THE regression that mattered most. fragua-edge-01/02-designer carried
    # their literals in a .py file and the checker only read YAML, so it had
    # never once looked at the two offenders it was written for.
    ("an intercept literal inside the DNS pool fails IN A .py FILE",
     {"wire.py": '''
EDGES = [
    {"name": "edge-01-designer", "intercept": "100.65.0.10", "port": 8088},
]
'''}, FAIL, "intercept-in-dns-pool"),

    ("an intercept literal inside the pool fails in a shell script",
     {"wire.sh": '''#!/bin/sh
flux edge create config x intercept.v1 '{"addresses":["100.65.0.2"]}'
'''}, FAIL, "intercept-in-dns-pool"),

    # Prose is exempt, code is not. The good scripts explain the incident that
    # produced the rule, and the explanation must name the address to mean
    # anything — flagging it tells the author to delete the only record of why.
    ("an address quoted in a Python docstring passes",
     {"wire.py": '''"""Wire the service.

  * NEVER put an intercept inside 100.65.0.0/16. fragua-k3s-api was pinned at
    100.65.1.1 and the router later handed the same address to another service.
"""
INTERCEPT_ADDR = "100.64.65.2"
'''}, PASS, None),

    ("code AFTER a docstring is still checked",
     {"wire.py": '''"""Historical note: this used to be 100.65.0.2."""
INTERCEPT_ADDR = "100.65.0.77"
'''}, FAIL, "intercept-in-dns-pool"),

    ("a single-line docstring does not swallow the rest of the file",
     {"wire.py": '''"""One-liner."""
BAD = "100.65.0.55"
'''}, FAIL, "intercept-in-dns-pool"),

    ("a literal OUTSIDE the pool passes",
     {"wire.py": '''
INTERCEPT_ADDR = "100.64.65.3"
'''}, PASS, None),

    ("a .flux.internal name passes",
     {"wire.py": '''
INTERCEPT_ADDR = "fragua-edge-01-designer.flux.internal"
'''}, PASS, None),

    # A range DECLARATION is not an allocation. flux-edge-tunnel documents its
    # own --dnsSvcIpRange argument, and flagging that got the documentation
    # deleted rather than the bug fixed.
    ("a CIDR declaration of the pool itself passes",
     {"values.yaml": """
# tunneler is started with --dnsSvcIpRange 100.65.0.0/16
extraArgs: "--dnsSvcIpRange 100.65.0.0/16"
"""}, PASS, None),

    ("node ranges named in an inline comment pass",
     {"values.yaml": """
nodeCidrs: ""   # nodes live in 100.64.0.0/24 and 100.64.1.0/24
"""}, PASS, None),

    ("a node address intercept fails",
     {"wire.py": '''
# an intercept that shadows a real machine
INTERCEPT_ADDR = "100.64.1.7"
'''}, FAIL, "intercept-shadows-node"),

    # A tls-san list is the set of names a serving cert must cover. During an
    # address migration it legitimately holds the old address AND the new one —
    # that is the point of it. Reading those as intercepts produced three
    # findings on templates/k3s/k3s-config.server.fragua.yaml, a file that is
    # correct, and the only way to "fix" it would be to break agent TLS.
    ("a tls-san list holding pool and node addresses passes",
     {"k3s.yaml": """
# agents dial an intercept address, not the node's real one
tls-san:
  - "fragua-k3s-api.flux.internal"
  - "100.64.65.2"
  - "100.65.1.1"
  - "100.64.2.2"
  - "100.64.0.30"
"""}, PASS, None),

    ("a bad intercept AFTER a tls-san block is still caught",
     {"k3s.yaml": """
tls-san:
  - "100.65.1.1"
intercept:
  addresses:
    - "100.65.0.99"
"""}, FAIL, "intercept-in-dns-pool"),

    # ---- the checker's own trustworthiness ------------------------------
    ("a directory with no scannable files refuses to pass",
     {"README.md": "nothing to scan here\n"}, UNTRUSTWORTHY, "refusing to pass"),

    ("a file declaring itself fixtures is skipped whole",
     {"t.py": '''# app-invariants: fixtures
BAD = "100.65.0.10"
'''}, PASS, "declared fixtures"),

    # The opt-out must not become a way to silence real files. It is declared
    # per file and reported, never inferred from a path.
    ("fixtures marker below the header window does NOT apply",
     {"t.py": "\n" * 45 + '# app-invariants: fixtures\nBAD = "100.65.0.10"\n'},
     FAIL, "intercept-in-dns-pool"),
]


def run_checker(paths):
    proc = subprocess.run(
        [sys.executable, CHECKER] + paths,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def expected_code(expected):
    return {PASS: 0, FAIL: 1}.get(expected, expected)


def main():
    if not os.path.isfile(CHECKER):
        print("cannot find %s" % CHECKER, file=sys.stderr)
        return 2

    failures = []
    for name, files, expected, must_mention in CASES:
        with tempfile.TemporaryDirectory() as td:
            for fn, content in files.items():
                with open(os.path.join(td, fn), "w", encoding="utf-8") as fh:
                    fh.write(content)
            code, output = run_checker([td])

        want = expected_code(expected)
        problems = []
        if code != want:
            problems.append("exit %d, wanted %d" % (code, want))
        # A case that fails for an unrelated reason is not a passing test.
        if must_mention and must_mention not in output:
            problems.append("output never mentioned %r" % must_mention)

        if problems:
            failures.append((name, problems, output))
            print("FAIL  %s\n        %s" % (name, "; ".join(problems)))
        elif VERBOSE:
            print("ok    %s" % name)

    # --- the multi-root rule, which needs two paths at once ----------------
    #
    # `check ... cluster charts templates` with one stale argument still scans
    # plenty of files, so a total-only guard passes while an entire tree goes
    # unread. That is how the `charts` prune stayed invisible.
    with tempfile.TemporaryDirectory() as good, tempfile.TemporaryDirectory() as empty:
        with open(os.path.join(good, "ok.yaml"), "w", encoding="utf-8") as fh:
            fh.write("apiVersion: v1\nkind: ConfigMap\n")
        with open(os.path.join(empty, "notes.md"), "w", encoding="utf-8") as fh:
            fh.write("not scannable\n")
        code, output = run_checker([good, empty])
        if code != UNTRUSTWORTHY or "matched 0 files" not in output:
            msg = "exit %d (wanted 2) / output %r" % (code, output[:200])
            failures.append(("one empty root among several must fail", [msg], output))
            print("FAIL  one empty root among several must fail\n        %s" % msg)
        elif VERBOSE:
            print("ok    one empty root among several must fail")

    # --- waivers must be REPORTED, not just tolerated ----------------------
    #
    # An exception that stops being visible stops being an exception and
    # becomes the new default. That is how privileged: true spread.
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "values.yaml"), "w", encoding="utf-8") as fh:
            fh.write("securityContext:\n"
                     "  # app-invariants: allow-privileged — needs its own tun device\n"
                     "  privileged: true\n")
        code, output = run_checker([td])
        if code != 0 or "waiver(s) in force" not in output:
            msg = "exit %d / output %r" % (code, output[:200])
            failures.append(("a waiver is counted in the summary", [msg], output))
            print("FAIL  a waiver is counted in the summary\n        %s" % msg)
        elif VERBOSE:
            print("ok    a waiver is counted in the summary")

    total = len(CASES) + 2
    if failures:
        print("\n%d/%d case(s) FAILED — the invariant checker has regressed.\n"
              "Fix the checker before trusting any chart result: every "
              "downstream \"passes\" is measured with it." % (len(failures), total))
        if VERBOSE:
            for name, _, output in failures:
                print("\n--- %s ---\n%s" % (name, output))
        return 1

    print("check-app-invariants: %d/%d regression case(s) pass" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
