# Stage 1 rigor memo

## Verdict

**Gate A: PASS**  
**Gate B deliverables: COMPLETE**  
**Proceed with identity-based Stage 4: YES**

## Coverage and primary universe

Fills end at Unix timestamp `1730747325`.
28.9% of active markets and
87.8% of taker volume are attached to markets
resolving after that cutoff. The unrestricted headline is therefore sensitivity analysis only.
Primary inference uses markets whose market and parent-event end dates are both on or
before the fill cutoff.
The exact calendar split no longer clamps later fills into week 3:
405,591 rows after the seven-day
test interval are explicit overflow.

Unrestricted labels: Excluded=160377, Unlucky_Loser=50939, Lucky_Winner=19524, Unskilled_Loser=17682, Skilled_Winner=1904.

Restricted primary labels:
Excluded=80795, Unlucky_Loser=8523, Lucky_Winner=3904, Unskilled_Loser=1928, Skilled_Winner=914.

## Statistical evidence

Eligible traders (at least 10 fills and 5 events): **11,465**.
Nominal p<0.05 flags: **914**, against a
discreteness-adjusted null expectation of **539.4**
(z=16.53). BH survivors: **484**
at q=0.05 and **530** at q=0.10.
Storey pi0 is 1.000.
The honest nominal Skilled share is **7.97%** of the
eligible cohort; the BH q=0.05 survivor share is
**4.22%**.
Downstream identity-based analyses must use
`labels/account_classification_stage1_fdr_q05.parquet`, not all nominal flags.

## Construct and accounting

For K≤12 events the null is exactly enumerated; larger K uses 10,000 seeded draws with
the `(1+exceedances)/(1+draws)` convention. This both respects the 2^-K floor and prevents
zero Monte Carlo p-values.

Raw event flips satisfy the gross-PnL negation identity:
`True`. P-values are nonzero and the five-event discreteness
floor holds: `True`.
Maker/taker/fee settlement closes within the configured tolerance:
`True`. The measured pre-fee leak is
-0.000830 bps of
participant volume, and
100.00%
of maker transaction/counterparty groups map to a taker user.

Wallet IDs are canonical addresses, but beneficial-owner linkage across proxy wallets is
not identifiable from `order_filled` alone. Complete-set mint/merge events are absent, but
their omission is settlement-PnL neutral because token payouts sum to one; direct transfers
and gas remain outside the ledger. Residual token positions are therefore expected.
The audit flags 139
high-volume/near-zero-PnL wallets and
8 high-frequency wallets;
these flags are reported rather than automatically excluded.

## Composition and sanity

Largest category: **us-election**, with 19.0% of restricted-universe
volume. Median largest-event share of absolute Skilled PnL:
35.3%.
The largest winner is classified **Lucky_Winner**.
13 of the top 20 winners are not
classified Skilled, illustrating the low-event/concentrated-whale limitation.
Detailed top-20 labels and top-five Skilled histories are saved under `logs/trader_sanity/`.

Top-five Skilled profiles:

- `0x4dfd481c16d9995b809780fd8a9808e8689f6e4a`: 78 fills, 11 events, $91,308 PnL, 100.0% BUY fills.
- `0x2ff0e36eb9632a47c3eb75224bba1f141a6346e3`: 2,979 fills, 273 events, $70,296 PnL, 58.7% BUY fills.
- `0xfffe4013adfe325c6e02d36dc66e091f5476f52c`: 60 fills, 9 events, $70,267 PnL, 98.3% BUY fills.
- `0x03626d34381b0337387b0e8464c898f772009661`: 688 fills, 71 events, $45,388 PnL, 90.0% BUY fills.
- `0xd1ecfa3e7d221851663f739626dcd15fca565d8e`: 84 fills, 16 events, $40,713 PnL, 97.6% BUY fills.

Top-five BUY-fill shares range from 58.7% to 100.0%; heavy-favorite fill shares range from 20.8% to 98.3%.
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
