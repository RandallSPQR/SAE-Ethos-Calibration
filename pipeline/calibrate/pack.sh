#!/usr/bin/env bash
# Pack the committed pipeline + scenarios for a pod, stamping the code identity the manifest records.
# Usage: bash pipeline/calibrate/pack.sh /path/out.tgz   (run from the repo root)
set -euo pipefail
OUT=${1:-/tmp/repo.tgz}
[ -z "$(git status --porcelain -uno -- pipeline scenarios | grep -v CLAUDE.md || true)" ] || { echo "uncommitted changes under pipeline/ or scenarios/; commit first (the manifest records HEAD)"; exit 2; }
git rev-parse HEAD > pipeline/GIT_COMMIT
git rev-parse HEAD > scenarios/GIT_COMMIT
# COPYFILE_DISABLE: no macOS AppleDouble "._*" entries; --no-xattrs: no com.apple.provenance headers.
# Unpack on the pod with: tar --no-same-owner --no-same-permissions -xzf  (network volumes refuse chown to the Mac uid).
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$OUT" pipeline/GIT_COMMIT scenarios/GIT_COMMIT $(git ls-files pipeline scenarios)
rm -f pipeline/GIT_COMMIT scenarios/GIT_COMMIT
echo "packed $(git rev-parse --short HEAD) -> $OUT"
