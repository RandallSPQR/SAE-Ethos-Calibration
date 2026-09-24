# runpod_watch — which datacenter should hold the network volume?

A network volume pins you to one datacenter, so the datacenter has to be picked
by *measured* GPU availability, not by a console glance. This tool polls
RunPod's documented catalog endpoint every 15 minutes, keeps the raw answers,
and reports availability as a rate per datacenter, plus which pair of
datacenters covers you best. Stdlib only; runs on the Mac's system `python3`. Both endpoints sit behind
Cloudflare, which rejects Python's default User-Agent; the script sends its own.

Method and reasoning: `RUNPOD_DC_SELECTION.md` (same directory).

## One-time setup

1. Put the API key (RunPod console: Settings, API Keys; starts with `rpa_`)
   in `~/runpod_watch/api_key`, mode 600. Easiest: `open -t ~/runpod_watch/api_key`,
   paste, save. The script also accepts `RUNPOD_API_KEY` in the environment or a
   login-keychain item named `RUNPOD_API_KEY`
   (`security add-generic-password -a "$USER" -s RUNPOD_API_KEY -w`), but the
   hidden prompt is easy to feed the wrong thing; the file is checked first.
   The key is never printed and never written into the launchd plist.

2. Check the key resolves and see today's catalog:

   ```bash
   tools/runpod_watch/runpod_watch.py datacenters --gpu a100 --only-hits
   ```

3. Install the poller (launchd user agent, every 15 min, runs at login too):

   ```bash
   tools/runpod_watch/runpod_watch.py install
   ```

   macOS does not let background agents read `~/Documents`, so `install`
   copies the tool to `~/runpod_watch/app/` and launchd runs that copy.
   Re-run `install` after editing the tool.
   Log: `~/runpod_watch/avail.jsonl`. Job stdout/stderr: `~/runpod_watch/poll.log`, `poll.err`.
   `status` shows job state, key resolution, poll count and last poll.
   `uninstall` removes the job and keeps the log.

## Reading the data

```bash
tools/runpod_watch/runpod_watch.py serve          # http://127.0.0.1:8765
tools/runpod_watch/runpod_watch.py report --since 7d --gpu-filter a100 --pairs --disagreements
```

The dashboard has: a ranking table (datacenter × GPU), a timeline heatmap
(one column per poll), the pair table, the GraphQL disagreement table, and a
coverage panel that shows where the laptop was asleep. "Poll now" appends one
observation immediately.

## What the numbers mean

- **listed** — fraction of polls where the catalog listed that GPU at that
  datacenter at all. A card that is not listed is recorded as **ABSENT** for
  that poll. This matters: absence is the usual way a card is unavailable,
  and a report that only counted listed rows would be biased upward.
- **usable** — fraction of polls in a grade you would deploy on. The catalog
  grades are `HIGH`, `MEDIUM`, `LOW`. Default counts any listed grade, because
  the secure A100 SXM was rented on 2026-09-17 while EUR-IS-1 reported LOW.
  Switch to "MEDIUM or HIGH" to see how much of the rate is LOW.
- **pairs / either** — P(at least one of the two datacenters usable), on
  aligned polls, next to the independence benchmark `1 − (1−pa)(1−pb)`. A
  negative gap means the two go dry together and hedge each other less than
  their solo rates suggest. Pick the top `either` with a non-negative gap.
- **GraphQL stockStatus** — global secure-cloud stock, not per datacenter,
  and known to be flaky. Recorded with `--cross-check` and shown against the
  catalog's best grade at the same poll so you can see how often they disagree
  before trusting either.
- **coverage** — polls seen vs. polls expected at 15 min over the window.
  launchd does not fire while the Mac sleeps, so a laptop series over-samples
  daytime; the hour histogram makes that visible.

## Record format (append-only JSONL, one poll = several lines)

```
{"ts":..,"src":"poll","n_dc":30,"dcs":[..]}                       # header; absence is measured against this
{"ts":..,"src":"catalog","dc":"EUR-IS-1","gpu":"NVIDIA A100-SXM4-80GB","gname":"A100 SXM","avail":"LOW","nv":["STANDARD"],"global_net":false}
{"ts":..,"src":"graphql","dc":null,"gpu":..,"secure":true,"stock_status":"Medium","bid":1.59,"ondemand":1.59}
{"ts":..,"src":"catalog","error":"HTTP 401","detail":..}          # failed poll; counted, not interpreted
```

The poller records every datacenter (no volume-type filter at poll time) and
every GPU matching the `--gpu` patterns (default: a100, h100, l40s, rtx pro
6000). Filtering by volume type and GPU happens at report time, so nothing
is thrown away.

## Snapshot at first run (2026-09-17, one read, not a rate)

A100 SXM was listed at EUR-IS-1, US-KS-2, US-MD-1, US-MO-1 and US-WA-1, all
LOW; of those only EUR-IS-1 offers STANDARD network volumes. A100 PCIe was
listed at EU-RO-1 (STANDARD). The doc's prior of US-KS-2 fails on the volume
criterion today. Whether that holds is what the week of polling is for.
