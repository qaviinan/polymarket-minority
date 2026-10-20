"""Label live Polymarket events by the alternative-data mechanism that could predict them.

Each event gets one mechanism archetype (first match in ``ARCHETYPES`` order wins, after a
sports pre-check on tags). An archetype records the real-world trace that precedes the
outcome, the alt-data categories that observe it, concrete free/cheap sources, cost, lead
time relative to resolution, and an edge rating:

  H     resolution source is itself a statistic that a faster free feed can nowcast
  M     alt data plausibly leads consensus, but noisily
  L     an efficient benchmark exists (derivatives, sharp books) or the outcome is an
        announcement-driven rare event
  none  no observable precursor (structural carry only)

Usage:
  python scripts/alt_data_screen.py            # use cached raw snapshot if present
  python scripts/alt_data_screen.py --refresh  # re-pull Gamma events
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

OUT = Path("logs/alt_data_screen")
RAW = OUT / "raw_events.json"
GAMMA = "https://gamma-api.polymarket.com/events"

CATEGORIES = {
    "CARD": "credit/debit card transactions",
    "RECEIPTS": "email receipts / e-commerce panels",
    "FOOT": "foot traffic / device geolocation",
    "APP": "mobile app usage & rankings",
    "SAT": "satellite imagery / remote sensing",
    "WX": "weather & climate data",
    "SOCIAL": "social media & sentiment",
    "SCRAPE": "web-scraped prices, listings, dockets",
    "WEB": "web traffic & search interest",
    "TRACK": "transponder tracking (AIS ships, ADS-B aircraft)",
    "GOV": "administrative filings & public records",
    "CHAIN": "on-chain / wallet flow",
    "NET": "internet measurement (outages, shutdowns)",
    "DERIV": "derivatives / sharp-book implied (benchmark, not alt data)",
}

US_STATES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|"
    "hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|"
    "michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|"
    "new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
    "rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|"
    "west virginia|wisconsin|wyoming"
)
US_CITIES = "los angeles|new york city|nyc|chicago|houston|seattle|boston|atlanta|denver|dc mayor"
BRAZIL = (
    "brazil|bahia|são paulo|sao paulo|rio de janeiro|rio grande|minas gerais|paraná|parana|"
    "mato grosso|goiás|goias|federal district|pernambuco|ceará|ceara|santa catarina|espírito|"
    "espirito|amazonas|pará|maranhão|paraíba|alagoas|sergipe|piauí|tocantins|rondônia|roraima|"
    "amapá|acre|rio grande do norte|distrito federal"
)
DATA_RICH_FOREIGN = (
    "israel|likud|knesset|france|french|sweden|swedish|quebec|british columbia|alberta|canada|"
    "holborn|by-election|uk |united kingdom|britain|london|new zealand|taipei|taiwan|kraków|"
    "krakow|poland|prague|czech|spain|spanish|japan|germany|german|australia|netherlands|dutch"
)
SPORT_TAGS = {"sports", "esports", "games", "soccer", "tennis", "football", "basketball",
              "hockey", "baseball", "mlb", "nba", "nhl", "nfl (all)", "cfb (all)", "ufc", "golf",
              "f1", "cricket", "counter strike 2", "league of legends", "dota 2", "valorant"}
ESPORT_TAGS = {"esports", "counter strike 2", "league of legends", "dota 2", "valorant",
               "rainbow six siege", "call of duty"}
OUTDOOR_TAGS = {"nfl (all)", "nfl", "cfb (all)", "ncaaf", "f1", "formula 1", "golf", "pga tour",
                "mlb", "baseball", "cricket", "kbo"}


@dataclass(frozen=True)
class Archetype:
    key: str
    pattern: str
    trace: str
    cats: tuple[str, ...]
    signals: str
    cost: str  # free | cheap | paid
    lead: str
    edge: str  # H | M | L | none
    recurring: bool = False
    lit: str = ""


A = Archetype

# Order is match priority: specific patterns first, broad ones ("launch", "hit", "gold") last.
ARCHETYPES: list[Archetype] = [
    A("no_precursor", r"jesus|aliens exist|earth flat|satoshi|sexiest man|antichrist|rapture",
      "No observable precursor", (), "None. Edge, if any, is NO-side time-value carry",
      "free", "none", "none"),
    A("speech_mentions", r"what will .* say|will .* mention|headlines say",
      "Speakers repeat themselves", ("SCRAPE",),
      "Word base rates from prior transcripts: earnings calls (Motley Fool / EDGAR 8-K), Trump "
      "rally transcripts (Factba.se / Roll Call), past broadcast captions; adjust for the news "
      "cycle via headline scraping", "free", "hours", "H", True),
    A("equity_or_index_price", r"\binc\.|\([a-z]{1,5}\) (hit|close|up)|up or down|above ___|"
      r"closes (above|week)|close at|richest person",
      "Priced by options markets", ("DERIV",),
      "Compare the strike ladder to listed-options implied distributions (CBOE/CME, Deribit "
      "for crypto); billionaire rankings are stock prices", "free", "n/a", "L", True),
    # --- physical-world statistics -----------------------------------------------------
    A("wx_station_temp", r"^(highest|lowest) temperature in",
      "Synoptic weather at one airport station", ("WX",),
      "ECMWF open data, GFS/GEFS, ICON (Open-Meteo); live METAR obs; station-specific bias "
      "correction (MOS) into bracket probabilities", "free", "1-3 days, intraday updates",
      "H", True, "Ensemble post-processing (Gneiting et al. 2005)"),
    A("climate_global", r"hottest year|el niño|el nino|\broni\b|global temp|arctic sea ice",
      "Global mean temperature already observed year-to-date", ("WX", "SAT"),
      "Copernicus Climate Pulse daily ERA5; monthly ERA5 bulletin published before GISTEMP; "
      "ENSO indices (NOAA CPC)", "free", "weeks before GISTEMP release", "H"),
    A("wx_event_count", r"hurricane|tropical storm|cyclone|tornado|named storm|snowfall|rainfall",
      "Seasonal storm activity", ("WX", "SAT"),
      "NHC/JTWC advisories, ECMWF/GEFS tropical-genesis ensembles, SPC storm reports",
      "free", "days (genesis) to season", "M", True),
    A("geo_hazard_count", r"earthquake|volcan|eruption|meteor",
      "None: these events are close to memoryless", ("GOV",),
      "USGS catalogue base rates (Poisson / ETAS aftershock clustering)", "free", "none",
      "L", True, "ETAS aftershock models (Ogata 1988)"),
    A("chokepoint_transits",
      r"hormuz.*(traffic|ships|transit)|ships transit|bab el-mandeb.*(ships|transit)|# of ships",
      "Ships crossing a geofence, as counted by IMF PortWatch from AIS", ("TRACK", "SAT"),
      "Replicate PortWatch's transit-call method on live AIS (aisstream.io, Global Fishing "
      "Watch); Sentinel-1 SAR for context only (dark ships don't count for resolution)",
      "free", "PortWatch publishes with multi-day lag", "H", True),
    A("energy_infra_status", r"pipeline|kharg|refinery|oil field|nord stream|island no longer",
      "Physical flows resuming or stopping at a facility", ("TRACK", "SAT"),
      "Tanker loadings via AIS at the relevant terminal (Yanbu for the East-West pipeline, Kharg "
      "for Iran); Sentinel-2 flaring/smoke; NASA FIRMS thermal anomalies", "free",
      "days before official announcement", "M"),
    A("river_level", r"mississippi level|river level|lake level|water level|reservoir",
      "Upstream water already in the channel", ("WX", "GOV"),
      "USGS real-time stream gauges upstream; NWS river forecast centre (RFC) stage forecasts; "
      "basin precipitation forecasts", "free", "1-4 weeks (travel time)", "H", True),
    A("naval_deployment", r"aircraft carrier|\buss [a-z]|carrier strike|centcom|warships",
      "Where the big ships are", ("TRACK", "SAT", "SOCIAL"),
      "USNI News Fleet & Marine Tracker (weekly, free); carrier-group AIS when broadcast; "
      "Sentinel-1/2 imagery of home ports and Suez/Gibraltar transits; ship-spotter posts on X",
      "free", "days to weeks", "H", True),
    A("chokepoint_closure", r"hormuz|bab el-mandeb|strait|blockade|suez|shipping",
      "Shipping avoids or resumes a chokepoint", ("TRACK", "SCRAPE"),
      "AIS rerouting around the Cape; Lloyd's JWC listed-area changes and war-risk premium "
      "quotes; tanker freight proxies; UKMTO incident notices", "free", "days", "M"),
    # --- conflict & geopolitics ----------------------------------------------------------
    A("nuclear_program", r"nuke|nuclear test|enriched uranium|uranium|enrichment|warhead",
      "Activity at known nuclear sites", ("SAT", "GOV"),
      "Sentinel-2 / Planet imagery of Fordow, Natanz, Isfahan, Punggye-ri (vehicle and spoil "
      "activity); IAEA quarterly reports; IRIS/CTBTO seismic for tests", "free", "weeks", "M"),
    A("frontline_control", r"ukraine map|\b(re)?capture\b|take control|\b(re-)?enter\b|control of|"
      r"withdraws? from|forces withdraw|cede territory",
      "Territorial control changes on the ground", ("SAT", "SOCIAL", "SCRAPE"),
      "DeepState/ISW map diffs (scraped daily), NASA FIRMS fire clusters along the front, "
      "Sentinel-1/2 imagery, milblogger Telegram channel sentiment", "free",
      "days to weeks of trend", "M"),
    A("mobilization", r"mobiliz|conscription|draft",
      "State preparing manpower", ("SCRAPE", "SOCIAL", "GOV"),
      "Russian regional decrees & enlistment-bonus listings scraped; recruiter job ads "
      "(hh.ru); Telegram reports of summons; exit-flight demand from Moscow (fare scraping)",
      "free", "weeks", "M"),
    A("military_strike", r"\bstrike\b|invade|invasion|military action|\btarget\b|attack|airstrike|"
      r"troops|clash|airspace clos|declare war|intervention|coup|land or take off",
      "Force posture build-up before kinetic action", ("TRACK", "FOOT", "GOV", "NET"),
      "ADS-B surges of tankers/ISR/AWACS (ADS-B Exchange); carrier positions (USNI fleet "
      "tracker); NOTAM airspace closures; GPS-jamming maps (gpsjam.org); 'Pentagon pizza index' "
      "(Google Maps popular-times near the Pentagon); embassy drawdown notices; for Taiwan: "
      "civilian ro-ro ferry mobilisation via AIS", "free", "hours to days", "M", False,
      "OSINT practice; no rigorous out-of-sample study of these indicators"),
    A("ceasefire_deal", r"ceasefire|peace deal|nuclear deal|agreement|peace talks|truce|deal by|"
      r"deal in|diplomatic meeting|accords|recognize|recognise|normaliz",
      "Negotiators converging, violence subsiding", ("TRACK", "SOCIAL", "SCRAPE"),
      "ADS-B of envoy/state aircraft to venues (Riyadh, Istanbul, Geneva, Doha); GDELT tone & "
      "event counts; ACLED weekly violence; hotel-availability scraping in candidate venues",
      "free", "days", "M"),
    A("leader_meeting_venue", r"where will .* meet|meet next|who will .* meet|meets? with|visit|"
      r"speak to|shake hands|call with|seen together",
      "VIP travel logistics", ("TRACK", "GOV", "SCRAPE"),
      "ADS-B of Air Force One / Russian Il-96 / Ukrainian govt jets; FAA VIP TFR NOTAMs; hotel "
      "block-outs and venue closures scraped; host-country security notices", "free",
      "1-7 days", "M"),
    A("elite_purge_retirement", r"purge|retire|retirement|steps? down|impeach|public appearance",
      "Who stops showing up", ("SCRAPE", "GOV", "SOCIAL"),
      "Appearance counts scraped from state media / PLA Daily / official readouts (absence "
      "precedes purge); for Supreme Court retirements: clerk hiring for future terms (a justice "
      "hiring clerks is not retiring), book deals, financial disclosures", "free",
      "weeks to months", "M"),
    A("leader_exit_regime", r"out as|out before|out by|leader end of|regime fall|leadership change|"
      r"out of power|leader out|resign|lead iran|in power",
      "Elite flight, unrest, or leader health", ("TRACK", "SCRAPE", "NET", "CHAIN", "SAT"),
      "ADS-B of government/oligarch jets leaving; public-appearance frequency scraped from "
      "state media (absences = health); black-market FX (bonbast for rial, dólar paralelo); "
      "USDT premium on Binance P2P (capital flight); IODA/Cloudflare Radar internet shutdowns "
      "(protest suppression); VIIRS night lights", "free", "days to weeks", "M", False,
      "Parallel-market FX premia as crisis indicators (Reinhart & Rogoff 2004)"),
    A("fx_parallel_rate", r"usd x|rial|bolívar|bolivar|exchange rate|lira|naira|ruble|\bpeso",
      "Street FX rate", ("SCRAPE", "CHAIN"),
      "Scrape bonbast.com / alanchand (Iran), dolarhoy (Argentina), Monitor Dólar (Venezuela); "
      "USDT/local-currency P2P quotes on Binance/Nobitex", "free", "real time", "H", True),
    A("immigration_enforcement", r"deport|\bice\b|border crossing|encounters|asylum",
      "Removal flights and detention counts", ("TRACK", "GOV", "SCRAPE"),
      "ICE Air charter flights via ADS-B (Witness at the Border monthly counts); ICE "
      "detention statistics (biweekly); CBP monthly encounter data; TRAC Syracuse",
      "free", "weeks", "H", True),
    A("trade_tariff", r"tariff|trade deal|sanction|export control|entity list|chinese military "
      r"companies",
      "Policy implemented through customs and the Federal Register", ("GOV", "SCRAPE", "TRACK"),
      "Federal Register / Commerce public-inspection desk (posts a day before publication); "
      "Treasury Daily Statement customs receipts (effective tariff rate); USTR releases; ADS-B "
      "of trade delegations", "free", "1 day to weeks", "M"),
    # --- elections ------------------------------------------------------------------------
    A("election_turnout", r"turnout",
      "Ballots already cast before election day", ("GOV", "WEB", "WX"),
      "Early-vote & mail-ballot returns (UF Election Lab, state SoS daily files); registration "
      "deltas; Google Trends 'where do I vote' by DMA; election-day weather", "free",
      "2-4 weeks (early vote)", "H", False,
      "Rain lowers turnout (Gomez, Hansford & Krause 2007)"),
    A("election_us_nomination", r"presidential nominee|vp nominee|presidential run|announce .* run|"
      r"presidential election winner 20|which party wins 20|presidential .* primary",
      "Invisible-primary logistics", ("TRACK", "GOV", "WEB", "SOCIAL", "SCRAPE"),
      "Candidate/donor private-jet ADS-B trips to IA/NH/SC/NV; leadership-PAC formation and "
      "spending (FEC); senior staff hires (LinkedIn scraping); endorsement trackers; Google "
      "Trends & Wikipedia pageview share; podcast/Sunday-show appearance counts", "free",
      "months to years", "L", False,
      "Endorsements predict nominations (Cohen et al. 2008, 'The Party Decides')"),
    A("election_us_chamber", r"balance of power|which party will win the|seats after|majority "
      r"leader|within \d|senate in 20|house in 20|popular vote",
      "Sum of district-level signals", ("GOV", "WEB"),
      "Aggregate the district model below; generic-ballot polls; special-election swing "
      "(average over/under-performance vs partisan lean)", "free", "weeks", "L"),
    A("election_us_district", rf"\b[a-z]{{2}}-(\d{{2}}|al)\b|({US_STATES}) (senate|governor|house|"
      rf"attorney general|secretary of state)|({US_CITIES})|combo|midterm",
      "Campaign insiders' private information leaks into how they spend",
      ("GOV", "WEB", "SOCIAL"),
      "FEC 24/48-hour independent-expenditure reports: party committees (DCCC/NRCC/SLF/SMP) "
      "reserving or cancelling ad time reveals internal polling; Meta & Google political ad "
      "libraries (spend by district); small-dollar donor counts by ZIP (ActBlue/WinRed via "
      "FEC); early vote by party; Cook PVI and special-election swing; Google Trends candidate "
      "share", "free", "weeks", "M", False,
      "Polls+fundamentals hard to beat (Jennings & Wlezien 2018; Graefe 2014)"),
    A("election_brazil", rf"({BRAZIL})",
      "Mandatory real-time campaign disclosures", ("GOV", "WEB", "SOCIAL"),
      "TSE DivulgaCandContas (candidate revenue/spend filed within 72h); PesqEle registry of "
      "every published poll; Google Trends by state; Kwai/TikTok/WhatsApp-group reach", "free",
      "weeks", "M"),
    A("election_foreign_govt", r"prime minister|coalition|government\?|premier|chancellor|"
      r"minister|secretary-general|\bseats\b|parties will",
      "Seat arithmetic and coalition signalling", ("GOV", "SOCIAL", "WEB"),
      "Poll aggregators -> seat projections; party leaders' statements scraped; Wikipedia "
      "pageview share (Smith & Gustafson 2017)", "free", "weeks", "M"),
    A("election_foreign", r"election|mayor|referendum|independence|ballot|round winner|"
      r"advance to the|place$|parliament",
      "Polls and attention in less-covered races", ("WEB", "SOCIAL", "GOV"),
      "Local poll aggregators (often missed by Polymarket's US-centric traders); Google Trends "
      "candidate share; Wikipedia pageviews; Telegram/WhatsApp reach; petition-signature "
      "counts for referendums", "free", "weeks", "M", False,
      "Wikipedia traffic and elections (Smith & Gustafson 2017); mixed Trends evidence"),
    # --- policy / legal -------------------------------------------------------------------
    A("state_policy_backlash", r"moratorium|legaliz|unban|\bban\b",
      "Local opposition building before legislation", ("GOV", "SCRAPE", "SOCIAL", "SAT", "WEB"),
      "LegiScan bill status API; county zoning & utility-commission agendas scraped; "
      "interconnection-queue filings (ERCOT, MISO, PJM); residential rate-case filings; "
      "local-news & Nextdoor sentiment; Sentinel-2 construction footprints; Google Trends "
      "'data center' by DMA", "free", "weeks to months", "M", False,
      "Bill-enactment prediction (Yano, Smith & Wilkerson 2012; Nay 2017)"),
    A("federal_legislation", r"signed into law|\bact\b|\bbills?\b|congress|vote for|filibuster|"
      r"shutdown|becomes? law",
      "Whip counts and procedural steps", ("GOV", "SCRAPE"),
      "congress.gov actions, cosponsor counts, floor schedules; LDA lobbying disclosures; "
      "member statements scraped", "free", "days to weeks", "M", False,
      "Nay 2017, 'Predicting and understanding law-making with word vectors'"),
    # --- companies, AI, crypto ------------------------------------------------------------
    A("ai_leaderboard", r"best ai model|#\d ai model|arena|livebench|ai lab|benchmark",
      "Unreleased models already in public A/B testing", ("SCRAPE", "SOCIAL", "WEB"),
      "arena.ai leaderboard history (votes, CIs -> rank-flip odds); anonymous models in battle "
      "mode; model IDs leaking in Vertex/Bedrock/Azure catalogues & API docs; app-teardown "
      "strings; lab staff posts on X", "free", "days to weeks", "H", True),
    A("token_launch_fdv", r"\bfdv\b|airdrop|token launch|launch a token|\btge\b",
      "Pre-launch price discovery and farmer demand", ("CHAIN", "SOCIAL", "WEB", "DERIV"),
      "Hyperliquid/Aevo pre-market perps & Whales Market OTC quotes; Dune airdrop-eligible "
      "wallet counts, points-program TVL; Kaito mindshare; comparable-launch FDV/TVL ratios",
      "free", "days to weeks", "H"),
    A("company_distress_ipo", r"bankruptcy|\bipos?\b|valuation|market cap|largest company|"
      r"acquisition|merger|acquire (?!greenland)|be acquired",
      "Customer demand and hiring reveal company trajectory",
      ("APP", "WEB", "CARD", "SCRAPE", "GOV"),
      "Similarweb free tier (chatgpt.com, claude.ai visits); App Store rank history; job-board "
      "scraping (Greenhouse/Lever openings, freezes); state WARN layoff notices; secondary "
      "prices (Forge/Hiive); bond prices via FINRA TRACE; EDGAR S-1/8-K; card-panel revenue "
      "(paid: Earnest, Bloomberg Second Measure)", "cheap", "months", "M", False,
      "Card data predicts revenue surprises (Agarwal et al. 2023); satellite parking-lot "
      "counts (Katona et al. 2025)"),
    A("macro_inflation", r"inflation|\bcpi\b|\bpce\b|\bppi\b",
      "Prices posted online and at the pump before statisticians sample them",
      ("SCRAPE", "CARD"),
      "Cleveland Fed inflation nowcast; EIA weekly gasoline; Manheim used-vehicle index; "
      "Zillow/Apartment List rents; scraped supermarket prices (PriceStats-style); for "
      "Argentina/Brazil: daily supermarket scrapes, IPCA-15 precursor", "free", "weeks", "M",
      True, "Cavallo & Rigobon 2016 (Billion Prices Project); Knotek & Zaman 2017"),
    A("macro_activity", r"\bgdp\b|recession|unemployment|jobless|payroll|jobs added",
      "Real activity visible in spending, hiring, and lights", ("CARD", "SCRAPE", "SAT", "GOV"),
      "Atlanta Fed GDPNow; Opportunity Insights card-spend tracker; Indeed Hiring Lab postings; "
      "weekly claims; VIIRS night lights & Sentinel-5P NO2 (China); Korea/Taiwan monthly "
      "exports", "free", "weeks", "M", True,
      "Night lights and GDP (Henderson, Storeygard & Weil 2012); Chetty et al. 2024"),
    A("gpu_compute_price", r"gpu rental|compute price|\bh100\b|\bh200\b|\bb200\b",
      "Marketplace GPU rental prices", ("SCRAPE",),
      "Scrape Vast.ai, RunPod, Lambda, TensorDock listings that feed the Ornn index", "free",
      "daily", "H", True),
    # --- attention & culture --------------------------------------------------------------
    A("search_rank_annual", r"searched",
      "Year-over-year search spikes", ("WEB",),
      "Google Trends daily series; model Google's 'trending' as spike vs prior year, not raw "
      "volume", "free", "months", "M", False, "Choi & Varian 2012"),
    A("post_counts", r"# tweets|truth social posts|tweet .* by|# of posts",
      "Posting behaviour of one account", ("SOCIAL", "TRACK", "GOV"),
      "xtracker live counter; pace model by hour/day; ElonJet ADS-B (flights vs posting); White "
      "House public schedule (golf days, rallies) for Trump", "free", "intraday", "M", True),
    A("video_views", r"mrbeast|youtube|\bviews\b",
      "Early view trajectory", ("WEB", "SOCIAL"),
      "YouTube Data API hourly view counts; thumbnail A/B tests; trailer views", "free",
      "hours after upload", "H", True),
    A("streaming_rank", r"netflix|spotify|billboard|album|song|streams|top 10",
      "Daily charts that aggregate into weekly/annual ones", ("SCRAPE", "APP", "RECEIPTS"),
      "FlixPatrol daily ranks -> Netflix weekly list; kworb Spotify/Apple daily streams; "
      "Mediabase radio proxy; vinyl/merch bundle presales scraped from artist stores for "
      "Billboard 200 sales", "free", "days", "H", True),
    A("box_office", r"box office|grossing|opening week|domestic gross",
      "Ticket presales and pre-release attention", ("SCRAPE", "WEB", "RECEIPTS", "FOOT"),
      "Fandango/AMC seat-map scraping (presale fill); Thursday previews; Wikipedia pageviews; "
      "Google Trends; trailer views; card/receipt panels and theatre foot traffic (paid)",
      "free", "1-3 weeks", "H", True,
      "Mestyán, Yasseri & Kertész 2013; Asur & Huberman 2010"),
    A("app_rank", r"app store|free app|top app",
      "Hourly app chart", ("APP",), "Apple App Store RSS top charts; AppFigures free charts",
      "free", "hours", "H", True),
    A("awards_jury", r"nobel|ballon|oscar|grammy|emmy|golden globe|person of the year|\bmvp\b|"
      r"rookie of the year|award|player of the year",
      "Small juries whose decisions leak", ("CHAIN", "WEB", "SCRAPE", "SOCIAL"),
      "Polymarket wallet-flow anomalies (fresh wallets buying a longshot; cf. 2025 Nobel leak); "
      "Wikipedia pageview spikes on nominee pages; precursor awards (guilds, critics); FBref "
      "stats for Ballon d'Or; Letterboxd ratings", "free", "hours (leaks) to months", "M",
      False, "Insider-flow detection ties to this repo's Skilled_Winner work"),
    A("fan_vote_show", r"eurovision|dancing with the stars|survivor|big brother|the voice|"
      r"bachelor|idol|love island",
      "Fan engagement before the televote", ("SOCIAL", "WEB", "APP"),
      "Spotify streams and YouTube views of entries (track Eurovision televote); Instagram/TikTok "
      "follower growth of contestants; Google Trends by country", "free", "days to weeks",
      "M", True),
    A("legal_personnel", r"arrest|indict|charged|jailed|pardon|convict|sentenced|prison|cabinet|"
      r"secretary|confirm|nominat|fired|leave the|leave trump|trial",
      "Court and personnel paper trails", ("GOV", "SCRAPE", "TRACK", "SOCIAL"),
      "CourtListener/RECAP docket alerts (sealed filings, grand-jury activity); OGE financial "
      "disclosures; Senate calendar; ADS-B of officials; X/Truth Social sentiment toward the "
      "person", "free", "days", "M"),
    A("casting_entertainment", r"james bond|actor|cast as|host|season \d|episode",
      "Production logistics leak before announcements", ("SOCIAL", "SCRAPE", "WEB"),
      "Trade-press scraping (Deadline, Variety); IMDbPro STARmeter; filming-permit filings; "
      "agent/talent social activity; Wikipedia edit wars on candidate pages", "free", "weeks",
      "L"),
    A("disease_declaration", r"pandemic|variant of concern|vaccine|which countries will have",
      "Spread to new countries before WHO acts", ("SCRAPE", "GOV", "TRACK"),
      "WHO Disease Outbreak News & IHR emergency-committee scheduling; ProMED/HealthMap; "
      "wastewater detections at international airports (CDC Traveler-based Genomic "
      "Surveillance); air-passenger flows from affected regions", "free", "weeks", "M"),
    A("disease_counts", r"measles|\bcases\b|west nile|ebola|hantavirus|covid|outbreak|bird flu",
      "Local reports precede national counters", ("SCRAPE", "GOV"),
      "State health-department pages (lead the weekly CDC counter); CDC NWSS & WastewaterSCAN "
      "wastewater detections; WHO Disease Outbreak News; ProMED/HealthMap", "free", "days",
      "H", True),
    A("airline_corporate_events", r"airline",
      "Operational distress", ("TRACK", "SCRAPE"),
      "ADS-B fleet utilisation (parked aircraft), fare scraping, WARN notices", "free",
      "weeks", "M"),
    A("ai_capability", r"ai solve|millennium prize|agi|ai (win|achieve)|imo gold",
      "Research progress visible in preprints", ("SCRAPE", "SOCIAL"),
      "arXiv and lab-blog scraping; Lean/mathlib commit activity; researcher posts on X",
      "free", "months", "L"),
    # --- broad catch-alls (must stay last) ------------------------------------------------
    A("ai_product_release", r"release|launch|gpt|gemini|claude|grok|foldable|iphone|macbook|"
      r"robotaxi|starship|cybercab|debut",
      "Product pipelines leave paper trails", ("SCRAPE", "GOV", "APP", "WEB"),
      "FCC equipment filings; USPTO trademarks; import bills of lading (ImportYeti); API model "
      "lists & docs diffs; app-teardown strings; domain/DNS registrations; FAA launch licences "
      "& NOTAMs for Starship", "free", "weeks", "M"),
    A("fed_policy", r"\bfed\b|fomc|rate cut|rate hike|\becb\b|bank of japan|bank of england|"
      r"\bboe\b|interest rate|warsh|treasury yield|10-year",
      "Priced by rates futures", ("DERIV",),
      "CME FedWatch / SOFR and gilt futures; alt-data inflation and labour nowcasts move it only "
      "marginally", "free", "n/a", "L", True),
    A("crypto_commodity_price", r"price will|price on|\bhit\b|bitcoin|ethereum|solana|\bxrp\b|"
      r"\bgold\b|crude|\bwti\b|s&p|nasdaq|hyperliquid|dogecoin",
      "Priced by options and perps", ("DERIV", "CHAIN"),
      "Compare ladder to Deribit / CME options-implied distributions; perp funding; on-chain "
      "exchange flows", "free", "n/a", "L", True),
    A("sports_outdoor_weather", r"$^",
      "Wind, rain, and temperature at the venue; practice pace", ("WX", "SCRAPE", "DERIV"),
      "Venue weather forecasts (wind for NFL totals and kicking, rain for F1, wind for golf); "
      "FastF1 practice long-run telemetry; injury reports; compare against sharp books "
      "(Pinnacle)", "free", "hours to days", "M", True,
      "Wind lowers NFL scoring (Borghesi 2008)"),
    A("esports_match", r"$^",
      "Form visible in scraped match and ladder data", ("SCRAPE", "SOCIAL", "DERIV"),
      "HLTV/Liquipedia/gol.gg stats; roster changes; patch notes; stand-ins announced on X; "
      "tier-2 events are priced less sharply than tier-1", "free", "hours", "M", True),
    A("sports_market", r"$^",
      "Priced by sharp sportsbooks", ("DERIV", "SOCIAL", "TRACK"),
      "Cross-venue gaps vs Pinnacle/Betfair; injury leaks via player social posts; team charter "
      "ADS-B (travel fatigue, surprise trips)", "free", "hours", "L", True),
]

BY_KEY = {a.key: a for a in ARCHETYPES}
# Keys that sports events may still match before the sports fallback.
SPORT_OVERRIDES = {"no_precursor", "awards_jury", "wx_event_count", "speech_mentions"}


def fetch_events() -> list[dict]:
    seen: dict[str, dict] = {}
    for order in ("volume24hr", "liquidity", "volume"):
        for off in range(0, 2100, 100):
            url = (f"{GAMMA}?active=true&closed=false&limit=100&offset={off}"
                   f"&order={order}&ascending=false")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                page = json.load(urllib.request.urlopen(req, timeout=60))
            except Exception:
                break
            if not page:
                break
            for e in page:
                seen.setdefault(e["id"], e)
            time.sleep(0.1)
    return list(seen.values())


def label(title: str, tags: list[str]) -> Archetype | None:
    t = title.lower()
    tagset = set(tags)
    is_sport = bool(SPORT_TAGS & tagset) or bool(re.search(r" vs\.? |\(bo[1-7]\)", t))
    if is_sport and "elections" not in tagset:
        hit = next((a for a in ARCHETYPES if a.key in SPORT_OVERRIDES
                    and re.search(a.pattern, t)), None)
        if hit and hit.key != "wx_event_count":
            return hit
        if ESPORT_TAGS & tagset or re.search(r"\(bo[1-7]\)", t):
            return BY_KEY["esports_match"]
        if OUTDOOR_TAGS & tagset:
            return BY_KEY["sports_outdoor_weather"]
        return BY_KEY["sports_market"]
    hit = next((a for a in ARCHETYPES if re.search(a.pattern, t)), None)
    if hit is None and "elections" in tagset:
        hit = BY_KEY["election_foreign"]
    return hit


def election_richness(key: str, title: str) -> str | None:
    if not key.startswith("election"):
        return None
    if key in {"election_turnout", "election_us_district", "election_us_chamber",
               "election_us_nomination"}:
        return "H"
    if key == "election_brazil" or re.search(DATA_RICH_FOREIGN, title.lower()):
        return "M"
    return "L"


EDGE_W = {"H": 3, "M": 2, "L": 1, "none": 0}
COST_W = {"free": 1.0, "cheap": 0.8, "paid": 0.4}


def priority(a: Archetype, liq: float, vol: float) -> float:
    depth = math.log10(1 + liq + 0.1 * vol)
    return round(EDGE_W[a.edge] * COST_W[a.cost] * depth * (1.3 if a.recurring else 1.0), 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.refresh or not RAW.exists():
        RAW.write_text(json.dumps(fetch_events()))
    events = json.loads(RAW.read_text())

    rows = []
    for e in events:
        title = e.get("title") or ""
        tags = [(t.get("label") or "").lower() for t in e.get("tags") or []]
        a = label(title, tags)
        liq = float(e.get("liquidity") or 0)
        vol = float(e.get("volume") or 0)
        row = {
            "id": e["id"], "slug": e.get("slug"), "title": title,
            "liquidity": liq, "volume": vol, "volume_24h": float(e.get("volume24hr") or 0),
            "end": (e.get("endDate") or "")[:10], "n_markets": len(e.get("markets") or []),
            "archetype": a.key if a else "unlabeled",
        }
        if a:
            row.update(categories="|".join(a.cats), trace=a.trace, signals=a.signals,
                       cost=a.cost, lead=a.lead, edge=a.edge, recurring=a.recurring,
                       literature=a.lit, priority=priority(a, liq, vol),
                       data_richness=election_richness(a.key, title))
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("liquidity", ascending=False)
    df.to_csv(OUT / "labeled_events.csv", index=False)

    lab = df[df.archetype != "unlabeled"]
    print(f"events {len(df)}, labeled {len(lab)} "
          f"({lab.liquidity.sum() / df.liquidity.sum():.1%} of liquidity)")

    arch = (lab.groupby("archetype")
            .agg(n=("id", "size"), liq_m=("liquidity", lambda s: s.sum() / 1e6),
                 vol_m=("volume", lambda s: s.sum() / 1e6),
                 v24_k=("volume_24h", lambda s: s.sum() / 1e3),
                 edge=("edge", "first"), cost=("cost", "first"),
                 cats=("categories", "first"), recurring=("recurring", "first"),
                 priority=("priority", "sum"))
            .sort_values("priority", ascending=False))
    meta = pd.DataFrame([{"archetype": a.key, "trace": a.trace, "signals": a.signals,
                          "lead": a.lead, "literature": a.lit} for a in ARCHETYPES])
    arch.reset_index().merge(meta, on="archetype", how="left").to_csv(
        OUT / "archetype_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(arch.round(2).to_string())

    cat = (lab.assign(category=lab.categories.fillna("").str.split("|"))
           .explode("category").query("category != ''"))
    cat_sum = (cat.groupby("category")
               .agg(events=("id", "nunique"), liq_m=("liquidity", lambda s: s.sum() / 1e6),
                    v24_k=("volume_24h", lambda s: s.sum() / 1e3)))
    hm = cat[cat.edge.isin(["H", "M"])].groupby("category")
    cat_sum["liq_edge_HM_m"] = hm.liquidity.sum() / 1e6
    cat_sum["archetypes_HM"] = hm.archetype.agg(lambda s: ", ".join(sorted(set(s))))
    cat_sum["description"] = cat_sum.index.map(CATEGORIES)
    cat_sum = cat_sum.sort_values("liq_edge_HM_m", ascending=False)
    cat_sum.to_csv(OUT / "category_summary.csv")
    print(cat_sum.drop(columns="archetypes_HM").round(1).to_string())

    el = lab[lab.archetype.str.startswith("election")]
    print(el.groupby(["archetype", "data_richness"])
          .agg(n=("id", "size"), liq_m=("liquidity", lambda s: s.sum() / 1e6),
               vol_m=("volume", lambda s: s.sum() / 1e6)).round(1).to_string())

    un = df[df.archetype == "unlabeled"].head(25)
    print("\nTop unlabeled:")
    for _, r in un.iterrows():
        print(f"  {r.liquidity / 1e3:7.0f}k | {r.title[:90]}")


if __name__ == "__main__":
    main()
