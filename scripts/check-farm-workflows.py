#!/usr/bin/env python3
"""Keeps the farm within its job budget (.github/farm-capacity.json).

The org is on GitHub Free: about 20 concurrent jobs for the whole org. On 2026-10-31 an uncapped
farm-fuzz matrix held all of them for 40 minutes and every farm-ci run queued behind it. This check
fails when a workflow could do that again:

- every matrix has a literal max-parallel, at most max_parallel_cap;
- every workflow file is listed in the budget, as on-demand or background;
- every background workflow runs one at a time: its workflow-level concurrency group is the fixed
  group of the budget (not per commit or branch) and cancel-in-progress is false;
- the background workflows' peaks (the most jobs one run can hold at once, times its groups) add up
  to at most org_concurrent_jobs - ci_reserve, so farm-ci always finds room;
- daily schedules start at least min_cron_gap_minutes apart.

Usage: python3 -I scripts/check-farm-workflows.py [repository root]
"""
import itertools
import json
import os
import sys

import yaml


def matrix_width(job):
    """The most matrix jobs of this job that can run at once, or an error."""
    strategy = job.get("strategy") or {}
    if "matrix" not in strategy:
        return 1, None
    cap = strategy.get("max-parallel")
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        return None, "has a matrix without a literal max-parallel"
    size = None
    matrix = strategy["matrix"]
    if isinstance(matrix, dict):
        axes = [v for k, v in matrix.items() if k not in ("include", "exclude")]
        include = matrix.get("include")
        if axes and all(isinstance(v, list) for v in axes) and not isinstance(include, str):
            size = 1
            for v in axes:
                size *= len(v)
            size += len(include) if isinstance(include, list) else 0
        elif not axes and isinstance(include, list):
            size = len(include)
    return (min(cap, size) if size else cap), cap


def peak(jobs, widths):
    """The largest total width of jobs that can run at the same time (no needs path between them)."""
    names = list(jobs)
    needs = {}
    for n in names:
        nd = jobs[n].get("needs") or []
        needs[n] = {nd} if isinstance(nd, str) else set(nd)
    ancestors = {}

    def anc(n):
        if n not in ancestors:
            ancestors[n] = set()
            for m in needs[n]:
                if m in jobs:
                    ancestors[n] |= {m} | anc(m)
        return ancestors[n]

    for n in names:
        anc(n)
    if len(names) > 16:
        return sum(widths.values())
    best = 0
    for r in range(1, len(names) + 1):
        for combo in itertools.combinations(names, r):
            if all(a not in ancestors[b] and b not in ancestors[a] for a, b in itertools.combinations(combo, 2)):
                best = max(best, sum(widths[n] for n in combo))
    return best


def daily_minutes(cron):
    """Minute of the day of a cron that runs at one fixed time, else None (e.g. */5)."""
    parts = cron.split()
    if len(parts) != 5 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return int(parts[1]) * 60 + int(parts[0])


def main(root):
    policy = json.load(open(os.path.join(root, ".github", "farm-capacity.json"), encoding="utf-8"))
    wf_dir = os.path.join(root, ".github", "workflows")
    errors, rows, crons = [], [], []
    background = policy["background"]
    cap_limit = policy["max_parallel_cap"]
    seen = set()
    for fname in sorted(os.listdir(wf_dir)):
        if not fname.endswith((".yml", ".yaml")):
            continue
        name = fname.rsplit(".", 1)[0]
        seen.add(name)
        path = f".github/workflows/{fname}"
        doc = yaml.safe_load(open(os.path.join(wf_dir, fname), encoding="utf-8"))
        jobs = doc.get("jobs") or {}
        widths = {}
        for jname, job in jobs.items():
            width, cap = matrix_width(job)
            if width is None:
                errors.append(f"{path}: job {jname} {cap}")
                width = 20
            elif cap is not None and cap > cap_limit:
                errors.append(f"{path}: job {jname} has max-parallel {cap}, more than {cap_limit}")
            widths[jname] = width
        on = doc.get(True, doc.get("on")) or {}
        for entry in (on.get("schedule") or []) if isinstance(on, dict) else []:
            m = daily_minutes(entry.get("cron", ""))
            if m is not None:
                crons.append((m, name, entry["cron"]))
        top = peak(jobs, widths)
        if name in background:
            want = background[name]
            conc = doc.get("concurrency")
            group = conc.get("group") if isinstance(conc, dict) else conc
            if group != want["group"]:
                errors.append(f"{path}: a background workflow needs the workflow-level concurrency group "
                              f"'{want['group']}' (one run at a time on the farm), found {group!r}")
            if not isinstance(conc, dict) or conc.get("cancel-in-progress") is not False:
                errors.append(f"{path}: a background workflow needs cancel-in-progress: false "
                              "(a newer dispatch replaces the queued run, never the running one)")
            groups = want.get("groups", 1)
            rows.append((name, "background", top, groups, top * groups))
        elif name in policy["on_demand"]:
            rows.append((name, "on-demand", top, None, None))
        else:
            errors.append(f"{path}: not in .github/farm-capacity.json; list it as background (with its "
                          "fixed concurrency group) or on_demand")
    for name in sorted(set(background) | set(policy["on_demand"])):
        if name not in seen:
            errors.append(f".github/farm-capacity.json: {name} has no workflow file")

    used = sum(r[4] for r in rows if r[4] is not None)
    room = policy["org_concurrent_jobs"] - policy["ci_reserve"]
    if used > room:
        errors.append(f"the background workflows can hold {used} jobs at once, more than {room} "
                      f"({policy['org_concurrent_jobs']} for the org minus {policy['ci_reserve']} kept for farm-ci)")

    crons.sort()
    gap = policy["min_cron_gap_minutes"]
    for (m1, n1, c1), (m2, n2, c2) in itertools.combinations(crons, 2):
        d = abs(m1 - m2)
        d = min(d, 1440 - d)
        if d < gap:
            errors.append(f"schedules of {n1} ('{c1}') and {n2} ('{c2}') start {d} minutes apart, "
                          f"less than {gap}; spread them")

    print("| workflow | kind | peak jobs | groups | budgeted |")
    print("| --- | --- | ---: | ---: | ---: |")
    for name, kind, top, groups, total in rows:
        print(f"| {name} | {kind} | {top} | {groups or ''} | {total if total is not None else ''} |")
    print(f"\nbackground total {used} of {room} (org {policy['org_concurrent_jobs']}, "
          f"farm-ci keeps {policy['org_concurrent_jobs'] - used})")
    print("daily schedules (UTC): " + ", ".join(f"{n} {m // 60:02d}:{m % 60:02d}" for m, n, _ in crons))
    for e in errors:
        print(f"::error::{e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
