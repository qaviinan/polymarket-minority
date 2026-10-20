# Alternative-data screen of live Polymarket events

**Snapshot:** 2026-10-05, 4,158 active events (Gamma API, top ~2,100 each by 24h volume,
lifetime volume, and liquidity). Reproduce with `python scripts/alt_data_screen.py [--refresh]`.
Outputs are in `logs/alt_data_screen/`:

- `labeled_events.csv`: every event with archetype, categories, trace, signals, cost, lead
  time, edge, literature, election data richness, and priority score.
- `archetype_summary.csv`: per-archetype totals and descriptions.
- `category_summary.csv`: per alt-data category totals.

**Coverage:** 3,917 events labeled, **99.5% of liquidity**. The unlabeled residual is novelty
markets ("Trump renames New Mexico", "Nothing Ever Happens").

**Caveat on "liquidity":** Gamma `liquidity` is resting depth near the midpoint, much of it
reward quoting. It measures how much you can take, not how many informed traders compete.
Read it alongside lifetime and 24h volume.

## How events are labeled

Each event gets one **mechanism archetype**: the real-world trace that comes before the
outcome. The office-REIT example works the same way: people leaving home → occupancy → rents.
An archetype carries:

| Field | Meaning |
|---|---|
| `trace` | What happens in the world before resolution |
| `categories` | Which alt-data families observe that trace (list below) |
| `signals` | Concrete sources, preferring free ones |
| `cost` | free / cheap / paid |
| `lead` | How far ahead of resolution the signal is visible |
| `edge` | **H** = the resolution source is a published statistic that a faster free feed can nowcast. **M** = alt data plausibly leads consensus, but noisily. **L** = an efficient benchmark already prices it (options, rates futures, sharp sportsbooks) or it is a rare announcement-driven event. **none** = no precursor exists |
| `data_richness` | Elections only: how much structured public data exists for the constituency |
| `priority` | edge weight × cost weight × log depth × 1.3 if recurring (summed per archetype) |

Categories: CARD (card transactions), RECEIPTS (email receipts / e-commerce panels), FOOT (foot
traffic / geolocation), APP (app usage and rankings), SAT (satellite / remote sensing), WX
(weather and climate), SOCIAL (social and sentiment), SCRAPE (web-scraped prices, listings,
dockets), WEB (web traffic and search), TRACK (AIS ships / ADS-B aircraft), GOV (administrative
filings and public records), CHAIN (on-chain / wallet flow), NET (internet outage measurement),
DERIV (derivatives or sharp-book implied: the benchmark to beat, not alt data).

## Headline finding: the classic hedge-fund alt-data stack barely maps to Polymarket

| Category | Events | Depth, all | Depth in H/M archetypes |
|---|---:|---:|---:|
| WEB (traffic, search) | 1,803 | $444M | $229M |
| SOCIAL | 2,618 | $543M | $223M |
| GOV (filings, records) | 1,834 | $433M | $219M |
| SCRAPE | 1,074 | $325M | $127M |
| WX | 485 | $72M | $72M |
| TRACK (AIS / ADS-B) | 1,038 | $355M | $35M |
| CHAIN | 277 | $41M | $25M |
| SAT | 216 | $25M | $25M |
| NET | 136 | $17M | $17M |
| FOOT | 85 | $10M | $10M |
| APP | 176 | $10M | $10M |
| CARD | 113 | $9M | $9M |
| RECEIPTS | 52 | $4M | $4M |

Card, receipt, foot-traffic, and app data are built to predict **company revenue**, and
Polymarket lists almost no revenue or KPI markets (equity exposure is "TSLA closes above X",
which options already price). The categories that pay here are **public records, scraping,
transponder tracking, and weather**. Those are cheaper anyway. Large WEB/SOCIAL/GOV totals are
mostly election markets, where those sources are ensemble inputs rather than a decisive edge.

## Archetypes ranked (excluding generic sports)

### Tier 1: edge H. The resolution source is a statistic you can replicate or nowcast for free

| Archetype | Events | Depth | Lifetime vol | Mechanism & data |
|---|---:|---:|---:|---|
| `wx_station_temp` | 166 | $9.6M | $2.2M ($1.6M/day) | One airport station's daily max/min. ECMWF/GFS/ICON ensembles via Open-Meteo, live METAR, station bias correction. Heavily botted. |
| `ai_leaderboard` | 39 | $3.4M | $6.7M | arena.ai rank at a check time. Scrape vote counts and CIs for rank-flip odds; anonymous models already in battle mode; model IDs leaking in Vertex/Bedrock catalogues. |
| `box_office` | 17 | $3.3M | $20.9M | Fandango/AMC seat-map scraping (presale fill), Thursday previews, Wikipedia pageviews and Trends (Mestyán et al. 2013). |
| `chokepoint_transits` | 14 | $2.7M | $30.6M | IMF PortWatch counts AIS transits with a lag. Replicate its method on live AIS. Dark ships don't count, so satellite radar adds nothing for resolution. |
| `election_turnout` | 48 | $5.2M | $0.5M | Early-vote and mail returns (UF Election Lab, state SoS files) are ballots already cast. Thin volume means stale reward-quoted books. |
| `streaming_rank` | 35 | $0.9M | $4.0M | FlixPatrol daily → Netflix weekly; kworb daily streams → Spotify Wrapped and Billboard; vinyl/merch presales scraped for album sales. |
| `token_launch_fdv` | 69 | $0.6M | $35M | Hyperliquid/Aevo pre-market perps and Whales OTC quotes give a price before launch; Dune farmer counts; Kaito mindshare. |
| `video_views` | 6 | $0.3M | $0.4M | YouTube Data API hourly views extrapolate a 7-day total from the first hours. |
| `climate_global` | 2 | $0.3M | $3.7M | Copernicus Climate Pulse daily ERA5 comes out weeks ahead of GISTEMP. By Q4 most of the year is observed. |
| `naval_deployment` | 5 | $0.2M | $0.1M | "Carriers in CENTCOM on Nov 30": USNI Fleet Tracker (weekly, free), carrier AIS when on, Suez/Gibraltar transit imagery. |
| `fx_parallel_rate` | 1 | $0.1M | $0.1M | "USD x Iranian rial": scrape bonbast/alanchand and USDT P2P quotes. The street rate is the resolution. |
| `immigration_enforcement` | 2 | $0.1M | $0.4M | ICE Air removal flights via ADS-B (Witness at the Border) plus ICE detention statistics. |
| `speech_mentions` | 7 | <$0.1M | <$0.1M | "What will X say on the earnings call / at the rally": word base rates from prior transcripts. |
| `disease_counts`, `gpu_compute_price`, `river_level`, `app_rank` | 20 | <$0.1M each | | State health pages lead the CDC counter; Vast.ai/RunPod listings feed the Ornn GPU index; upstream USGS gauges lead Baton Rouge by 1–4 weeks; the App Store RSS chart. Near-deterministic, but tiny. |

### Tier 2: edge M. Alt data plausibly leads consensus, and liquidity is large

| Archetype | Events | Depth | Lifetime vol | Mechanism & data |
|---|---:|---:|---:|---|
| `election_us_district` | 893 | $97M | $97M | See the election section below. |
| `sports_outdoor_weather` | 257 | $57M | $399M | Wind for NFL totals and kicking (Borghesi 2008), rain for F1, FastF1 practice telemetry, compared against Pinnacle. |
| `election_foreign` / `_govt` / `_brazil` | 484 | $82M | $703M | Foreign races that US-centric traders cover less: local poll aggregators, Wikipedia pageviews (Smith & Gustafson 2017), Brazil TSE spending filed within 72h. |
| `awards_jury` | 54 | $14M | $88M | Small juries leak. Polymarket wallet anomalies (fresh wallets buying a longshot before the 2025 Nobel), nominee Wikipedia spikes, guild precursors. **This builds directly on this repo's Skilled_Winner / insider-flow work.** |
| `leader_exit_regime` | 68 | $10M | $414M | ADS-B of government and oligarch jets leaving; state-media appearance counts (absence = health); black-market FX and USDT P2P premium as capital flight; IODA/Cloudflare internet shutdowns. |
| `state_policy_backlash` | 10 | $8.3M | $6.7M | Data-center moratoria (Indiana $2.4M depth alone). County zoning and utility-commission agendas, interconnection-queue filings, residential rate cases, LegiScan, local-news sentiment, Sentinel-2 construction footprints. |
| `company_distress_ipo` | 76 | $7.9M | $87M | The one place card/app/web data fits: claude.ai and chatgpt.com traffic, App Store rank, job-board freezes, WARN layoff notices, TRACE bond prices, secondary-market quotes. |
| `military_strike` | 68 | $6.6M | $213M | Tanker/ISR ADS-B surges, NOTAMs, GPS-jamming maps, USNI carriers, the "Pentagon pizza index" (Google Maps popular-times near the Pentagon, i.e. foot traffic), and civilian ro-ro ferry mobilisation via AIS for Taiwan. |
| `search_rank_annual` | 11 | $5.8M | $1.2M | Google Trends. Google's "trending" ranks the spike vs the prior year, not raw volume. |
| `ceasefire_deal`, `leader_meeting_venue` | 64 | $8.3M | $110M | Envoy and state-aircraft ADS-B to candidate venues, VIP TFR NOTAMs, hotel block-outs scraped, GDELT/ACLED intensity. |
| `legal_personnel`, `elite_purge_retirement` | 61 | $3.5M | $35M | CourtListener sealed filings and grand-jury dockets; who stops appearing in PLA Daily readouts; Supreme Court clerk hiring (a justice hiring for future terms isn't retiring). |
| `post_counts` | 10 | $1.5M | $2.8M ($0.5M/day) | xtracker pace model plus ElonJet ADS-B and the White House schedule (golf days → posting bursts). |
| `energy_infra_status`, `nuclear_program`, `frontline_control` | 89 | $2.4M | $205M | Terminal tanker loadings via AIS (Yanbu, Kharg), FIRMS fires, Sentinel imagery of Fordow/Natanz, DeepState map diffs. |
| `macro_inflation` / `macro_activity` | 37 | $0.6M | $7.1M | Cleveland Fed nowcast, scraped prices (Cavallo & Rigobon 2016), card-spend tracker, night lights (Henderson et al. 2012). Strong research base, thin markets. |

### Low priority: edge L or none

- **`election_us_nomination` ($197M depth, $2.7B volume)** and **`election_us_chamber`
  ($17M).** Heavily traded headline markets. Candidate-jet ADS-B trips to early states, PAC
  formation, and staff hires are creative signals, but the horizon is long and attention is
  saturated.
- **`sports_market` ($122M), `fed_policy` ($11M), `equity_or_index_price` ($15M),
  `crypto_commodity_price` ($16M).** These are priced by sharp books, rates futures, and
  options. The trade is cross-venue arbitrage against those benchmarks, not alt data.
- **`geo_hazard_count`, `ai_capability`, `casting_entertainment`, `no_precursor`.** No usable
  precursor. Long-dated "Jesus returns" style markets offer only NO-side carry.

## Elections: where the "data-heavy constituency" idea actually fits

| Sub-archetype | Data richness | Events | Depth | Lifetime vol |
|---|---|---:|---:|---:|
| `election_us_district` (House, Senate, Gov, MOV) | H | 893 | $97M | $97M |
| `election_us_nomination` | H | 17 | $197M | $2,715M |
| `election_us_chamber` | H | 12 | $17M | $42M |
| `election_turnout` | H | 48 | $5M | $0.5M |
| `election_brazil` | M | 109 | $13M | $180M |
| `election_foreign` | M / L | 69 / 227 | $26M / $27M | $152M / $21M |
| `election_foreign_govt` | M / L | 36 / 43 | $10M / $6M | $55M / $294M |

The US district books are the sweet spot. There are 893 events with about $90k median depth
but only about $18k median lifetime volume, so the quotes are mostly reward liquidity that
nobody is trading against with a model. An ensemble for them:

1. **Party money as revealed preference.** This is the election equivalent of card data. FEC
   24/48-hour independent-expenditure reports and AdImpact-style reservation news show
   DCCC/NRCC/SLF/SMP adding or cancelling ad time. Campaigns pay for internal polls and then
   spend on them.
2. **Meta and Google political ad libraries** (free APIs): spend by district and candidate,
   updated daily.
3. **Small-dollar donor counts by ZIP** (ActBlue/WinRed via FEC): an enthusiasm proxy.
4. **Early vote by party registration** (state files): for turnout and late MOV.
5. **Structural priors:** Cook PVI, 2024 presidential margin, average special-election swing.
6. **Attention:** Google Trends candidate share by DMA, which is weak alone.

The literature says not to expect sentiment to beat polls. Twitter-based forecasting failed
out of sample (Tumasjan et al. 2010 → Gayo-Avello 2012), Google Trends results are mixed, and
polls plus fundamentals plus markets are hard to beat (Graefe 2014; Jennings & Wlezien 2018).
The edge in low-attention MOV books comes from **having a calibrated model at all**, and
structured filings (items 1–5) do most of the work.

## The office-REIT example

Polymarket has no REIT or relative real-estate markets, and Google's COVID-19 Community
Mobility Reports stopped updating in October 2022. The free successors for that thesis are the
Kastle Back-to-Work barometer (weekly office occupancy by metro) and the Placer.ai office index,
but that is an equities trade. The nearest Polymarket analogue is `state_policy_backlash`
(data-center moratoria), where physical build-out and utility filings come before legislation.

## Suggested next steps

1. **Backtest with many repetitions first:** `wx_station_temp` (NWP + METAR vs closed markets)
   and `chokepoint_transits` (live-AIS PortWatch replica), pricing entries at actual fills from
   the `order_filled` ingest. Stage 2 currently excludes weather, so this needs a separate pull.
2. **Midterm district/MOV ensemble** before Nov 3: FEC IE + ad libraries + early vote + PVI,
   traded against stale reward-quoted books.
3. **Insider-flow monitor for `awards_jury`**, reusing the Skilled_Winner wallet labels.
