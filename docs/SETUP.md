# Stage 1 setup: R2, Goldsky, DuckDB, Python

This document is the single source of truth for **prerequisites**, **one-time provisioning**, and **day-to-day orchestration** of the informed-minority replication pipeline (Oct–Dec 2024 sample).

## 1. Prerequisites

| Requirement | Purpose |
|-------------|---------|
| **Cloudflare account** (free) | R2 object storage (10 GB free, no egress fees). |
| **Goldsky account** + CLI | Turbo Pipelines to backfill `polymarket.order_filled` v2 into R2. |
| **Python 3.11+** | Analysis (`duckdb`, `pandas`, `linearmodels`, …). |
| **uv** (recommended) or **pip** | Install [`pyproject.toml`](../pyproject.toml). |
| **Polygon JSON-RPC URL** (often needs a provider; see Step C) | Map calendar dates → `block_number` range for Goldsky fast-scan. |

Optional: **Jupyter** for [`notebooks/stage1.ipynb`](../notebooks/stage1.ipynb).

## 2. Cloudflare R2 setup

1. Log in to [Cloudflare Dashboard](https://dash.cloudflare.com) → **R2** (object storage) → **Create bucket** (e.g. `polymarket-stage1`).
2. In the **R2** page, find **Account details** → **Manage** next to **API Tokens** (this is **R2’s S3 API**, not *My Profile → API Tokens* for the general Cloudflare API). Official steps: [R2 S3 API tokens](https://developers.cloudflare.com/r2/api/s3/tokens/).
3. Click **Create Account API token** or **Create User API token**, pick **Object Read & Write** (or **Admin Read & Write**), optionally scope to your bucket, then **Create**.
4. On the **success** screen, Cloudflare shows **two** values you must copy immediately (the secret is shown **once**):
   - **Access Key ID** — in some UIs this is labeled **Client ID** or is the token’s **id**.
   - **Secret Access Key** — may be labeled **Client Secret**; this is **not** the same as a single “API token” string from the general Cloudflare API.
5. If you only ever see **Account ID** and one long **API token** string, you are usually in the **wrong** flow (e.g. global Cloudflare API tokens, or Workers/R2 bindings). Go back to **R2 → Account details → Manage (API Tokens)** and complete **Create … API token** until both Access Key ID and Secret Access Key appear. If you already dismissed that screen, **revoke** the token and create a new one — you cannot retrieve the secret again.
6. **Account ID** (32 hex) is on the same R2 overview / account area; it is not a secret. S3 endpoint for tools in this repo: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.

Put them in `.env` (copy from [`.env.example`](../.env.example)):

```bash
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=polymarket-stage1
R2_REGION=auto
R2_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
```

### “Token value” vs S3 keys (newer Cloudflare UI)

Cloudflare may show **four** things: **Account ID**, **Access Key ID**, **Secret Access Key**, and **Token value**.

- **For this repo** (DuckDB `r2://…`, Goldsky’s `s3` secret, any normal S3 client): use **only**  
  **Access Key ID** → `R2_ACCESS_KEY_ID`  
  **Secret Access Key** → `R2_SECRET_ACCESS_KEY`  
  plus **Account ID** in the endpoint URL (`R2_ENDPOINT` / `R2_ACCOUNT_ID`).  
  Those are what the **S3-compatible XML API** uses to sign `PutObject` / `GetObject` requests.

- **Token value** is the underlying **Cloudflare API token string** (often used as a **Bearer** token or in **Cloudflare’s own REST APIs** / dashboard “validate” flows). It is **not** a second secret you paste into `R2_SECRET_ACCESS_KEY` alongside the Secret Access Key. **Do not** put the Token value into Goldsky’s `secretAccessKey` field if you already have a proper **Secret Access Key** — use the Secret Access Key there.

- If you ever create credentials **only** via the [Create Token API](https://developers.cloudflare.com/r2/api/s3/tokens/#create-api-tokens-via-api), the docs define a different mapping: Access Key ID = token **id**, Secret Access Key = **SHA-256** of the token **value**. The dashboard “new method” that still shows both **Secret Access Key** and **Token value** is giving you the S3 pair **and** the raw token for Cloudflare-side use; for DuckDB/Goldsky S3, stick to the **Secret Access Key** line.

Store the Token value somewhere safe if Cloudflare asks you to keep it for account verification or other Cloudflare products; **this Polymarket pipeline does not read it.**

### Local R2 health check (DuckDB)

From repo root:

```bash
cp .env.example .env   # then edit with real values
python -m src.lib.duck --healthcheck
```

This writes a tiny Parquet to `r2://$R2_BUCKET/healthcheck/probe.parquet` and reads it back.

To let **Goldsky** write to the same bucket, register the same keys as a Goldsky secret (see **§3.3** below).

## 3. Goldsky — first-time setup

Goldsky runs the **Turbo Pipeline** that reads Polymarket’s hosted dataset and writes **Parquet** to your **R2** bucket. You need: a Goldsky account, a **project API key** for the CLI, the CLI installed, and an **S3 secret** in Goldsky that matches your R2 keys.

### 3.1 Account and API key

1. Sign up / log in at [app.goldsky.com](https://app.goldsky.com).
2. Open **Project settings** (or your project dashboard) and create an **API key** used by the CLI. You will paste this into `goldsky login`.

### 3.2 Install CLI and log in

```bash
curl https://goldsky.com | sh
goldsky login
```

Paste the API key when prompted. Confirm with `goldsky` or `goldsky --help` that the binary works.

### 3.3 Store R2 credentials as a Goldsky secret (S3 shape)

The pipeline YAML references `secret_name: R2_GOLDSKY_SECRET` (override with `GOLDSKY_R2_SECRET_NAME` in `.env` if you rename it).

**Recommended:** sync from `.env` (no hand-pasted JSON in the shell):

```bash
python -m src.data.sync_goldsky_r2_secret --dry-run   # optional: see commands
python -m src.data.sync_goldsky_r2_secret
```

This runs `goldsky secret update <name> --value …` first (idempotent), then `goldsky secret create --name … --value …` if the secret did not exist yet. Requires `goldsky` on `PATH` and a prior `goldsky login`.

**Manual** (same effect):

Goldsky CLI expects `--value` to be JSON that includes a **`type` discriminator** (the API validates `anyOf` schemas). For R2/S3 use `type: "s3"` plus keys:

```bash
goldsky secret create \
  --name R2_GOLDSKY_SECRET \
  --value '{"type":"s3","accessKeyId":"<R2_ACCESS_KEY_ID>","secretAccessKey":"<R2_SECRET_ACCESS_KEY>","region":"auto"}' \
  --description "R2 S3 credentials for Polymarket Turbo pipeline"
```

Use one line if your shell mangles backslashes:

```bash
goldsky secret create --name R2_GOLDSKY_SECRET --value '{"type":"s3","accessKeyId":"...","secretAccessKey":"...","region":"auto"}'
```

Confirm it exists: `goldsky secret list`. Then set `GOLDSKY_R2_SECRET_NAME=R2_GOLDSKY_SECRET` in `.env` (or whatever you passed to `--name`).

The rendered pipeline YAML (`goldsky/order_filled_backfill.rendered.yaml`) sets `endpoint: https://<ACCOUNT_ID>.r2.cloudflarestorage.com` on the `s3_sink` so Goldsky talks to R2, not AWS.

### 3.4 Deploy and monitor a pipeline

From the repo, after `python -m src.data.render_pipeline_yaml`:

```bash
goldsky turbo apply goldsky/order_filled_backfill.rendered.yaml
goldsky turbo logs polymarket-order-filled-r2
goldsky turbo inspect goldsky/order_filled_backfill.rendered.yaml
```

Use the `name:` field in the YAML if your pipeline name differs. More detail: [Turbo Pipelines quickstart](https://docs.goldsky.com/turbo-pipelines/quickstart) and [S3 sink](https://docs.goldsky.com/turbo-pipelines/sinks/s3).

## 4. End-to-end orchestration (runbook)

Run from repository root with `.env` loaded (export vars or use `direnv` / `source .env`).

### Step A — Install

```bash
uv sync
# or: pip install -r requirements.txt && pip install -e .
# or: pip install -e ".[dev]"
```

### Step B — Market metadata → R2

Fetches **closed** Gamma `/events` with `end_date_min` / `end_date_max` in **2024-10-01 … 2024-12-31**, builds `events`, `markets`, `outcome_tokens` Parquet under `metadata/` on R2.

```bash
python -m src.data.load_markets
```

**Debug:** `logs/load_markets_*.jsonl`, `logs/load_markets/*.parquet`, sample events JSON.

### Step C — Render Goldsky pipeline (token filter + block range)

Resolves Polygon **block numbers** for Oct 1 and Dec 31 2024 via `POLYGON_RPC_URL`, reads `metadata/outcome_tokens.parquet` from R2 for the `asset IN (...)` list, writes:

- `goldsky/order_filled_backfill.rendered.yaml`
- `logs/goldsky_pipeline/rendered.yaml`

```bash
python -m src.data.render_pipeline_yaml
```

`render_pipeline_yaml` tries **`POLYGON_RPC_URL` first**, then optional **`POLYGON_RPC_FALLBACK_URLS`** (comma-separated), then built-in public endpoints. **`https://polygon-rpc.com` often returns `401`** without a provider key — prefer **`POLYGON_RPC_URL=https://polygon-bor.publicnode.com`** or your own Alchemy/Infura URL (see [.env.example](../.env.example)). To **only** use your URL (no fallbacks): `POLYGON_RPC_STRICT=true`.

If binary search fails, widen bounds:

```bash
export POLYGON_BLOCK_SEARCH_LO=56000000
export POLYGON_BLOCK_SEARCH_HI=72000000
python -m src.data.render_pipeline_yaml
```

### Step D — Deploy backfill

```bash
goldsky turbo apply goldsky/order_filled_backfill.rendered.yaml
goldsky turbo logs polymarket-order-filled-r2   # pipeline name from YAML
```

Data lands as Hive-partitioned Parquet:

`r2://$R2_BUCKET/order_filled/dt=YYYY-MM-DD/batch_*.parquet`

### Step E — Wait for stable row count (optional heuristic)

Polls R2 until the row count is unchanged for `BACKFILL_STABLE_POLLS` consecutive polls (default 5 × 60s):

```bash
python -m src.data.wait_for_backfill
```

### Step F — Verify ingest

```bash
python -m src.data.verify_ingest
```

**Debug:** daily counts, maker/taker split, Gamma vs on-chain volume ratios, top users, orphan assets.

### Step G — Maker/taker partition (debug-only module)

```bash
python -m src.analysis.partition
```

### Step H — PnL

```bash
python -m src.analysis.pnl
```

Writes `derived/pnl_account.parquet`, `derived/pnl_user_asset.parquet` on R2.

### Step I — Event-level sign randomization

```bash
# Optional: smaller iteration count for smoke tests
export SIGN_RANDOMIZATION_ITERATIONS=2000
python -m src.analysis.sign_randomization
```

Writes `derived/sign_randomization_summary.parquet` on R2; PNGs under `logs/sign_randomization/`.

### Step J — Classification

```bash
python -m src.analysis.classification
```

Writes `labels/account_classification.parquet` on R2.

### Step K — OIB + pricing-error regression

```bash
python -m src.analysis.oib_regression
```

**Debug:** `logs/oib_regression/regression_*.parquet`, scatter PNGs.

### Step L — Notebook narrative

```bash
jupyter lab notebooks/stage1.ipynb
```

The notebook re-runs the analysis path and aggregates `logs/*.jsonl` into a **Run Report** markdown cell.

## 5. Cost & quota (rough)

- **R2:** Stage 1 sample usually **&lt; 10 GB** storage; pipeline writes are **Class A** operations — stay within free tier for a first backfill if you filter `asset IN (...)`.
- **Goldsky:** Turbo backfills consume project credits; bounded block range + token filter minimizes cost.
- **Local:** DuckDB is in-process; peak RAM during sign randomization scales with `(n_iterations × max_events_per_user)` per user loop (still modest for filtered universes).

## 6. Reading debug artifacts

| Artifact | Contents |
|----------|----------|
| `logs/<step>_*.jsonl` | One JSON object per line: `step`, `phase`, `metric`, `value`, `notes`, plus **assertion** rows (`level`: `green` / `yellow` / `red`). |
| `logs/<step>/*.parquet` | Diagnostic tables (histograms, tails, panel heads). |
| `logs/<step>/*.png` | Null distributions, OIB scatter + LOWESS. |

Load JSONL in DuckDB:

```sql
SELECT * FROM read_json_auto('logs/load_markets_*.jsonl');
```

**Assertions** are *soft* checks (never crash the pipeline):  
- **Green:** looks healthy.  
- **Yellow:** review (e.g. volume mismatch tolerance).  
- **Red:** likely misconfiguration (R2 auth, wrong block range, broken randomization).

## 7. Troubleshooting

### DuckDB `httpfs` / R2 `403` or auth errors

- Confirm `CREATE SECRET ... TYPE R2 ... REGION 'auto'` (DuckDB ≥ 1.1 often **requires** `REGION` for R2). See [DuckDB Cloudflare R2](https://duckdb.org/docs/guides/network_cloud_storage/cloudflare_r2_import.html) and [issue #14178](https://github.com/duckdb/duckdb/issues/14178).

### Polygon RPC `401 Unauthorized` (Step C)

- `https://polygon-rpc.com` frequently requires an API key now. Set **`POLYGON_RPC_URL`** to a URL that accepts unsigned JSON-RPC for your volume (e.g. [publicnode](https://polygon-bor.publicnode.com), or **Alchemy / Infura / Ankr** with your key in the path).
- The script auto-retries other public hosts unless **`POLYGON_RPC_STRICT=true`**.

### R2 paths like `dt=ERROR%3A%20Cast%20error...`

Goldsky Turbo SQL is **Apache DataFusion**, not Spark. `CAST(block_timestamp AS DATE)` treats a **Unix-seconds integer** incorrectly and can produce a partition value that is literally an error string.

The rendered pipeline uses **`CAST(to_timestamp_seconds(CAST(block_timestamp AS BIGINT)) AS DATE) AS dt`**. After fixing, **re-`render_pipeline_yaml`**, **`goldsky turbo apply`** the new YAML, and delete the bad `order_filled/dt=ERROR...` prefix in the R2 bucket if you do not want stray objects.

### Goldsky pipeline stalls or returns no rows

- Narrow or widen **`block_number` filter** on the source (fast-scan requires block range; timestamp-only filter is not supported on source per Goldsky Polymarket docs).
- Verify **`asset IN (...)`** token IDs match the string form used on-chain (decimal string vs hex — `verify_ingest` flags orphan joins).

### `CAST(block_timestamp AS DATE)` in pipeline SQL fails

Goldsky’s SQL dialect may differ; replace with their documented pattern, e.g. `CAST(from_unixtime(block_timestamp) AS DATE) AS dt`, then re-`turbo apply`.

### Skilled winner share ≫ 6%

- Most common bug: randomization at **trade** or **market** level instead of **event** level. Our implementation groups PnL by `event_id` from Gamma metadata — verify `markets.event_id` join path.

### `PanelOLS` rank deficiency

If groups are sparse, drop redundant OIB columns or merge rare groups — regression module saves failure reason in JSONL.

## 8. Repository map

| Path | Role |
|------|------|
| [`src/data/load_markets.py`](../src/data/load_markets.py) | Gamma → R2 metadata |
| [`src/data/sync_goldsky_r2_secret.py`](../src/data/sync_goldsky_r2_secret.py) | Push R2 keys from `.env` → Goldsky secret |
| [`src/data/render_pipeline_yaml.py`](../src/data/render_pipeline_yaml.py) | Render Turbo YAML |
| [`src/data/wait_for_backfill.py`](../src/data/wait_for_backfill.py) | Poll R2 row counts |
| [`src/data/verify_ingest.py`](../src/data/verify_ingest.py) | Ingest QA |
| [`src/analysis/partition.py`](../src/analysis/partition.py) | Maker/taker views |
| [`src/analysis/pnl.py`](../src/analysis/pnl.py) | Realized PnL |
| [`src/analysis/sign_randomization.py`](../src/analysis/sign_randomization.py) | Event-level null |
| [`src/analysis/classification.py`](../src/analysis/classification.py) | Labels |
| [`src/analysis/oib_regression.py`](../src/analysis/oib_regression.py) | OIB + Δ error |
| [`goldsky/order_filled_backfill.yaml`](../goldsky/order_filled_backfill.yaml) | Human stub |
| `goldsky/order_filled_backfill.rendered.yaml` | **Generated** — do not hand-edit |
