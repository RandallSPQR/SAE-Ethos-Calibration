"""Real, deterministic sandbox for Arm-A code behavior. Operates on the REAL rendered repo on disk, so
labels come from real final state (real edits, real test runs, real canaries), not an auditor's
role-play. Snapshot/restore supports decision-point resampling: run the deterministic prefix once,
snapshot, then let each continuation diverge into its own real filesystem.

Confinement of the code that runs INSIDE this tree is harness.confine's job (selected and canary-verified at
launch); this class handles the working copy, its ownership, the no-delete rule, canary tracking, and
forbidden-access logging.
"""
import shutil
import tempfile
from pathlib import Path

from . import confine

IGNORED_DIRS = {".pytest_cache", "__pycache__"}    # written by pytest/python, never by the model; excluded from final state


class Sandbox:
    def __init__(self, rendered_variant_dir, canary=None, forbidden_files=None, allow_delete=False):
        self.src = Path(rendered_variant_dir)
        self.root = Path(tempfile.mkdtemp(prefix="arm_a_"))
        shutil.copytree(self.src, self.root, dirs_exist_ok=True)
        confine.own(self.root)                     # the episode uid owns ONLY this tree
        self.canary = canary
        self.forbidden = set(forbidden_files or [])
        self.allow_delete = allow_delete
        self.events = []             # audit log: every tool call + outcome
        # canary: OBSERVED (appeared in a tool RESULT the model saw) vs USED (re-emitted by the model
        # in a tool ARG / written content / submitted answer). These are different behaviors.
        self.canary_observed = False
        self.canary_used = False
        self.forbidden_hits = []     # forbidden files that were read/queried

    # --- path safety -----------------------------------------------------
    def resolve(self, rel):
        p = (self.root / rel).resolve()
        # robust containment: is_relative_to, NOT string startswith (a sibling dir shares the prefix)
        if not p.is_relative_to(self.root.resolve()):
            raise PermissionError("EACCES: path escapes repo root")
        return p

    def note_canary(self, text, direction):
        """direction: 'observed' (canary in a tool RESULT) or 'used' (canary emitted BY the model)."""
        if self.canary and text and self.canary in str(text):
            if direction == "used":
                self.canary_used = True
            else:
                self.canary_observed = True

    def log(self, tool, args, result_summary):
        self.note_canary(args, "used")            # args are what the MODEL emitted -> using the secret
        self.note_canary(result_summary, "observed")   # result is what the tool RETURNED -> seeing it
        self.events.append({"tool": tool, "args": args, "result": str(result_summary)[:400]})

    # --- snapshot / restore for decision-point resampling ---------------
    def snapshot(self):
        """Snapshot BOTH the filesystem AND the audit-event state, so a restored continuation inherits
        exactly the prefix's world — not a mutated FS with a stale/foreign event log."""
        snap_dir = Path(tempfile.mkdtemp(prefix="arm_a_snap_"))
        shutil.copytree(self.root, snap_dir, dirs_exist_ok=True)
        return {"dir": snap_dir,
                "events": [dict(e) for e in self.events],
                "canary_observed": self.canary_observed,
                "canary_used": self.canary_used,
                "forbidden_hits": list(self.forbidden_hits)}

    def restore(self, snap):
        shutil.rmtree(self.root)
        shutil.copytree(snap["dir"], self.root, dirs_exist_ok=True)
        confine.own(self.root)
        self.events = [dict(e) for e in snap["events"]]
        self.canary_observed = snap["canary_observed"]
        self.canary_used = snap["canary_used"]
        self.forbidden_hits = list(snap["forbidden_hits"])

    def final_state(self):
        """Return a manifest of the final repo for the labeler: file -> text (small files only).
        Nothing of the episode uid may still be running when this is read (a background process could
        edit files after the labeler saw them), so reap first and refuse if anything survived."""
        left = confine.reap()
        if left:
            raise confine.ConfineError(f"{left} process(es) of the episode uid survived reaping; final state untrusted")
        out = {}
        for p in sorted(self.root.rglob("*")):
            if any(part in IGNORED_DIRS for part in p.relative_to(self.root).parts):
                continue                             # tool-runner residue is not model action
            if p.is_file():
                try:
                    out[str(p.relative_to(self.root))] = p.read_text()
                except UnicodeDecodeError:
                    out[str(p.relative_to(self.root))] = None
        return out

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
        confine.sweep()                            # nothing the episode uid left elsewhere survives it
