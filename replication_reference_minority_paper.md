# Reference Document: Replicating "Prediction Market Accuracy: Crowd Wisdom or Informed Minority?"

## 1. Context and Reference

**Paper:** Gómez-Cram, R., Guo, Y., Jensen, T. I., & Kung, H. (2026). *Prediction Market Accuracy: Crowd Wisdom or Informed Minority?*

**Context:** The conventional view of prediction markets—often promoted by the platforms themselves—is that their accuracy stems from the "wisdom of crowds," where the aggregated beliefs of a large, diverse group of participants cancel out idiosyncratic errors. This paper challenges that hypothesis. By analyzing highly granular, transaction-level data from Polymarket (the world's largest prediction market), the authors demonstrate that market accuracy is actually driven by a very small, highly informed minority (roughly 3% of accounts). This skilled minority consistently predicts outcomes, reacts instantaneously to news, and drives prices toward the truth. The remaining majority of traders do not systematically contribute to price discovery; instead, they generate trading volume and incur the financial losses that compensate the informed minority.

---

## 2. Full Task Outline for Replication

The following tasks outline the exact methodological steps required to replicate the paper's core finding that a distinct, informed minority exists and drives market accuracy.

### Task 1: Data Acquisition and Assembly
The foundational step involves reconstructing the full trading environment of Polymarket between January 1, 2023, and December 31, 2025, by combining three separate data layers.
* **1.1 Collect Market Metadata:** Extract event details, market definitions, categories, resolution dates, and unique digital asset identifiers (Outcome Tokens) for all markets.
* **1.2 Collect Historical Prices:** Retrieve the time-series pricing data for every outcome token to establish the trajectory of market probabilities over time.
* **1.3 Collect Transaction Ledger:** Extract the complete, second-by-second record of all trades from the public blockchain. This must include the exact timestamp, trade size, price, and the specific pseudonymous wallet address associated with the trade.
* **1.4 Data Integration:** Merge these three datasets using the common token identifiers to create a unified database linking specific traders to specific trades, prices, and market outcomes.

### Task 2: Trader Categorization (Liquidity vs. Information)
Before assessing predictive skill, participants must be separated based on their primary trading mechanism.
* **2.1 Identify Market Makers:** Isolate accounts that primarily operate by posting limit orders to earn rebates and provide liquidity ("makers"). The authors identify these as a distinct group (~0.1% of accounts) separate from directional traders.
* **2.2 Identify Directional Traders:** Isolate accounts that cross the bid-ask spread and pay fees to execute trades immediately ("takers"). The subsequent skill analysis is performed exclusively on this group.

### Task 3: The Sign-Randomization Test of Skill
Prediction markets are largely zero-sum, meaning some traders will amass profits purely by chance. To separate genuine skill from luck, a specialized Monte Carlo simulation must be constructed.
* **3.1 Calculate Realized PnL:** Compute the actual profit and loss for every taker account across the sample period.
* **3.2 Event-Level Randomization:** For each account, generate a simulated "no-predictability" null distribution by randomly flipping the direction of their trades (changing buys to sells, and vice versa) 10,000 times.
* **3.3 Account for Correlation:** *Crucial methodological constraint:* The randomization must occur at the *event* level, not the individual market level. If a trader makes correlated bets across multiple markets tied to the same underlying event, or reverses their position within the same market, these trades must be flipped together in the simulation to preserve the correlation structure and prevent statistical bias.

### Task 4: Account Classification
Using the distributions generated in Task 3, categorize every account based on the statistical significance of their profits.
* **4.1 Calculate P-Values:** Compare each trader's realized PnL to their simulated null distribution to determine their p-value.
* **4.2 Group Assignment:** Partition the accounts into four distinct categories:
    * **Skilled Winners:** Accounts with significant profits (p-value < 0.05). (The paper finds this is ~3.14% of accounts).
    * **Lucky Winners:** Accounts with positive profits but lacking statistical significance.
    * **Unlucky Losers:** Accounts with statistically insignificant losses.
    * **Unskilled Losers:** Accounts with persistent, significant losses (p-value > 0.95).

### Task 5: Out-of-Sample Persistence Testing
To prove the classification captures genuine, persistent skill rather than statistical noise, the test must be validated out-of-sample.
* **5.1 Event Splitting:** Randomly divide the universe of events into a training set and a test set. (The authors specifically note *not* to split by time, as market conditions like liquidity and user base varied too heavily across the 2023-2025 window).
* **5.2 Independent Classification:** Rerun the sign-randomization test and account classification (Tasks 3 & 4) independently on both the training and test sets.
* **5.3 Transition Analysis:** Construct a transition matrix to evaluate how many accounts classified as "Skilled Winners" in the training set retain that classification in the test set.

### Task 6: Measuring Price Discovery and Accuracy
Once the "Skilled Winners" are identified, their specific impact on market prices must be measured against the rest of the crowd.
* **6.1 Compute Order Imbalance (OIB):** Calculate the directional trading activity (net buy volume for the "Yes" outcome scaled by total volume) for each of the classified trader groups.
* **6.2 Price Prediction Regressions:** Regress subsequent price changes and the final resolved market outcomes on the OIB of the different trader groups to determine whose trades actually forecast the truth.
* **6.3 Lifecycle Accuracy Analysis:** Standardize the lifespan of every market onto a unit interval divided into 50 equal bins. Calculate the change in absolute pricing error (the distance between the current price and the final 0 or 1 resolution) in each bin, and regress this improvement on the share of trading volume executed by the informed minority.

### Task 7: Event Studies on Information Arrival
To test the speed and informational edge of the skilled minority, analyze periods of sudden, scheduled information release.
* **7.1 Isolate Scheduled Events:** Filter the dataset for markets tied to specific, pre-scheduled news drops (e.g., FOMC rate announcements, corporate earnings reports).
* **7.2 High-Frequency OIB Analysis:** Measure the order imbalance in narrow, high-frequency time windows immediately surrounding the news release to determine which trader group trades in the direction of the news surprise the fastest.
