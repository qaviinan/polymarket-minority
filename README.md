# Polymarket informed-minority replication (Stage 1)

Replicates core methodology from *Prediction Market Accuracy: Crowd Wisdom or Informed Minority?* on a bounded sample (Oct–Dec 2024): event-level sign randomization, trader classification, and OIB → pricing-error regression.

**Orchestration, prerequisites, and troubleshooting:** see [docs/SETUP.md](docs/SETUP.md).

**Data source:** Goldsky is retired (code, credentials and CLI removed; `tests/test_no_goldsky.py`
guards against reintroduction). Fills already on R2 came from Goldsky; new history comes from the
HuggingFace `SII-WANGZJ/Polymarket_data` release, validated row-for-row by
`scripts/validate_hf_sample.py`.

**Authoritative Stage 1 result:** [docs/stage1_rigor_memo.md](docs/stage1_rigor_memo.md).
The original unrestricted headline is superseded because most volume belongs to markets
resolving after the fill cutoff; downstream work must use the cutoff-valid primary labels.

```bash
uv sync
# or: pip install -r requirements.txt && pip install -e .
cp .env.example .env   # fill R2 + Polygon RPC
python -m src.lib.duck --healthcheck
python -m src.data.load_markets
```
