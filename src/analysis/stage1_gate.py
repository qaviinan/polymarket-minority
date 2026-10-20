"""Aggregate Stage 1 audits into explicit gates and a reproducible rigor memo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from src.lib.debug import debug_step
from src.lib.period import logs_path, period_id


def _latest_rows(step: str) -> list[dict[str, Any]]:
    files = sorted(logs_path().glob(f"{step}_*.jsonl"))
    if not files:
        return []
    return [json.loads(line) for line in files[-1].read_text().splitlines() if line.strip()]


def _metric(step: str, name: str, default: Any = None) -> Any:
    for row in reversed(_latest_rows(step)):
        if row.get("metric") == name:
            return row.get("value")
    return default


def _assertion_level(step: str, name: str) -> str | None:
    for row in reversed(_latest_rows(step)):
        if row.get("phase") == "assertion" and row.get("name") == name:
            return str(row.get("level"))
    return None


def _fmt_counts(path: Path) -> str:
    if not path.exists():
        return "missing"
    df = pd.read_parquet(path)
    return ", ".join(f"{r.trader_group}={int(r.n)}" for r in df.itertuples())


def main() -> None:
    required = [
        "coverage_audit",
        "sign_construct_audit",
        "multiple_testing",
        "population_audit",
        "market_closure",
        "composition_audit",
        "trader_sanity",
    ]
    missing = [step for step in required if not _latest_rows(step)]
    with debug_step("stage1_gate") as dbg:
        if missing:
            raise RuntimeError(f"Missing completed Stage 1 audit logs: {missing}")

        statistical = json.loads(
            logs_path("multiple_testing", "gate_a_statistical.json").read_text()
        )
        pid = period_id()
        from src.analysis.partition import minority_train_test_split_enabled

        def _ok(step: str, name: str, *, allow_yellow: bool = False, default: bool = False) -> bool:
            level = _assertion_level(step, name)
            if level is None:
                return default
            if level == "green":
                return True
            return bool(allow_yellow and level == "yellow")

        checks = {
            "daily_continuity": _ok("verify_ingest", "daily_continuity"),
            # Combos have no Gamma row; Stage 2 analysis inner-joins tokens and drops them.
            "metadata_join": _ok("verify_ingest", "metadata_join", allow_yellow=bool(pid)),
            "unique_fill_ids": _ok("verify_ingest", "unique_fill_ids"),
            # 13.5 days cannot support the 3w/1w calendar split.
            "exact_test_week": (
                True
                if pid and not minority_train_test_split_enabled()
                else _ok("partition", "exact_test_week")
            ),
            "restricted_primary_exists":             logs_path(
                "coverage_audit", "account_classification_primary.parquet"
            ).exists(),
            "fdr_primary_exists": logs_path(
                "multiple_testing", "account_classification_primary_fdr.parquet"
            ).exists(),
            "negation_identity": _assertion_level(
                "sign_construct_audit", "event_gross_negation"
            )
            == "green",
            "discreteness_floor": _assertion_level(
                "multiple_testing", "discreteness_floor"
            )
            == "green",
            "nonzero_pvalues": _assertion_level(
                "multiple_testing", "nonzero_empirical_pvalues"
            )
            == "green",
            "zero_sum_closure": _assertion_level("market_closure", "zero_sum_closure")
            == "green",
            "address_shape": _assertion_level(
                "population_audit", "user_id_address_shape"
            )
            == "green",
        }
        gate_a_accounting = all(checks.values())
        gate_a_statistical = bool(statistical["passed"])
        gate_a_passed = gate_a_accounting and gate_a_statistical
        gate_b_complete = all(
            Path(path).exists()
            for path in [
                logs_path("composition_audit", "category_by_group.parquet"),
                logs_path("composition_audit", "skilled_event_concentration.parquet"),
                logs_path("trader_sanity", "top20_winners_with_labels.parquet"),
            ]
        )
        verdict = {
            "gate_a_passed": gate_a_passed,
            "gate_a_accounting_passed": gate_a_accounting,
            "gate_a_statistical_passed": gate_a_statistical,
            "gate_b_stage1_complete": gate_b_complete,
            "checks": checks,
            "statistical": statistical,
            "stage4_identity_toxicity_allowed": gate_a_passed,
        }
        out_dir = logs_path("stage1_gate")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "stage1_verdict.json").write_text(
            json.dumps(verdict, indent=2), encoding="utf-8"
        )
        for key, value in checks.items():
            dbg.log("check", key, value)
        dbg.assertion(
            "gate_a",
            "green" if gate_a_passed else "red",
            "Non-null profitability labels pass accounting and FDR"
            if gate_a_passed
            else "Profitability labels do not clear all accounting/FDR checks",
        )
        dbg.assertion(
            "gate_b",
            "green" if gate_b_complete else "red",
            "Stage 1 composition and trader-sanity deliverables are complete"
            if gate_b_complete
            else "Stage 1 interpretive deliverables are incomplete",
        )

        exposure = pd.read_parquet(logs_path("coverage_audit", "cutoff_exposure.parquet")).iloc[0]
        composition_top = _metric("composition_audit", "top_category", "unknown")
        composition_share = float(
            _metric("composition_audit", "top_category_volume_share", float("nan"))
        )
        profiles_path = logs_path("trader_sanity", "top5_skilled_profiles.parquet")
        profile_lines = ""
        buy_line = ""
        if profiles_path.exists():
            profiles = pd.read_parquet(profiles_path)
            profile_lines = "\n".join(
                f"- `{r.user_id}`: {int(r.fills):,} fills, {int(r.events)} events, "
                f"${r.pnl:,.0f} PnL, {r.buy_share:.1%} BUY fills."
                for r in profiles.itertuples()
            )
            if len(profiles):
                buy_line = (
                    f"Top-five BUY-fill shares range from {profiles['buy_share'].min():.1%} to "
                    f"{profiles['buy_share'].max():.1%}; heavy-favorite fill shares range from "
                    f"{profiles['heavy_favorite_fill_share'].min():.1%} to "
                    f"{profiles['heavy_favorite_fill_share'].max():.1%}."
                )
        memo = f"""# Stage 1 rigor memo

## Verdict

**Gate A: {'PASS' if gate_a_passed else 'FAIL'}**  
**Gate B deliverables: {'COMPLETE' if gate_b_complete else 'INCOMPLETE'}**  
**Proceed with identity-based Stage 4: {'YES' if gate_a_passed else 'NO'}**

## Coverage and primary universe

Fills end at Unix timestamp `{int(_metric('coverage_audit', 'fill_cutoff_ts', 0))}`.
{float(exposure['market_share_after_cutoff']):.1%} of active markets and
{float(exposure['volume_share_after_cutoff']):.1%} of taker volume are attached to markets
resolving after that cutoff. The unrestricted headline is therefore sensitivity analysis only.
Primary inference uses markets whose market and parent-event end dates are both on or
before the fill cutoff.
The exact calendar split no longer clamps later fills into week 3:
{int(_metric('partition', 'taker_rows_after_test_week', 0)):,} rows after the seven-day
test interval are explicit overflow.

Unrestricted labels: {_fmt_counts(logs_path('coverage_audit', 'group_counts_all.parquet'))}.

Restricted primary labels:
{_fmt_counts(logs_path('coverage_audit', 'group_counts_resolved_by_fill_cutoff.parquet'))}.

## Statistical evidence

Eligible traders (at least 10 fills and 5 events): **{statistical['n_eligible']:,}**.
Nominal p<0.05 flags: **{statistical['observed_nominal']:,}**, against a
discreteness-adjusted null expectation of **{statistical['expected_under_discrete_null']:.1f}**
(z={statistical['excess_z']:.2f}). BH survivors: **{statistical['bh_q05_survivors']:,}**
at q=0.05 and **{statistical['bh_q10_survivors']:,}** at q=0.10.
Storey pi0 is {statistical['storey_pi0']:.3f}.
The honest nominal Skilled share is **{statistical['eligible_skilled_share']:.2%}** of the
eligible cohort; the BH q=0.05 survivor share is
**{statistical['bh_q05_survivors']/statistical['n_eligible']:.2%}**.
Downstream identity-based analyses must use
`labels/account_classification_stage1_fdr_q05.parquet`, not all nominal flags.

## Construct and accounting

For K≤12 events the null is exactly enumerated; larger K uses 10,000 seeded draws with
the `(1+exceedances)/(1+draws)` convention. This both respects the 2^-K floor and prevents
zero Monte Carlo p-values.

Raw event flips satisfy the gross-PnL negation identity:
`{checks['negation_identity']}`. P-values are nonzero and the five-event discreteness
floor holds: `{checks['nonzero_pvalues'] and checks['discreteness_floor']}`.
Maker/taker/fee settlement closes within the configured tolerance:
`{checks['zero_sum_closure']}`. The measured pre-fee leak is
{float(_metric('market_closure', 'pre_fee_leak_bps_volume', float('nan'))):.6f} bps of
participant volume, and
{float(_metric('market_closure', 'maker_counterparty_match_rate', float('nan'))):.2%}
of maker transaction/counterparty groups map to a taker user.

Wallet IDs are canonical addresses, but beneficial-owner linkage across proxy wallets is
not identifiable from `order_filled` alone. Complete-set mint/merge events are absent, but
their omission is settlement-PnL neutral because token payouts sum to one; direct transfers
and gas remain outside the ledger. Residual token positions are therefore expected.
The audit flags {int(_metric('population_audit', 'near_zero_pnl_high_volume_accounts', 0)):,}
high-volume/near-zero-PnL wallets and
{int(_metric('population_audit', 'high_frequency_accounts', 0)):,} high-frequency wallets;
these flags are reported rather than automatically excluded.

## Composition and sanity

Largest category: **{composition_top}**, with {composition_share:.1%} of restricted-universe
volume. Median largest-event share of absolute Skilled PnL:
{float(_metric('composition_audit', 'skilled_median_max_event_share', float('nan'))):.1%}.
The largest winner is classified **{_metric('trader_sanity', 'largest_winner_group', 'unknown')}**.
{int(_metric('trader_sanity', 'top20_not_skilled', 0))} of the top 20 winners are not
classified Skilled, illustrating the low-event/concentrated-whale limitation.
Detailed top-20 labels and top-five Skilled histories are saved under `logs/trader_sanity/`.

Top-five Skilled profiles:

{profile_lines}

{buy_line}
Gate A establishes non-random profitability; it does **not** by itself establish an
informational mechanism. That distinction is left to OIB/toxicity tests. Strategy,
category, and cross-wallet evidence for the Skilled cohort is in
`logs/skilled_profile/` and `docs/v1_pipeline.md` (Skilled-account analysis).

## What replicated and what did not

- Event-split persistence remains a supporting robustness result; it does not override Gate A.
- Directional OIB remains unsupported with execution-price VWAP and truncated price paths.
- The primary universe requires both the market and its parent event to be scheduled to end
  by the fill cutoff; market-only end dates admitted the Harris 2024 winner market.
- Any Stage 3 result must use frozen labels and strict temporal provenance. Stage 4 proceeds
  only if the Gate A line above is PASS.
"""
        memo_path = (
            Path("docs/stage2_gate_memo.md") if period_id() else Path("docs/stage1_rigor_memo.md")
        )
        if period_id():
            memo = memo.replace("# Stage 1 rigor memo", "# Stage 2 gate memo (recent period)")
        memo_path.write_text(memo, encoding="utf-8")
        dbg.log("export", "memo", str(memo_path))
        dbg.log("export", "verdict", str(out_dir / "stage1_verdict.json"))


if __name__ == "__main__":
    main()
