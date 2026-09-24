# Picking two RunPod datacenters for two network volumes

## Why I can't just name two

Nobody publishes per-datacenter A100 stock, RunPod included. The "14 secure / 17
community regions" figure on the A100 pages is platform boilerplate — it appears
verbatim on the RTX Pro 6000 page too. Any two DCs I named today would be a guess
dressed as a recommendation, and you'd find out it was wrong the first week a pod
wouldn't schedule.

The good news: there *is* a first-party, per-DC, documented source. It's just not
the GraphQL `stockStatus` everyone wraps.

```
GET https://api.runpod.io/v2/catalog/datacenters
      ?include=GPU_AVAILABILITY
      &networkVolumeTypes=STANDARD
Authorization: Bearer $RUNPOD_API_KEY
```

That returns, per datacenter: `id`, `region`, `globalNetwork`,
`networkVolumeTypes`, and a `gpuAvailability` array of `{id, name, availability}`.
The `networkVolumeTypes` filter is the important part — it drops every DC that
can't hold your volume in the first place, which is most of the decision.

## The method

**One read tells you nothing.** Availability is a rate, not a state. Poll every
15 minutes for a week (672 samples/DC), then pick from the distribution.

```bash
export RUNPOD_API_KEY=...

./runpod_watch.py datacenters                    # what's even eligible
./runpod_watch.py poll --log ~/rp.jsonl --cross-check
./runpod_watch.py report --log ~/rp.jsonl --since 7d --gpu-filter a100 --pairs
```

Cron it:

```
*/15 * * * * RUNPOD_API_KEY=... $HOME/runpod_watch.py poll \
    --log $HOME/rp.jsonl --cross-check >> $HOME/rp.err 2>&1
```

The log is append-only JSONL of raw API values. The script never interprets an
availability string — it records whatever the API returns and lets `report`
bucket it, so an undocumented enum value can't silently become "unavailable".

## The part that actually answers the question

`--pairs` exists because **this is a portfolio problem, not a ranking problem.**

Taking the two highest-availability DCs is the obvious move and often the wrong
one. Same-metro pairs (US-MO-1/US-MO-2, US-NC-1/US-NC-2) plausibly share supply
and go dry together — two volumes there buy you much less failover than the
marginal rates suggest. The number that should decide this is
**P(at least one usable)**, computed on aligned timestamps.

`--pairs` prints that, next to the independence benchmark
`1 − (1−p_a)(1−p_b)`. A negative gap means the outages co-occur and the two DCs
are near-substitutes. On a synthetic test where MO-1 and MO-2 were deliberately
coupled, the top two solo rates (US-KS-2 64%, US-MO-1 59%) did *not* form the
best pair — EU-RO-1 + US-MO-1 won at 84.4% despite EU-RO-1 ranking third solo.

Decision rule: **highest `either`, and require a non-negative gap.** If the top
pair is flagged correlated, drop to the next one.

## Eligible datacenters (starting universe)

15 DCs expose the S3-compatible API for network volumes, so they certainly
support volumes. Treat this as a lower bound — S3 support is a subset of volume
support, and the catalog endpoint is authoritative:

```
EU-CZ-1  EU-RO-1  EUR-IS-1  EUR-NO-1  US-CA-2  US-GA-2  US-IL-1  US-KS-2
US-MD-1  US-MO-1  US-MO-2  US-NC-1   US-NC-2  US-NE-1  US-WA-1
```

**Priors to test, not to act on.** US-KS-2 is RunPod's example DC throughout
their own docs, which usually means large and well-stocked. EU-RO-1 is their
oldest large European site and sits on a different continent and supply chain
from anything in the US, so it's the most likely candidate to be *decorrelated*
with a US primary. `US-KS-2 + EU-RO-1` is where I'd start the week — and latency
is irrelevant to your batch workload, so the EU leg costs you nothing but a
slower interactive shell.

## Two things about your plan worth pushing on

**1. Mirror them; don't specialize them.** Holding the 9B stack in DC-A and the
27B stack in DC-B gives you parallel runs with zero sync — but zero failover,
against exactly the failure mode that started this. If DC-B dries up, the 27B
work stalls and you're rebuilding anyway. Mirrors give you parallelism *and*
failover, and the S3 API means syncing is `aws s3 sync` from your Mac against
`s3api-us-ks-2.runpod.io` / `s3api-eu-ro-1.runpod.io` — no pod, no GPU hours.

**2. Don't persist the weights.** Your notes have the volume holding
weights + SAEs + oracle. Weights are ~72GB of the total (9B ≈ 18, 27B ≈ 54) and
are the one thing that's trivially re-fetchable from HF at pod-start in ten-odd
minutes. The SAEs, oracle artifacts, code and results are what actually justify
$0.07/GB/mo.

Mirroring everything is roughly $12–17/mo. Mirroring only the irreplaceable
parts is roughly $3–4/mo — and it's *better for your gates*, not just cheaper:
pulling weights from a pinned HF revision every run means G0a/G0b get exercised
on every single run instead of trusting a cached copy that no gate ever
re-checks. A silently corrupted or drifted cached weight file is precisely the
kind of thing your framework is built to catch, and right now the cache is the
one place it can't look.

## On the cross-check

`--cross-check` also records GraphQL `stockStatus`, which the community tooling
that wraps it openly documents as unreliable. It's in there as a disagreement
detector, not a second opinion: if `stockStatus` says `IN_STOCK` while the
per-DC catalog table shows little usable capacity, that's the known flakiness,
and the catalog numbers are the ones to trust. Worth logging both for a week
just to see how badly they diverge — that's a cheap, self-contained
known-answer check on the data source before you let it pick infrastructure.
