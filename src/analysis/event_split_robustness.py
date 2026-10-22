"""Run event-split persistence over several seeds and summarize the metrics across seeds."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pandas as pd

from src.lib.debug import debug_step
from src.lib.duck import load_env
from src.lib.period import logs_path


def _metrics_from_transition(path: Path, seed: int) -> dict[str, float | int]:
    table = pd.read_parquet(path)
    classified = table.drop(index="Excluded", columns="Excluded", errors="ignore")
    n_both = int(classified.to_numpy().sum())
    skilled_row = classified.loc["Skilled_Winner"]
    unskilled_row = classified.loc["Unskilled_Loser"]
    n_skilled_train = int(skilled_row.sum())
    n_unskilled_train = int(unskilled_row.sum())
    skilled_test = float(classified["Skilled_Winner"].sum() / n_both)
    unskilled_test = float(classified["Unskilled_Loser"].sum() / n_both)
    p_skilled = float(skilled_row["Skilled_Winner"] / n_skilled_train)
    p_unskilled = float(unskilled_row["Unskilled_Loser"] / n_unskilled_train)
    p_winner = float(
        (skilled_row["Skilled_Winner"] + skilled_row["Lucky_Winner"]) / n_skilled_train
    )
    return {
        "seed": seed,
        "n_classified_both": n_both,
        "n_skilled_train_classified_test": n_skilled_train,
        "p_skilled_test_given_skilled_train": p_skilled,
        "base_p_skilled_test": skilled_test,
        "skilled_persistence_lift": p_skilled / skilled_test,
        "p_winner_test_given_skilled_train": p_winner,
        "n_unskilled_train_classified_test": n_unskilled_train,
        "p_unskilled_test_given_unskilled_train": p_unskilled,
        "base_p_unskilled_test": unskilled_test,
        "unskilled_persistence_lift": p_unskilled / unskilled_test,
    }


def main() -> None:
    load_env()
    seeds = [
        int(value.strip())
        for value in os.environ.get("EVENT_OOS_SEEDS", "42,43,44,45,46").split(",")
        if value.strip()
    ]
    if len(seeds) < 2:
        raise RuntimeError("EVENT_OOS_SEEDS must contain at least two integer seeds")

    root = logs_path("event_split_persistence")
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, float | int]] = []
    with debug_step("event_split_robustness") as dbg:
        dbg.log("meta", "event_oos_seeds", seeds)
        for seed in seeds:
            env = os.environ.copy()
            env["EVENT_OOS_SEED"] = str(seed)
            env["EVENT_OOS_EXPORT_R2"] = "false"
            run_log = root / f"seed_{seed}" / "runner_output.log"
            run_log.parent.mkdir(parents=True, exist_ok=True)
            with run_log.open("w") as output:
                subprocess.run(
                    [sys.executable, "-m", "src.analysis.event_split_persistence"],
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            row = _metrics_from_transition(
                root / f"seed_{seed}" / "transition_counts.parquet", seed
            )
            rows.append(row)
            dbg.log("seed", str(seed), row)

        per_seed = pd.DataFrame(rows)
        per_seed.to_parquet(root / "robustness_by_seed.parquet", index=False)
        numeric = [c for c in per_seed.columns if c != "seed"]
        summary = per_seed[numeric].agg(["mean", "std", "min", "max"]).T.reset_index()
        summary = summary.rename(columns={"index": "metric"})
        summary.to_parquet(root / "robustness_summary.parquet", index=False)
        dbg.save_diagnostic_df("robustness_by_seed", per_seed)
        dbg.save_diagnostic_df("robustness_summary", summary)
        dbg.log("export", "per_seed", str(root / "robustness_by_seed.parquet"))
        dbg.log("export", "summary", str(root / "robustness_summary.parquet"))


if __name__ == "__main__":
    main()
