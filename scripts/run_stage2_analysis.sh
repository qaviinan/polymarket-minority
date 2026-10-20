#!/bin/zsh
# Stage 2 analysis on already-verified R2 slices. Does not call Goldsky/turbo.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
export MPLBACKEND=Agg
export PERIOD_ID=recent_2026
export PNL_MARKET_UNIVERSE=resolved_by_fill_cutoff
export OIB_MARKET_UNIVERSE=resolved_by_fill_cutoff
export OIB_EVAL_WEEK=all
export CLASSIFICATION_FDR_Q=0.05
export STAGE2_EXCLUDE_COMBOS=1
# Empty (not unset) so python-dotenv cannot restore 3w/1w flags from .env.
export MINORITY_LABEL_SPLIT=""
export ANALYSIS_USE_LAST_N_DAYS=""
export ANALYSIS_BLOCK_TS_MIN=""
export ANALYSIS_BLOCK_TS_MAX=""
# Materialize after publish; then point DuckDB at the local mirror (no Goldsky).
export LOCAL_R2_MIRROR_DEST="data/periods/recent_2026/r2_mirror"

mkdir -p logs/periods/recent_2026
exec > >(tee -a logs/periods/recent_2026/analysis_run.log) 2>&1

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) republish metadata from local Gamma cache (no Goldsky) ==="
python -c "
from src.lib.duck import get_duck, load_env
from src.data.stage2_metadata import publish_metadata
load_env()
print(publish_metadata(get_duck()))
"

echo "=== materialize period to local mirror (one R2 read) ==="
python scripts/materialize_period.py
export LOCAL_R2_MIRROR="${LOCAL_R2_MIRROR_DEST}"
echo "LOCAL_R2_MIRROR=${LOCAL_R2_MIRROR}"

echo "=== partition QA ==="
python -m src.analysis.partition

echo "=== verify_ingest (period-scoped R2; no Goldsky) ==="
python -m src.data.verify_ingest

PRIMARY_LABELS="logs/periods/recent_2026/coverage_audit/account_classification_primary.parquet"
if [[ -f "$PRIMARY_LABELS" ]]; then
  echo "=== skip coverage_audit (primary labels exist); publish restricted labels ==="
  python -c "
from src.lib.duck import get_duck, r2_uri, load_env
from src.lib.period import logs_path
load_env()
src = logs_path('coverage_audit', 'account_classification_primary.parquet')
uri = r2_uri('labels', 'account_classification_stage1_restricted.parquet')
con = get_duck()
con.execute(f\"COPY (SELECT * FROM read_parquet('{src}')) TO '{uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)\")
print('wrote', uri)
"
else
  echo "=== coverage_audit (two sign-randomization universes) ==="
  python -m src.analysis.coverage_audit
fi

echo "=== multiple_testing ==="
python -m src.analysis.multiple_testing

echo "=== gate-b audits ==="
python -m src.analysis.population_audit
python -m src.analysis.sign_construct_audit
python -m src.analysis.market_closure
python -m src.analysis.composition_audit
python -m src.analysis.trader_sanity
python -m src.analysis.stage1_gate

echo "=== publish FDR labels for OIB / favorites ==="
python -c "
from src.lib.duck import get_duck, r2_uri
from src.lib.period import logs_path
import pandas as pd
df = pd.read_parquet(logs_path('multiple_testing', 'account_classification_primary_fdr.parquet'))
if 'trader_group_fdr_q05' in df.columns:
    df = df.copy()
    df['trader_group'] = df['trader_group_fdr_q05']
con = get_duck()
con.register('_l', df)
uri = r2_uri('labels', 'account_classification.parquet')
con.execute(f\"COPY _l TO '{uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)\")
print('wrote', uri, len(df))
"

echo "=== favorites + OIB ==="
python -m src.analysis.favorite_buying
python -m src.analysis.oib_regression

echo "=== skilled profile ==="
python -m src.analysis.skilled_profile

echo "=== event-split persistence (single seed) ==="
EVENT_OOS_SEED=42 EVENT_OOS_EXPORT_R2=true python -m src.analysis.event_split_persistence

echo "=== stage2 compare / report ==="
python -m src.analysis.stage2_compare

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) DONE ==="
