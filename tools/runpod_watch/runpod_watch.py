#!/usr/bin/env python3
"""
runpod_watch.py — measure RunPod GPU availability per datacenter over time.

Why this exists: a single read of RunPod stock tells you nothing; availability
is a rate, not a state. This polls the documented v2 catalog endpoint on a
schedule, appends raw records to a JSONL time series, and reports availability
RATES per datacenter, plus which PAIR of datacenters covers you best.

Primary source (needs an API key):
    GET https://api.runpod.io/v2/catalog/datacenters?include=GPU_AVAILABILITY
Cross-check (no key needed, known-flaky, recorded for disagreement only):
    POST https://api.runpod.io/graphql  (gpuTypes.lowestPrice.stockStatus)

Stdlib only; runs on the system python3 (3.9+).

Record semantics
----------------
Every poll writes ONE header row  {"src":"poll", "dcs":[...]}  and then one
row per (datacenter, matching GPU) that the catalog listed. A GPU the catalog
does NOT list at a datacenter is recorded by its absence and counted by the
report as "ABSENT" for that poll — absence is the most common way a card is
unavailable, so a report that only counted listed rows would be biased up.
The poller never interprets an availability string; it stores what the API
returned and lets the report bucket it.

Key resolution (never printed): $RUNPOD_API_KEY, else ~/runpod_watch/api_key
(mode 600), else the login keychain item named RUNPOD_API_KEY. Store it with
    security add-generic-password -a "$USER" -s RUNPOD_API_KEY -w
(the prompt keeps the value out of your shell history).

Usage
-----
  ./runpod_watch.py datacenters                 # one-shot listing
  ./runpod_watch.py poll --cross-check          # one observation -> log
  ./runpod_watch.py report --since 7d --gpu-filter a100 --pairs
  ./runpod_watch.py serve                       # dashboard at http://127.0.0.1:8765
  ./runpod_watch.py install                     # launchd job every 15 min
  ./runpod_watch.py status | uninstall
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import http.server
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CATALOG_URL = "https://api.runpod.io/v2/catalog/datacenters"
GRAPHQL_URL = "https://api.runpod.io/graphql"
HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("RUNPOD_WATCH_DIR", str(Path.home() / "runpod_watch")))
DEFAULT_LOG = DATA_DIR / "avail.jsonl"
LAUNCHD_LABEL = "com.sae-ethos.runpod-watch"
POLL_INTERVAL_S = 900
# Cloudflare in front of api.runpod.io returns 403 (error 1010) for the default
# "Python-urllib/x.y" signature; any ordinary product token gets through.
USER_AGENT = "runpod-watch/1.0 (stdlib urllib)"
ABSENT = "ABSENT"

# Substring match, case-insensitive, against the GPU id the API returns
# ("NVIDIA A100-SXM4-80GB", "NVIDIA A100 80GB PCIe", ...). Loose on purpose;
# narrow in the report with --gpu-filter.
DEFAULT_GPU_PATTERNS = ["a100", "h100", "l40s", "rtx pro 6000"]


# ------------------------------------------------------------------ key + http

def _key(required: bool = True) -> str | None:
    k = os.environ.get("RUNPOD_API_KEY", "").strip()
    if k:
        return k
    f = DATA_DIR / "api_key"
    if f.exists():
        mode = stat.S_IMODE(f.stat().st_mode)
        if mode & 0o077:
            print(f"warning: {f} is mode {mode:o}; chmod 600 it", file=sys.stderr)
        k = f.read_text(encoding="utf-8").strip()
        if k:
            return k
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "RUNPOD_API_KEY", "-w"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    if required:
        sys.exit(
            "No RunPod API key. Set RUNPOD_API_KEY, or store it once with:\n"
            '  security add-generic-password -a "$USER" -s RUNPOD_API_KEY -w\n'
            f"or write it to {f} (chmod 600)."
        )
    return None


def _get(url: str, params: dict | None = None) -> dict:
    if params:
        pairs = []
        for k, v in params.items():
            for item in (v if isinstance(v, list) else [v]):
                pairs.append((k, item))
        url = f"{url}?{urllib.parse.urlencode(pairs)}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_key()}", "Accept": "application/json",
                      "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())


def _graphql(query: str) -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    k = _key(required=False)
    if k:
        headers["Authorization"] = f"Bearer {k}"
    req = urllib.request.Request(GRAPHQL_URL, data=json.dumps({"query": query}).encode(),
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _matches(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(p.lower() in low for p in patterns if p)


def _dcs(data: dict) -> list[dict]:
    return data.get("dataCenters", data.get("datacenters", [])) or []


# ---------------------------------------------------------------- datacenters

def _explain_http(e: urllib.error.HTTPError) -> str:
    if e.code == 401:
        return ("HTTP 401: RunPod rejected the API key. Keys look like rpa_... (roughly 50 chars). "
                "Easiest fix: copy the key in the RunPod console, then run\n"
                "  umask 077; pbpaste > ~/runpod_watch/api_key\n"
                "(the file wins over the keychain item). Or replace the keychain item:\n"
                "  security delete-generic-password -s RUNPOD_API_KEY; "
                "security add-generic-password -a \"$USER\" -s RUNPOD_API_KEY -w")
    if e.code == 403:
        return "HTTP 403: blocked at the edge (Cloudflare); the request signature, not the key"
    return f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"


def cmd_datacenters(args) -> None:
    try:
        dcs = _dcs(_get(CATALOG_URL, {"include": "GPU_AVAILABILITY"}))
    except urllib.error.HTTPError as e:
        sys.exit(_explain_http(e))
    nv_want = {v.upper() for v in (args.network_volumes or [])}
    shown = 0
    for dc in sorted(dcs, key=lambda d: d.get("id", "")):
        nvt = [v.upper() for v in (dc.get("networkVolumeTypes") or [])]
        if nv_want and not nv_want <= set(nvt):
            continue
        hits = [g for g in (dc.get("gpuAvailability") or [])
                if _matches(f"{g.get('id','')} {g.get('name','')}", args.gpu)]
        if args.only_hits and not hits:
            continue
        shown += 1
        gn = "global-net" if dc.get("globalNetwork") else ""
        print(f"{dc.get('id','?'):<10} {dc.get('region',''):<14} nv=[{','.join(nvt) or '-'}] {gn}")
        for g in sorted(hits, key=lambda g: g.get("id", "")):
            print(f"    {g.get('availability','?'):<8} {g.get('id')}  ({g.get('name')})")
        if not hits:
            print("    (no matching GPU listed)")
    print(f"\n{shown} of {len(dcs)} datacenter(s) shown")


# ---------------------------------------------------------------------- poll

def do_poll(log: Path, patterns: list[str], cross_check: bool) -> list[dict]:
    ts = _now()
    rows: list[dict] = []
    try:
        data = _get(CATALOG_URL, {"include": "GPU_AVAILABILITY"})
        dcs = _dcs(data)
        rows.append({"ts": ts, "src": "poll", "n_dc": len(dcs),
                     "dcs": sorted(d.get("id") for d in dcs if d.get("id"))})
    except urllib.error.HTTPError as e:
        rows.append({"ts": ts, "src": "catalog", "error": f"HTTP {e.code}",
                     "detail": e.read().decode(errors="replace")[:300]})
        dcs = []
    except Exception as e:  # noqa: BLE001 - poller must never die on a schedule
        rows.append({"ts": ts, "src": "catalog", "error": repr(e)})
        dcs = []

    for dc in dcs:
        nvt = [v.upper() for v in (dc.get("networkVolumeTypes") or [])]
        for g in dc.get("gpuAvailability") or []:
            gid = g.get("id") or g.get("name") or ""
            if not _matches(f"{gid} {g.get('name','')}", patterns):
                continue
            rows.append({"ts": ts, "src": "catalog", "dc": dc.get("id"), "gpu": gid,
                         "gname": g.get("name"), "avail": g.get("availability"),
                         "nv": nvt, "global_net": dc.get("globalNetwork")})

    if cross_check:
        q = ("query { gpuTypes { id displayName "
             "lowestPrice(input:{gpuCount:1, secureCloud:true}) "
             "{ stockStatus minimumBidPrice uninterruptablePrice } } }")
        try:
            gd = _graphql(q)
            if gd.get("errors"):
                raise RuntimeError(str(gd["errors"])[:300])
            for gt in (gd.get("data") or {}).get("gpuTypes") or []:
                gid = gt.get("id") or gt.get("displayName") or ""
                if not _matches(f"{gid} {gt.get('displayName','')}", patterns):
                    continue
                lp = gt.get("lowestPrice") or {}
                rows.append({"ts": ts, "src": "graphql", "dc": None, "gpu": gid,
                             "secure": True, "stock_status": lp.get("stockStatus"),
                             "bid": lp.get("minimumBidPrice"),
                             "ondemand": lp.get("uninterruptablePrice")})
        except Exception as e:  # noqa: BLE001
            rows.append({"ts": ts, "src": "graphql", "error": repr(e)})

    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    return rows


def cmd_poll(args) -> None:
    rows = do_poll(Path(args.log), args.gpu, args.cross_check)
    errs = [r for r in rows if "error" in r]
    print(f"{rows[0]['ts']}  wrote {len(rows)} row(s) to {args.log}"
          + (f"  [{len(errs)} error(s)]" if errs else ""))
    for e in errs:
        print(f"  ! {e['src']}: {e.get('error')} {e.get('detail','')}", file=sys.stderr)
    if errs and len(errs) == len([r for r in rows if r["src"] != "graphql"]):
        sys.exit(1)


# -------------------------------------------------------------- report engine

def _parse_since(s: str | None) -> dt.datetime | None:
    if not s or s in ("all", "0"):
        return None
    unit, mult = s[-1], {"h": 3600, "d": 86400, "w": 604800}
    if unit not in mult or not s[:-1].isdigit():
        raise ValueError("--since takes forms like 12h, 7d, 2w, or all")
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=int(s[:-1]) * mult[unit])


def _iter_log(log: Path):
    if not log.exists():
        return
    with open(log, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def build_report(log: Path, since: str | None = None, gpu_filter: str = "",
                 nv: str = "", usable: str = "HIGH,MEDIUM,LOW") -> dict:
    """Everything the CLI report and the dashboard show, from one pass.

    usable: comma list of availability grades you would deploy on. ABSENT is
    never usable. Grades are compared upper-cased; unknown strings are kept
    verbatim so a new enum value shows up rather than vanishing.
    """
    cutoff = _parse_since(since)
    usable_set = {u.strip().upper() for u in usable.split(",") if u.strip()}
    gf = (gpu_filter or "").lower()
    nv = (nv or "").upper()

    polls: dict[str, dict] = {}          # ts -> header (or synthesized)
    obs: dict[str, dict[tuple, str]] = collections.defaultdict(dict)  # ts -> {(dc,gpu): avail}
    dc_nv: dict[str, list] = {}          # latest networkVolumeTypes per dc
    gname: dict[str, str] = {}
    gql: dict[str, dict[str, str]] = collections.defaultdict(dict)   # ts -> {gpu: status}
    gql_price: dict[str, float] = {}
    errors: list[dict] = []

    for r in _iter_log(log):
        ts = r.get("ts")
        if not ts:
            continue
        if cutoff:
            try:
                if dt.datetime.fromisoformat(ts) < cutoff:
                    continue
            except ValueError:
                continue
        src = r.get("src")
        if "error" in r:
            errors.append({"ts": ts, "src": src, "error": r.get("error")})
            continue
        if src == "poll":
            polls[ts] = r
        elif src == "catalog":
            polls.setdefault(ts, {"ts": ts, "synth": True})
            dcid, gpu = r.get("dc"), r.get("gpu", "")
            if not dcid or not gpu:
                continue
            dc_nv[dcid] = [v.upper() for v in (r.get("nv") or [])]
            if r.get("gname"):
                gname[gpu] = r["gname"]
            if gf and gf not in gpu.lower() and gf not in str(r.get("gname", "")).lower():
                continue
            obs[ts][(dcid, gpu)] = str(r.get("avail"))
        elif src == "graphql":
            gpu = r.get("gpu", "")
            if gf and gf not in gpu.lower():
                continue
            gql[ts][gpu] = str(r.get("stock_status"))
            if r.get("ondemand") is not None:
                gql_price[gpu] = r["ondemand"]

    stamps = sorted(polls)
    keys = sorted({k for m in obs.values() for k in m})
    if nv:
        keys = [k for k in keys if nv in dc_nv.get(k[0], [])]

    # per-(dc,gpu) distribution with ABSENT filled in
    table = []
    for key in keys:
        counts: collections.Counter = collections.Counter()
        last_state, last_listed, streak = ABSENT, None, 0
        for ts in stamps:
            st = obs[ts].get(key, ABSENT).upper()
            counts[st] += 1
            if st != ABSENT:
                last_listed = ts
            # streak = consecutive most-recent polls in the same state
            if ts == stamps[-1]:
                last_state = st
        for ts in reversed(stamps):
            if obs[ts].get(key, ABSENT).upper() == last_state:
                streak += 1
            else:
                break
        n = len(stamps)
        listed = n - counts[ABSENT]
        usable_n = sum(v for k, v in counts.items() if k in usable_set)
        table.append({
            "dc": key[0], "gpu": key[1], "gname": gname.get(key[1], ""),
            "nv": dc_nv.get(key[0], []), "n": n,
            "listed_pct": listed / n if n else 0.0,
            "usable_pct": usable_n / n if n else 0.0,
            "dist": dict(counts.most_common()),
            "last_state": last_state, "last_listed": last_listed, "streak": streak,
        })
    table.sort(key=lambda t: (-t["usable_pct"], -t["listed_pct"], t["dc"], t["gpu"]))

    # per-DC usability grid (any matching GPU usable), for heatmap + pairs
    dcs = sorted({k[0] for k in keys})
    grade_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, ABSENT: 0}
    timeline = {d: [] for d in dcs}           # best grade per poll
    usable_grid = {d: [] for d in dcs}
    for ts in stamps:
        for d in dcs:
            best, ok = ABSENT, False
            for key in keys:
                if key[0] != d:
                    continue
                st = obs[ts].get(key, ABSENT).upper()
                if st in usable_set:
                    ok = True
                if grade_rank.get(st, 1) > grade_rank.get(best, 0) or (st not in grade_rank and best == ABSENT):
                    best = st
            timeline[d].append(best)
            usable_grid[d].append(ok)

    n = len(stamps)
    solo = {d: (sum(usable_grid[d]) / n if n else 0.0) for d in dcs}
    pairs = []
    for i, a in enumerate(dcs):
        for b in dcs[i + 1:]:
            either = sum(1 for k in range(n) if usable_grid[a][k] or usable_grid[b][k])
            both = sum(1 for k in range(n) if usable_grid[a][k] and usable_grid[b][k])
            pa, pb = solo[a], solo[b]
            indep = 1 - (1 - pa) * (1 - pb)
            e = either / n if n else 0.0
            pairs.append({"a": a, "b": b, "either": e, "indep": indep, "gap": e - indep,
                          "both": both / n if n else 0.0, "n": n,
                          "correlated": (e - indep) < -0.03})
    pairs.sort(key=lambda p: (-p["either"], p["gap"]))

    # GraphQL cross-check: distribution + disagreement with the catalog's best grade
    gql_dist: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    disagree: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for ts, m in gql.items():
        for gpu, st in m.items():
            gql_dist[gpu][st] += 1
            if ts in obs:
                best = ABSENT
                for key, v in obs[ts].items():
                    if key[1] == gpu and grade_rank.get(v.upper(), 1) > grade_rank.get(best, 0):
                        best = v.upper()
                disagree[gpu][f"gql={st} / catalog_best={best}"] += 1
    # agreement summary: rank the two scales the same way and compare per poll
    gql_rank = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    gql_out = []
    for g, c in sorted(gql_dist.items()):
        agree = higher = lower = 0
        for label, v in disagree[g].items():
            gq, cb = label.split(" / ")
            a = gql_rank.get(gq[len("gql="):].upper(), None)
            b = grade_rank.get(cb[len("catalog_best="):].upper(), None)
            if a is None or b is None:
                continue
            if a == b:
                agree += v
            elif a > b:
                higher += v
            else:
                lower += v
        tot = agree + higher + lower
        gql_out.append({"gpu": g, "dist": dict(c.most_common()), "ondemand": gql_price.get(g),
                        "aligned": tot,
                        "agree_pct": agree / tot if tot else None,
                        "gql_higher_pct": higher / tot if tot else None,
                        "gql_lower_pct": lower / tot if tot else None,
                        "vs_catalog": dict(disagree[g].most_common())})

    # coverage: how continuous the series is (laptop asleep => gaps)
    coverage = {"n_polls": n, "first": stamps[0] if stamps else None,
                "last": stamps[-1] if stamps else None, "expected": None,
                "hour_hist": [0] * 24, "gaps": []}
    if n >= 2:
        t0 = dt.datetime.fromisoformat(stamps[0])
        t1 = dt.datetime.fromisoformat(stamps[-1])
        coverage["expected"] = int((t1 - t0).total_seconds() // POLL_INTERVAL_S) + 1
        prev = t0
        for ts in stamps[1:]:
            t = dt.datetime.fromisoformat(ts)
            if (t - prev).total_seconds() > 2.5 * POLL_INTERVAL_S:
                coverage["gaps"].append({"from": prev.isoformat(timespec="seconds"),
                                         "to": t.isoformat(timespec="seconds"),
                                         "hours": round((t - prev).total_seconds() / 3600, 1)})
            prev = t
    for ts in stamps:
        coverage["hour_hist"][dt.datetime.fromisoformat(ts).astimezone().hour] += 1

    return {
        "generated": _now(), "log": str(log), "since": since or "all",
        "gpu_filter": gpu_filter, "nv": nv, "usable": sorted(usable_set),
        "stamps": stamps, "n_errors": len(errors), "errors": errors[-20:],
        "table": table, "dcs": dcs, "solo": solo, "timeline": timeline,
        "pairs": pairs, "graphql": gql_out, "coverage": coverage,
        "dc_nv": {d: dc_nv.get(d, []) for d in dcs},
    }


# -------------------------------------------------------------------- report

def cmd_report(args) -> None:
    rep = build_report(Path(args.log), args.since, args.gpu_filter or "",
                       args.network_volume or "", args.usable)
    if not rep["table"]:
        sys.exit("No catalog rows matched. Poll for a while first (or loosen the filters).")
    cov = rep["coverage"]
    print(f"{cov['n_polls']} poll(s) {cov['first']} .. {cov['last']}"
          + (f", expected ~{cov['expected']} at 15 min" if cov["expected"] else "")
          + (f", {rep['n_errors']} failed" if rep["n_errors"] else ""))
    if cov["gaps"]:
        print(f"gaps > 37 min: {len(cov['gaps'])} (largest {max(g['hours'] for g in cov['gaps'])} h)")
    print(f"usable = {rep['usable']}   nv filter = {rep['nv'] or '-'}   gpu filter = {rep['gpu_filter'] or '-'}\n")

    print(f"{'usable%':>8} {'listed%':>8} {'n':>5}  {'datacenter':<10} {'nv':<9} {'gpu':<26} last (streak)  distribution")
    print("-" * 118)
    for t in rep["table"]:
        dist = " ".join(f"{k}={v}" for k, v in t["dist"].items())
        nv = ",".join(x[0] for x in t["nv"]) or "-"
        print(f"{t['usable_pct']*100:7.1f}% {t['listed_pct']*100:7.1f}% {t['n']:5d}  {t['dc']:<10} {nv:<9} "
              f"{(t['gname'] or t['gpu'])[:26]:<26} {t['last_state']:<7}({t['streak']:>3})  {dist}")

    if args.pairs and rep["pairs"]:
        print("\nPair analysis — P(at least one datacenter usable), aligned polls:")
        print(f"{'either':>8} {'if indep':>9} {'gap':>7} {'both':>7} {'n':>5}  pair")
        print("-" * 74)
        for p in rep["pairs"][:15]:
            flag = "  <- correlated" if p["correlated"] else ""
            print(f"{p['either']*100:7.1f}% {p['indep']*100:8.1f}% {p['gap']*100:+6.1f}% "
                  f"{p['both']*100:6.1f}% {p['n']:5d}  {p['a']} + {p['b']}{flag}")
        print("\nSolo: " + ", ".join(f"{d}={rep['solo'][d]*100:.0f}%" for d in rep["dcs"]))
        print("Pick the top 'either' with a non-negative gap; a negative gap means the")
        print("two dry up together and are near-substitutes, not a hedge.")

    if args.disagreements and rep["graphql"]:
        print("\nGraphQL stockStatus (global secure-cloud, not per-DC) vs catalog best grade:")
        for g in rep["graphql"]:
            print(f"  {g['gpu'][:40]:<40} " + " ".join(f"{k}={v}" for k, v in g["dist"].items()))
            if g["aligned"]:
                print(f"      aligned polls {g['aligned']}: agree {g['agree_pct']*100:.0f}%  "
                      f"gql higher {g['gql_higher_pct']*100:.0f}%  gql lower {g['gql_lower_pct']*100:.0f}%")
            for k, v in list(g["vs_catalog"].items())[:6]:
                print(f"      {v:5d}  {k}")


# --------------------------------------------------------------------- serve

class _Handler(http.server.BaseHTTPRequestHandler):
    log_path: Path = DEFAULT_LOG
    patterns: list[str] = DEFAULT_GPU_PATTERNS

    def log_message(self, fmt, *a):  # quiet
        if os.environ.get("RUNPOD_WATCH_VERBOSE"):
            super().log_message(fmt, *a)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path in ("/", "/index.html"):
            self._send(200, (HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        elif u.path == "/api/report":
            q = urllib.parse.parse_qs(u.query)
            try:
                rep = build_report(self.log_path, q.get("since", ["7d"])[0],
                                   q.get("gpu", ["a100"])[0], q.get("nv", [""])[0],
                                   q.get("usable", ["HIGH,MEDIUM,LOW"])[0])
                self._send(200, json.dumps(rep).encode(), "application/json")
            except ValueError as e:
                self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path == "/api/poll":
            rows = do_poll(self.log_path, self.patterns, True)
            errs = [r for r in rows if "error" in r]
            self._send(200, json.dumps({"ts": rows[0]["ts"], "rows": len(rows), "errors": errs}).encode(),
                       "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def cmd_serve(args) -> None:
    _Handler.log_path = Path(args.log)
    _Handler.patterns = args.gpu
    srv = http.server.ThreadingHTTPServer((args.bind, args.port), _Handler)
    print(f"dashboard: http://{args.bind}:{args.port}/   log: {args.log}   (ctrl-c to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


# ------------------------------------------------------------------- launchd

def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def cmd_install(args) -> None:
    if _key(required=False) is None:
        sys.exit("Store the API key first (see --help); the launchd job reads it from the keychain.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # macOS TCC denies background agents access to ~/Documents, ~/Desktop and
    # ~/Downloads, so launchd runs a copy of the tool from the data directory.
    # Re-run `install` after editing the tool to refresh the copy.
    app = DATA_DIR / "app"
    app.mkdir(exist_ok=True)
    for name in ("runpod_watch.py", "dashboard.html"):
        shutil.copy2(HERE / name, app / name)
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [sys.executable, str(app / "runpod_watch.py"), "poll",
                             "--log", str(Path(args.log)), "--cross-check"],
        "StartInterval": args.interval,
        "RunAtLoad": True,
        "StandardOutPath": str(DATA_DIR / "poll.log"),
        "StandardErrorPath": str(DATA_DIR / "poll.err"),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                                 "RUNPOD_WATCH_DIR": str(DATA_DIR)},
    }
    p = _plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LAUNCHD_LABEL}"],
                   capture_output=True)
    for old_log in ("poll.log", "poll.err"):   # fresh install, fresh diagnostics
        (DATA_DIR / old_log).write_text("")
    with open(p, "wb") as fh:
        plistlib.dump(plist, fh)
    r = subprocess.run(["launchctl", "bootstrap", _domain(), str(p)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"launchctl bootstrap failed: {r.stderr.strip()}")
    print(f"installed {LAUNCHD_LABEL}: every {args.interval}s -> {args.log}\n"
          f"runs the copy in {app} (re-run install after editing the tool)\n"
          f"plist: {p}\nstdout/stderr: {DATA_DIR}/poll.log, poll.err")


def cmd_uninstall(args) -> None:
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LAUNCHD_LABEL}"], capture_output=True)
    p = _plist_path()
    if p.exists():
        p.unlink()
    print(f"removed {LAUNCHD_LABEL} (log kept at {args.log})")


def cmd_status(args) -> None:
    r = subprocess.run(["launchctl", "print", f"{_domain()}/{LAUNCHD_LABEL}"],
                       capture_output=True, text=True)
    if r.returncode:
        print("launchd job: not installed")
    else:
        keep = []
        for ln in r.stdout.splitlines():
            ln = ln.strip()
            if any(k in ln for k in ("state =", "last exit code", "run interval", "runs =")) and ln not in keep:
                keep.append(ln)
        print("launchd job: " + "; ".join(keep))
    print("api key: " + ("resolves" if _key(required=False) else "NOT FOUND"))
    log = Path(args.log)
    if not log.exists():
        print(f"log: {log} does not exist yet")
        return
    last = None
    n = 0
    for r_ in _iter_log(log):
        if r_.get("src") == "poll":
            n += 1
            last = r_["ts"]
    print(f"log: {log}  {log.stat().st_size/1e6:.1f} MB  polls={n}  last={last}")
    err = DATA_DIR / "poll.err"
    if err.exists() and err.stat().st_size:
        tail = err.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
        print("recent stderr:\n  " + "\n  ".join(tail))


# ----------------------------------------------------------------------- cli

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def gpu_opt(sp):
        sp.add_argument("--gpu", nargs="+", default=DEFAULT_GPU_PATTERNS,
                        help="substring patterns of GPU ids to record")

    def log_opt(sp):
        sp.add_argument("--log", default=str(DEFAULT_LOG))

    sp = sub.add_parser("datacenters", help="one-shot listing from the catalog")
    gpu_opt(sp)
    sp.add_argument("--network-volumes", nargs="*", default=[],
                    help="only DCs supporting ALL of these (STANDARD, HIGH_PERFORMANCE)")
    sp.add_argument("--only-hits", action="store_true", help="hide DCs with no matching GPU")
    sp.set_defaults(func=cmd_datacenters)

    sp = sub.add_parser("poll", help="append one observation to the log")
    gpu_opt(sp); log_opt(sp)
    sp.add_argument("--cross-check", action="store_true",
                    help="also record GraphQL stockStatus for comparison")
    sp.set_defaults(func=cmd_poll)

    sp = sub.add_parser("report", help="summarise the log")
    log_opt(sp)
    sp.add_argument("--since", default=None, help="e.g. 24h, 7d, 2w (default: all)")
    sp.add_argument("--usable", default="HIGH,MEDIUM,LOW",
                    help="grades you would deploy on; ABSENT never counts")
    sp.add_argument("--gpu-filter", help="restrict to one GPU substring, e.g. a100")
    sp.add_argument("--network-volume", help="restrict to DCs with this volume type, e.g. STANDARD")
    sp.add_argument("--pairs", action="store_true",
                    help="rank DATACENTER PAIRS by P(at least one usable)")
    sp.add_argument("--disagreements", action="store_true",
                    help="also show the GraphQL stockStatus column")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("serve", help="local dashboard over the log")
    gpu_opt(sp); log_opt(sp)
    sp.add_argument("--bind", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("install", help="launchd job: poll every 15 min")
    log_opt(sp)
    sp.add_argument("--interval", type=int, default=POLL_INTERVAL_S)
    sp.set_defaults(func=cmd_install)

    sp = sub.add_parser("uninstall", help="remove the launchd job (keeps the log)")
    log_opt(sp)
    sp.set_defaults(func=cmd_uninstall)

    sp = sub.add_parser("status", help="job state, key resolution, log summary")
    log_opt(sp)
    sp.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
