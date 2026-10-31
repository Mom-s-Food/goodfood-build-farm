#!/usr/bin/env python3
"""Counts the external probe's window (B9 gate G19) from farm-probe's artifacts.

Every farm-probe run keeps probe-<run id> with results.jsonl: one line per probe
({at, ok, run, seq, steps}; ok null means skipped). This reads every run of the last
--days (default 7) through the gh CLI and prints:

- the probes counted (ok true or false; skipped ones are not probes), passed and failed;
- every gap of more than --max-gap minutes (default 15) between counted probes, and
  between the window's edges and the first or last probe: each is a miss;
- the verdict: GREEN when the window holds counted probes from its start to its end,
  no miss and no two failed probes in a row; else NOT GREEN with the reasons.

Usage: python3 -I scripts/probe-window.py [--days 7] [--max-gap 15] [--repo OWNER/NAME] [--json]
Needs gh signed in with read access to the farm's Actions. Exit 0 green, 1 not green.
"""
import argparse
import datetime as dt
import io
import json
import subprocess
import sys
import zipfile


def gh(*args, raw=False):
    out = subprocess.run(["gh", *args], check=True, capture_output=True)
    return out.stdout if raw else json.loads(out.stdout or b"null")


def parse_at(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def load(repo, since):
    probes, page = [], 1
    while True:
        runs = gh("api", f"repos/{repo}/actions/workflows/farm-probe.yml/runs?per_page=100&page={page}"
                  f"&created=>={since:%Y-%m-%dT%H:%M:%SZ}")["workflow_runs"]
        if not runs:
            break
        for r in runs:
            arts = gh("api", f"repos/{repo}/actions/runs/{r['id']}/artifacts")["artifacts"]
            for a in arts:
                if a["name"] != f"probe-{r['id']}" or a["expired"]:
                    continue
                blob = gh("api", f"repos/{repo}/actions/artifacts/{a['id']}/zip", raw=True)
                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                    for name in z.namelist():
                        text = z.read(name).decode("utf-8")
                        if name.endswith(".jsonl"):
                            probes += [json.loads(l) for l in text.splitlines() if l.strip()]
                        elif name.endswith(".json"):  # runs before the chain kept one result.json
                            one = json.loads(text)
                            one.setdefault("at", r["created_at"])
                            probes.append(one)
        page += 1
    return probes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--max-gap", type=float, default=15)
    ap.add_argument("--repo", default="Mom-s-Food/goodfood-build-farm")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    end = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    start = end - dt.timedelta(days=a.days)
    probes = load(a.repo, start - dt.timedelta(hours=7))  # a run starts up to ~6 h before its last probe
    counted = sorted((p for p in probes if p.get("ok") in (True, False) and "at" in p
                      and start <= parse_at(p["at"]) <= end), key=lambda p: p["at"])
    gap = dt.timedelta(minutes=a.max_gap)
    misses, edges = [], [start] + [parse_at(p["at"]) for p in counted] + [end]
    for x, y in zip(edges, edges[1:]):
        if y - x > gap:
            misses.append({"from": f"{x:%Y-%m-%dT%H:%M:%SZ}", "to": f"{y:%Y-%m-%dT%H:%M:%SZ}",
                           "minutes": round((y - x).total_seconds() / 60, 1)})
    twice = [f"{p['at']} (run {p.get('run')})" for q, p in zip(counted, counted[1:])
             if q["ok"] is False and p["ok"] is False]
    passed = sum(1 for p in counted if p["ok"])
    reasons = []
    if not counted:
        reasons.append("no counted probe in the window")
    if misses:
        reasons.append(f"{len(misses)} gap(s) over {a.max_gap:g} minutes")
    if twice:
        reasons.append(f"{len(twice)} time(s) two failed probes in a row")
    report = {"window": {"from": f"{start:%Y-%m-%dT%H:%M:%SZ}", "to": f"{end:%Y-%m-%dT%H:%M:%SZ}"},
              "counted": len(counted), "passed": passed, "failed": len(counted) - passed,
              "skipped": sum(1 for p in probes if p.get("ok") is None),
              "first": counted[0]["at"] if counted else None, "last": counted[-1]["at"] if counted else None,
              "misses": misses, "two_failed_in_a_row": twice, "green": not reasons, "reasons": reasons}
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"window {report['window']['from']} .. {report['window']['to']} ({a.days:g} days)")
        print(f"counted {report['counted']} probes: {passed} passed, {report['failed']} failed; "
              f"first {report['first']}, last {report['last']}")
        for m in misses:
            print(f"miss: {m['from']} .. {m['to']} ({m['minutes']} min)")
        for t in twice:
            print(f"two failed in a row ending {t}")
        print("GREEN" if not reasons else "NOT GREEN: " + "; ".join(reasons))
    return 0 if not reasons else 1


if __name__ == "__main__":
    sys.exit(main())
