# T2 isolation probe, 2026-09-17 19:33 UTC, RunPod secure L40S, EU-NL-1 (pod pp35cib2sejo0k, ~8 min, $1.09/hr)

Container: kernel 6.8.0-124 x86_64, root, Docker default capability set (CapBnd a80425fb, no CAP_SYS_ADMIN),
Seccomp mode 2 already active. Both namespace doors locked: `unshare -Urn` -> EPERM; `bwrap --unshare-all`
-> "No permissions to create new namespace" (with and without a uid map). So the bwrap backend is not
available on this box.

`seccomp_uid` backend: **9/9 canaries** (fake HF_TOKEN and RUNPOD_POD_ID were set in the parent env):
connect -> EPERM; root-owned parent-level file -> EACCES; no secret keys in the child env; fork bomb hit
the limit at 63 (EAGAIN); over-time sleep killed; episode dir + TMPDIR writable by uid 61000; pytest, yaml,
sqlite3 importable from /workspace/venv; trivial test green; a setsid'd daemon did not survive the episode
(heartbeat frozen after reap). This is the box for T2 (`calibrate/run_t2.sh` will select it and record
`isolation: {mechanism: seccomp_uid, canaries: 9/9}` in the manifest).

Not exercised here: the full bootstrap (vLLM venv, weights) and the HF login; the probe used a venv with
pytest/pyyaml/openai only.
