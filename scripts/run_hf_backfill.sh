#!/bin/bash
# One Hugging Face fill week per process. Exit 2 means the week is on R2; exit 0 means
# the backfill has reached 2024-10-01. Any other exit is retried, and a restarted
# process resumes the unfinished week from data/periods/recent_2026/hf_load_manifest.json.
cd "$(dirname "$0")/.." || exit 1
while true; do
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) next week ==="
  python -m src.data.hf_fills
  code=$?
  if [ "$code" -eq 0 ]; then
    echo "backfill finished"
    exit 0
  fi
  if [ "$code" -eq 2 ]; then
    continue
  fi
  echo "week failed with ${code}; retry in 30s"
  sleep 30
done
