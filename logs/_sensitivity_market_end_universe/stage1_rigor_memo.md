# Stage 1 rigor memo

## Verdict

**Gate A: PASS**  
**Gate B deliverables: COMPLETE**  
**Proceed with identity-based Stage 4: YES**

## Coverage and primary universe

Fills end at Unix timestamp `1730747325`.
28.5% of active markets and
66.1% of taker volume are attached to markets
resolving after that cutoff. The unrestricted headline is therefore sensitivity analysis only.
Primary inference uses markets resolving on or before the fill cutoff.
The exact calendar split no longer clamps later fills into week 3:
405,591 rows after the seven-day
test interval are explicit overflow.

Unrestricted labels: Excluded=160377, Unlucky_Loser=50933, Lucky_Winner=19525, Unskilled_Loser=17688, Skilled_Winner=1903.

Restricted primary labels:
Excluded=157550, Unlucky_Loser=12187, Lucky_Winner=4717, Unskilled_Loser=2351, Skilled_Winner=890.

## Statistical evidence

Eligible traders (at least 10 fills and 5 events): **12,845**.
Nominal p<0.05 flags: **890**, against a
discreteness-adjusted null expectation of **599.8**
(z=12.14). BH survivors: **349**
at q=0.05 and **459** at q=0.10.
Storey pi0 is 1.000.
The honest nominal Skilled share is **6.93%** of the
eligible cohort; the BH q=0.05 survivor share is
**2.72%**.
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
0.000386 bps of
participant volume, and
100.00%
of maker transaction/counterparty groups map to a taker user.

Wallet IDs are canonical addresses, but beneficial-owner linkage across proxy wallets is
not identifiable from `order_filled` alone. Complete-set mint/merge events are absent, but
their omission is settlement-PnL neutral because token payouts sum to one; direct transfers
and gas remain outside the ledger. Residual token positions are therefore expected.
The audit flags 238
high-volume/near-zero-PnL wallets and
15 high-frequency wallets;
these flags are reported rather than automatically excluded.

## Composition and sanity

Largest category: **trump**, with 66.7% of restricted-universe
volume. Median largest-event share of absolute Skilled PnL:
37.6%.
The largest winner is classified **Skilled_Winner**.
17 of the top 20 winners are not
classified Skilled, illustrating the low-event/concentrated-whale limitation.
Detailed top-20 labels and top-five Skilled histories are saved under `logs/trader_sanity/`.

Top-five Skilled profiles:

- `0x4ce73141dbfce41e65db3723e31059a730f0abad`: 59,434 fills, 53 events, $5,606,363 PnL, 1.2% BUY fills.
- `0x59ee6c6a56d7b00223f0c30f8002c4df762b684d`: 41,638 fills, 13 events, $4,519,605 PnL, 0.1% BUY fills.
- `0xd218e474776403a330142299f7796e8ba32eb5c9`: 26,181 fills, 97 events, $392,321 PnL, 99.9% BUY fills.
- `0x5f390e4b7d6f06d6756a6c92afdbf7b3176aa78c`: 11,046 fills, 65 events, $185,163 PnL, 78.0% BUY fills.
- `0x7821317090705c46e50e1e5b6c4bbff22dece564`: 34 fills, 7 events, $181,053 PnL, 52.9% BUY fills.

The two highest-PnL Skilled wallets have only 1.2% and 0.1% BUY fills. They may represent
systematic selling, complete-set/arbitrage, or other market-structure strategies rather than
superior directional beliefs. Gate A establishes non-random profitability; it does **not**
by itself establish an informational mechanism. That distinction is left to OIB/toxicity
tests.

## What replicated and what did not

- Event-split persistence and short-horizon Skilled volume-share price efficiency remain
  supporting robustness results, but they do not override Gate A.
- Directional OIB remains unsupported with execution-price VWAP and truncated price paths.
- Heavy-favorite buying does not explain most Skilled PnL.
- The result is strongly election/category concentrated and should not be generalized to
  unrelated market categories without Stage 2.
- Any Stage 3 result must use frozen labels and strict temporal provenance. Stage 4 proceeds
  only if the Gate A line above is PASS.
