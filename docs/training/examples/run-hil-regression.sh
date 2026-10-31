#!/usr/bin/env bash
# Training example: executes existing loopback tests on a reserved lab board.
# No deployment, reprogramming, reboot, or service changes are performed.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
cd "$repo_root"
ssh_key=${CORA_SSH_KEY:-"$HOME/.ssh/cora_codex_ed25519"}
mkdir -p "$repo_root/results"
run_dir=$(mktemp -d "$repo_root/results/hil-training-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
printf 'Evidence directory: %s\n' "$run_dir"

# One runner host must own this board. This is not a distributed board lease.
exec 9>"${TMPDIR:-/tmp}/cora-z7-10-training-hil.lock"
flock -n 9 || { printf 'Board reserved by another local run\n' >&2; exit 75; }
ssh_options=(
  -i "$ssh_key" -o IdentitiesOnly=yes -o BatchMode=yes
  -o ConnectTimeout=8 -o ServerAliveInterval=5 -o ServerAliveCountMax=2
  -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no
)
target=petalinux@192.168.10.2
# Host-key exception above applies only to this fixed isolated lab endpoint.

date -u +%FT%TZ > "$run_dir/started-utc.txt"
git rev-parse HEAD > "$run_dir/source-commit.txt"
git status --porcelain=v1 > "$run_dir/source-status.txt"
git diff --binary HEAD > "$run_dir/tracked-changes.patch"
sha256sum platforms/cora-z7-10/platform.json \
  platforms/cora-z7-10/platform-manifest.json > "$run_dir/metadata-sha256.txt"
printf 'case\tresult\texit_code\n' > "$run_dir/summary.tsv"

# Remote timeout bounds the test itself, even if the client loses its link.
# GNU timeout below bounds the host-side SSH process. Neither repairs a
# kernel task blocked in uninterruptible I/O; inspect the board after timeout.
preflight='set -eu
command -v timeout
command -v cora-axis-loopback-test
test -c /dev/cora-dsp0
test -r /dev/cora-dsp0
test -w /dev/cora-dsp0
id; hostname; uname -a
cat /proc/cmdline
cat /proc/uptime
ps
timeout -k 2 5 true'
if timeout -k 5s 30s ssh "${ssh_options[@]}" "$target" "$preflight" \
    > "$run_dir/preflight.log" 2>&1; then
  printf 'preflight\tPASS\t0\n' >> "$run_dir/summary.tsv"
else
  rc=$?
  printf 'preflight\tBLOCKED\t%s\n' "$rc" >> "$run_dir/summary.tsv"
  printf 'BLOCKED: inspect %s/preflight.log\n' "$run_dir" >&2
  exit 2
fi

run_case() {
  local name=$1 frames=$2 rate=$3 rc=0
  local log="$run_dir/$name.log"
  # Arguments below are fixed numeric values supplied by this script.
  timeout -k 5s 150s ssh "${ssh_options[@]}" "$target" \
    "timeout -k 5 120 cora-axis-loopback-test --frames $frames --sample-rate $rate" \
    > "$log" 2>&1 || rc=$?
  if [[ $rc -eq 0 ]] && grep -Fxq \
      'PASS Cora AXI-stream acoustic loopback' "$log"; then
    printf '%s\tPASS\t0\n' "$name" >> "$run_dir/summary.tsv"
  else
    printf '%s\tFAIL\t%s\n' "$name" "$rc" >> "$run_dir/summary.tsv"
    printf 'FAIL: inspect %s\n' "$log" >&2
    return 1
  fi
}

# Fail fast: a DMA failure must be investigated before another transaction.
run_case loopback-smoke 32 250000
run_case loopback-repeat-1 2048 250000
run_case loopback-repeat-2 2048 250000
run_case loopback-repeat-3 2048 250000
date -u +%FT%TZ > "$run_dir/completed-utc.txt"
printf 'PASS: loopback integrity subset; evidence: %s\n' "$run_dir"
# A zero exit is limited to these cases. It is not full release qualification.
