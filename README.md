# Polymarket informed-minority replication (Stage 1)

Replicates core methodology from *Prediction Market Accuracy: Crowd Wisdom or Informed Minority?* on a bounded sample (Oct–Dec 2024): event-level sign randomization, trader classification, and OIB → pricing-error regression.

**Orchestration, prerequisites, and troubleshooting:** see [docs/SETUP.md](docs/SETUP.md).

```bash
uv sync
# or: pip install -r requirements.txt && pip install -e .
cp .env.example .env   # fill R2 + Goldsky
python -m src.data.sync_goldsky_r2_secret   # push R2 keys to Goldsky (after goldsky login)
python -m src.lib.duck --healthcheck
python -m src.data.load_markets
```
