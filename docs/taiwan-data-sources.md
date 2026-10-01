# Taiwan market data sources

The `taiwan` provider uses one existing provider seam with the following policy:

1. TWSE and TPEx official endpoints are authoritative for the security directory,
   daily close quote, and daily K-line.
2. FinMind and Yahoo remain supplements and fallbacks for coverage unavailable from
   official endpoints. Fallback records are labelled `third_party_fallback`.
3. No third-party value silently overwrites a successful official value.

TWSE Security Master and official quote/history requests use the existing
`httpx` transport with normal certificate verification. This avoids a Windows
Python 3.13 `urllib` system-trust chain incompatibility without weakening TLS.

TPEx valuation remains fail-closed on TLS errors. On 2026-08-31 the same Python
3.13 runtime verified the endpoint with production, plain, and explicit-certifi
`httpx` clients without redirects; no certificate bypass or code change was needed.

TWSE rule profiles for 00631L and 00632R are explicit audited product entries,
not ticker/name heuristics. Official ETF product pages establish domestic-index
leveraged/inverse identity; TWSE trading rules then apply `10% * abs(multiplier)`.
This rule registry does not populate Phase 6E structured multiplier/direction fields.

## Official endpoints

- TWSE companies: `opendata/t187ap03_L`
- TWSE ETFs: `opendata/t187ap47_L`
- TPEx companies: `mopsfin_t187ap03_O`
- TWSE daily quote: `exchangeReport/STOCK_DAY_ALL`
- TPEx daily quote: `tpex_mainboard_quotes`
- TWSE monthly K-line: `exchangeReport/STOCK_DAY`
- TPEx monthly K-line: `afterTrading/tradingStock`
- TWSE institutional: `fund/T86`
- TPEx institutional: `insti/dailyTrade`
- TWSE margin/short: `marginTrading/MI_MARGN`
- TPEx margin/short: `margin/balance`
- TWSE market-wide institutional amounts: `fund/BFI82U`
- TPEx market-wide institutional amounts: `insti/summary`
- TWSE ex-rights/ex-dividend results: `exRight/TWT49U`
- TWSE capital reduction resumption prices: `reducation/TWTAUU`
- TWSE par value change resumption prices: `change/TWTB8U`
- TWSE ETF split resumption prices: `split/TWTCAU`
- TPEx ex-rights/ex-dividend results: `bulletin/exDailyQ`
- TPEx capital reduction resumption prices: `bulletin/revivt`

TPEx ETF identity is retained from the official ISIN directory. Product
classification remains `data_insufficient` until an official structured metadata
source is integrated; code/name heuristics are not used to fill this gap.

## Normalized contract

- `volume` is shares/units. TPEx monthly `成交仟股` is multiplied by 1,000.
- `amount` is TWD. TPEx monthly `成交仟元` is multiplied by 1,000.
- ROC dates such as `115/08/30` and `1150830` normalize to `2026-08-30`.
- Daily `timestamp` is explicitly `13:30 Asia/Taipei`.
- Missing values (`""`, `-`, `--`, `N/A`) become null; explicit zero stays zero;
  malformed numeric input raises a schema/parse error.

Every normalized Taiwan record carries `provider`, `source`, `source_url`,
`retrieved_at`, `trade_date`, and `status`. Supported status values used here are
`official`, `third_party`, `third_party_fallback`, `stale`, and `error`/schema errors
through raised exceptions. Weekend gaps alone do not make Friday's close stale.

Institutional source quantities are already shares and are not multiplied. Margin
and short quantities are official lots and normalize to shares with a 1,000
multiplier. Dealer net follows the existing contract: proprietary net plus hedge
net; official component and total nets are validated and discrepancies remain
visible instead of being overwritten.

Institutional and margin adapters use the same strict value parser as other Taiwan
official providers: missing tokens remain missing errors for required fields,
explicit zero remains zero, and malformed values raise. Each record includes its
official provider and dataset provenance. FinMind currently supplies price data
only, so there is no reliable institutional/margin fallback; official failures are
raised instead of returned as empty success. Rolling flows and ratios remain in
`app.taiwan.enrichment.factors`, outside raw providers.

## Market-wide institutional amounts

`app.taiwan.enrichment.market_institutional` parses the daily TWSE `fund/BFI82U`
and TPEx `insti/summary` tables. They are the source of the market-level foreign,
investment trust and dealer net figures on the dashboard, screener and research
context. Per-symbol `fund/T86` and `insti/dailyTrade` rows are not summed for these
figures: they are share counts that mix stocks, ETF units and warrants.

- Amounts are TWD. The unit comes from the TWSE `hints` field and from the bracket
  in each TPEx amount column title, after NFKC normalization. Only `元` (x1) and
  `千元` (x1,000) are accepted; any other unit raises.
- Every official category row is stored. Market aggregates use foreign =
  `外資及陸資(不含外資自營商)` (TPEx `外資及陸資(不含自營商)`), investment trust =
  `投信`, dealer = `自營商(自行買賣)` + `自營商(避險)`, and total = the official
  total row, which excludes foreign dealers.
- Each row is checked for buy - sell = net, and the official subtotal and total rows
  are compared with the sum of their categories on buy, sell and net. A mismatch sets
  `has_discrepancy`; official values are never rewritten.
- Rows store `provider`, `source`, `source_url`, `retrieved_at` (Asia/Taipei),
  `trade_date` and `status` as columns. `status` follows the institutional stale
  policy (more than 4 days after the 13:30 close), so backfilled history is `stale`,
  meaning fetched late rather than unsettled.
- A response whose date stamp is missing or differs from the requested day, whose
  `stat` is not OK, or which has no rows counts as no data. HTTP, JSON, format, unit
  and missing-category errors raise.
- A day is written only when both exchanges return rows. When both return nothing,
  the day is a holiday unless the daily bar store or this store already has that date,
  in which case it is a failure. A day with only one exchange is not written.
  Failures after the latest stored day are failures; failures on or before it are
  repairs that keep the stored rows.
- Storage is `data/taiwan/market_institutional/date=YYYY-MM-DD/part.parquet`.
  When institutional flows are refreshed, `run_update` fetches it after the daily
  bars and institutional flows, refetches provisional days among the newest 20
  partitions, and reports failures only in the institutional note without changing
  the institutional status. Backfill with
  `python -m app.taiwan.market_institutional_refresh --days 60 [--end YYYY-MM-DD]`.
- `data_quality.institutional_amount` reports whether the target day has amounts.
  Missing amounts are returned as null and do not change `overall_status`.
- Known gap: when one run catches up several days, a day whose daily bars also fail
  and whose two responses are both empty is taken as a holiday. If a later day is
  stored in the same run, that day is not refetched automatically and needs the
  backfill command.

## Corporate actions and forward adjustment

`app.taiwan.enrichment.corporate_actions` parses the six corporate-action tables
listed above; `app.taiwan.corporate_actions` stores them and derives reference prices
and adjustment factors from them.

- Each row is one `(symbol, date, kind)`, where `kind` is `exdiv`, `reduction`,
  `par_change` or `etf_split` and `date` is the session the official reference price
  applies to. It keeps `pre_close`, `ref_price`, `limit_up`, `limit_down`,
  `open_ref`, `ex_factor` and `official_label` in TWD where they are prices, plus
  `provider`, `source`, `source_url`, `retrieved_at` (Asia/Taipei), `trade_date` and
  `status`. The stored `ex_factor` of a split is the unrounded price ratio; consumers
  take factors from `adjustment_factors`. A row whose prices are `-` (not yet priced)
  is skipped. When one symbol has several kinds on one date, a reduction wins: TWT49U
  excludes dividends paid together with a capital-return or split reduction and
  TWTAUU's resumption price already deducts them. TPEx states no such rule and the
  same order is assumed.
- A response counts as no data only when `stat` is OK, the echoed date range equals
  the request and there are no rows, or when TWSE answers
  `很抱歉，沒有符合條件的資料!` (compared after NFKC normalization) with no data or
  fields. Any other `stat`, a different echoed range, a TPEx response without its
  single table, unit or format errors and HTTP errors raise. ROC dates are
  normalized: TWT49U's 年/月/日 dates accept two-digit ROC years, while slash or
  compact dates with two-digit years (TPEx before 2011) raise. Responses with data
  were verified for 2024 onward.
- Storage is `data/taiwan/corporate_actions/events.parquet`, rewritten atomically as
  a whole; a refetch that returns different figures replaces the row and is logged,
  and rows are never deleted. `fetch_state.json` next to it records, per table, the
  date range successful fetches cover.
- Each publish (the scheduled runs and the settings update button) first fetches the
  window from 10 days before the first republished session to the target session,
  skipping a table whose recorded coverage already spans it. Failures do not change
  the run status: the scheduled run adds them to the daily note, the button logs
  them. Backfill with `python -m app.taiwan.corporate_actions --since 2024-01-01`; it
  always fetches and exits non-zero when any table fails. Corrections inside an
  already covered range need the backfill command.
- The first session on or after an event's date (normally the event session itself)
  takes the reference price as its previous close: enriched stores it as `ref_prev_close` and the official limits as `ref_limit_up` and
  `ref_limit_down` (raw price scale, null on other sessions), and `change_pct` uses
  `ref_prev_close * close / raw_close` as its base. Limit signals always use raw prices
  and official limits on event sessions. Modules that read the daily store for the
  one-day change use the same reference price.
- Forward adjustment: enriched `open/high/low/close` are divided by the product of
  the factors of all later events, so the newest bar equals the raw price and `raw_*`
  keep traded prices. The factor is `pre_close / ref_price` for dividends and
  reductions (cash dividends included); splits and par value changes use the official
  share ratio recovered from the reference prices (0050 on 2025-06-18 is 4). Only
  events dated on or before the target session are applied. Volume before splits,
  par value changes and reductions is multiplied by the share ratio; dividends leave
  volume unchanged. The reduction volume ratio includes any capital refund and is an
  approximation.
- `.taiwan_enriched_factors.json` in the data directory records the factor set of the
  last full publish. When the factors change, or the previous full publish never
  committed, every enriched partition is rewritten as one `EnrichedPublication`, and
  the record is saved before the publication is committed, so a record that cannot be
  saved leaves the publication incomplete. While a publication is incomplete, daily
  bars are served from the memory cache and the screener service answers 503 instead
  of an empty result; the next scheduled run or the settings update button rewrites
  everything and commits. A full rewrite takes about 20 seconds, and in the dividend
  season full rewrites are frequent.
- Intraday, events dated after the recorded factor set are applied on read to the
  history of the daily chart, the watchlist mini chart and the live indicator state,
  so an event session does not show a gap before the evening publish. For a few
  seconds after that publish, until the memory cache is rebuilt, the cached history of
  that day's event symbols is still on the previous scale. If a publish saves the
  record but fails to commit, the memory cache keeps the previous scale while the
  record already has the new one, so those symbols show the gap again until a later
  publish commits. An event that arrives late and is dated on or before the recorded
  factor set is applied at the next full publish. The stock detail page's daily bars
  apply events only through the newest stored session, so on an event day, until
  that session's bars are stored, they differ by that day's factor from the daily
  chart dialog while each chart stays consistent in itself.
- Consumers get factors through `adjustment_factors` or the provider's
  `get_adj_factors`; nothing is written to `data/adj_factor`. Code that reads the raw
  daily store for multi-day values puts every price of one output on one scale:
  `forward_adjusted` for bar ranges, `factors_between` for single lookback closes.
- Not covered: TPEx par value change and ETF split tables (no official endpoint was
  found); the reduction tables' ex-rights reference column, which applies only to a
  loss-offset reduction paired with a cash capital increase; and stock dividends,
  which change the share count but do not restate volume.

## TPEx quote and historical daily separation

TPEx publishes multiple official close-style datasets with different coverage.
They must retain separate provenance and must not overwrite each other:

- `tpex_mainboard_quotes` is the latest official close-style quote source used by
  `get_realtime`. Its Swagger description is "上櫃股票收盤行情"; the OpenAPI does
  not provide a historical date parameter or explicitly enumerate every included
  or excluded trading type.
- `afterTrading/tradingStock` is the reconstructable official monthly historical
  daily-bar source used by `get_daily`. TPEx labels its quantity fields as
  `成交仟股`/`成交仟元`, and the official page states that the data excludes TPEx
  block trades. It remains the canonical Taiwan historical source for indicators
  and backtests.
- `tpex_mainboard_daily_close_quotes` is a broader official daily close dataset.
  It is diagnostic here, not an automatic replacement for historical bars.

The datasets are not contractually required to have identical daily volume. On
2026-08-28, TPEx official records for 6488 reconciled as follows (retrieved
2026-08-30 Asia/Taipei):

```text
tpex_mainboard_quotes                         11,084,000 shares
盤中零股                                         410,427 shares
盤後定價                                          26,000 shares
盤後零股                                           2,113 shares
non-block total                                 11,522,540 shares
tradingStock 成交仟股 (rounded)                     11,523 x 1,000 shares
鉅額交易                                         250,000 shares
tpex_mainboard_daily_close_quotes total        11,772,540 shares
```

The corresponding non-block amount was exactly TWD 11,236,724,997, which the
monthly dataset reports as `11,236,725` thousand TWD. This proves an official
dataset coverage and thousand-unit rounding difference for that observation; it
does not justify a hard-coded reconciliation rule for every date or security.
Consumers must continue to distinguish `official_quote` from
`official_daily_kline` through `source`, `source_url`, `retrieved_at`, and
`trade_date`.

## Historical volume consumers

Taiwan official, FinMind, and Yahoo daily adapters all enter storage with
`volume` normalized to shares/units. Parquet persistence keeps that number
unchanged; enriched restates volume before splits, par value changes and
reductions in post-event shares (see Corporate actions and forward adjustment).
Generic consumers resolve the unit from the canonical symbol: `.TWSE`/`.TPEX`
matrices use `shares`; existing CN matrices retain
`lots` (100 shares per lot).

Turnover is a percentage against `float_shares`, which is a share count:

```text
Taiwan: volume(shares) / float_shares(shares) * 100
CN:     volume(lots) * 100 / float_shares(shares) * 100
```

Relative-volume indicators are unit-invariant. VWAP first converts volume to
shares; matrix cache metadata preserves the resolved unit across build, load,
slice, copy, and live append paths. Mixed Taiwan/CN matrices fail closed because
one matrix cannot truthfully expose a single `volume_unit` for both contracts.
The current execution engine has no absolute-volume participation cap; volume is
only used to identify non-trading rows, while slippage remains a configured bps
model.
# Point-in-time fundamentals

Taiwan company fundamentals use official TWSE/TPEx MOPS open-data records and
keep every record's provider, source URL, retrieval time, status, units, and
revision evidence. The time fields are intentionally distinct:

- `period_end` is the accounting period end; it is never an availability date.
- `published_at` is the verified publication timestamp, when a source provides one.
- `available_at` is the first verified timestamp the record could be consumed.
- `retrieved_at` is when this application fetched the record.

Historical queries are strict: a record is visible only when
`query_at > available_at`. A missing `available_at` is never inferred from the
period end or a date-only `出表日期`; the record is retained as
`data_insufficient` and excluded from backtests. Revisions are stored separately
and an as-of query selects the latest eligible revision rather than today's
latest value.

Official financial amounts reported in `仟元` are normalized to TWD. Missing
values remain null, explicit zero remains zero, and malformed values are errors.
Company monthly revenue and statements are `unsupported` for ETFs.

Share-capital fields are not interchangeable: `total_shares`, `issued_shares`,
`float_shares`, and monetary `capital` retain their own meanings. In particular,
issued shares or capital divided by par value are not used as historical float
shares without an authoritative source.

Phase 6H verified that TWSE/TPEx company profiles expose current issued common
shares, paid-in capital, and par value, but only with date-level report metadata.
These fields are retained as a current/reference `share_capital_record` with
`available_at=None`; `total_shares` and `float_shares` remain null. Official
capital-event sources did not provide a uniform exact-time, effective-date, and
revision chain suitable for historical ingest. Taiwan historical turnover now
fails closed when no verified PIT float denominator exists. The source matrix and
live samples are recorded in
[`taiwan-share-capital-phase-6h.md`](taiwan-share-capital-phase-6h.md).

## Official fundamentals availability investigation (Phase 6C.1)

Investigation on 2026-08-31 distinguished an official timestamp from a safe
end-to-end mapping to an OpenAPI record. MOPS historical material information
exposes exact `發言日期` and `發言時間`, and the official financial-report
document service exposes exact `上傳日期` timestamps. The aggregate OpenAPI
datasets used by the provider, however, do not carry a material-information
serial number, document filename, or another stable identifier that proves
which announcement/upload created each value or revision.

| Dataset | Official evidence | Classification | Production policy |
| --- | --- | --- | --- |
| Monthly revenue (`t187ap05_L`, `mopsfin_t187ap05_O`) | Date-only `出表日期`; some issuers separately publish timed material information, but this is not universal | C: insufficient record-level evidence | Keep `published_at`/`available_at` null |
| Financial statement aggregate (`t187ap06/07`) | Exact PDF upload timestamps exist in the MOPS document service; correction history has a separate official query | C for the aggregate record/revision mapping | Keep aggregate values unavailable until a stable report/revision join exists |
| Valuation (`BWIBBU_ALL`, `tpex_mainboard_peratio_analysis`) | Trade date and current HTTP refresh metadata only; no historical per-record finalization timestamp | C | Keep `available_at` null |
| Dividend board resolution | MOPS material information has exact timestamp | A for the announcement event, C for the aggregate-row join | Do not copy the timestamp by subject-text matching |
| Dividend ex-date/basis-date announcement | MOPS material information has exact timestamp | A for the announcement event, C for the aggregate-row join | Preserve lifecycle events separately when a stable key is available |
| Dividend payment | Timed announcements exist for some issuers, not a universal keyed source for current aggregate rows | C | Keep missing lifecycle fields null |

Historical evidence included:

- `2330`: July 2026 revenue material information at `2026-08-10
  13:51:09+08:00`; Q2 financial-report PDF upload at `2026-08-14
  13:59:44+08:00`; board dividend resolution at `2026-08-11
  18:53:34+08:00`; ex-date announcement at `2026-08-11 19:01:29+08:00`.
- `6488`: Q2 financial-report board approval at `2026-08-04
  15:16:23+08:00` and PDF upload at `2026-08-07 15:22:28+08:00`;
  dividend resolution and basis-date announcements at `2026-03-03
  16:05:53+08:00` and `16:06:25+08:00`. No equivalent July-revenue
  material-information event was found.
- `2881`: Q2 financial-report board approval at `2026-08-20
  16:48:04+08:00` and PDF upload at `2026-08-28 14:09:26+08:00`.
  Its July disclosure was a consolidated self-reported profit/loss event, not
  an unambiguous match to the monthly-revenue aggregate.

These timestamps prove that the official event/document streams can express
exact times. They do not yet prove that an aggregate value was public at the
same instant. HTTP `Date` is response time and aggregate `Last-Modified` is a
resource/cache timestamp; neither is historical record-level availability.
Likewise, a statutory filing deadline does not prove that a particular issuer
filed successfully by that time. No date-level next-trading-day fallback is
therefore adopted.

The future integration boundary should accept only an official stable join,
such as issuer + report period + report/revision identifier. Subject-text
matching is not sufficient. Once joined, the existing strict rule remains:
`query_at > available_at`; equality is unavailable. Original and corrected
reports must keep their own upload/announcement timestamps.

Phase 6G re-verified this boundary against the live MOPS financial-report
document service and both official OpenAPI schemas. Exact 2026 Q1/Q2 document
timestamps were reproducible for 2330, 6488, and 2881, but the aggregate rows
still exposed no document or revision identifier. Monthly revenue and valuation
also exposed no new stable exact-time identity. No production availability was
upgraded; the evidence and rejection matrix are recorded in
[`taiwan-fundamentals-availability-phase-6g.md`](taiwan-fundamentals-availability-phase-6g.md).

## Dividend lifecycle stable event ingest (Phase 6D)

The official source is the MOPS historical material-information service:

- search: `https://mops.twse.com.tw/mops/api/t05st01`
- detail: `https://mops.twse.com.tw/mops/api/t05st01_detail`
- user-facing query: `https://mops.twse.com.tw/mops/web/t05st01`

The search result supplies the detail parameters `marketKind`, `companyId`,
`enterDate`, and `serialNumber`. Their deterministic encoding
`marketKind/companyId/enterDate/serialNumber` is the event and revision
identity. The detail response supplies the exact official speech date/time,
subject, and structured description. Therefore these events use
`availability_policy=exact_timestamp`, `available_at=event_timestamp`, and
`availability_confidence=verified`. Point-in-time queries remain strict:
an event is visible only when `query_at > available_at`.

Classification is deliberately conservative. Phase 6D supports an explicit
issuer-level board dividend-resolution subject, an explicit ex-date subject,
and an explicit basis-date/distribution-record subject. Subsidiary and preferred
share subjects are excluded from the ordinary-share stream. Generic shareholder
meeting resolutions, payment announcements, paid status, corrections, and
subjects that do not identify one lifecycle stage remain unresolved; no fuzzy
matching is used.

The material event and the existing TWSE `t187ap45_L` / TPEx
`mopsfin_t187ap39_O` dividend aggregation remain separate records. Aggregation
rows can validate amounts and dates, but their date-only metadata and lack of a
material-event serial identity do not authorize copying an exact timestamp.
Phase 6D parses amounts only when they are stated in the official event detail.
Missing event fields remain null, while explicit zero remains zero.

Live official read-back on 2026-08-31 verified:

- `2330.TWSE`: board resolution
  `sii/2330/1150811/3` at `2026-08-11T18:53:34+08:00`, cash dividend
  `7.0`, stock dividend `0.0`; ex-date announcement
  `sii/2330/1150811/4` at `19:01:29+08:00`, ex-date `2026-12-10`,
  basis date `2026-12-16`, payment date `2027-01-07`.
- `6488.TPEX`: board resolution
  `otc/6488/1150303/1` at `2026-03-03T16:05:53+08:00`, cash dividend
  `5.7`, stock dividend `0.0`; basis-date announcement
  `otc/6488/1150302/2` at `16:06:25+08:00`, ex-date `2026-07-16`,
  basis date `2026-07-22`, payment date `2026-08-14`. The identity retains
  the official `enterDate` even where it differs from the displayed speech date.
- `2881.TWSE`: ordinary-share board resolution
  `sii/2881/1150430/7` at `2026-04-30T18:06:38+08:00`, cash dividend
  `4.25`, stock dividend `0.0`; basis-date announcement
  `sii/2881/1150612/15` at `2026-06-12T17:50:31+08:00`, ex-date
  `2026-07-01`, basis date `2026-07-07`, payment date `2026-07-31`.

ETF company dividend events are outside this stream. For example,
`0050.TWSE` returns no company lifecycle events without making a network
request; ETF distributions remain a separate structured-data phase.

Taiwan Market Contract v1.1 should add `period_start`, `period_end`,
`published_at`, `available_at`, `revision`, `normalized_unit`, `raw_unit`, an
availability evidence URL/identifier, and a policy type (`exact_timestamp`,
`date_level_conservative`, or `insufficient`). Contract v1 remains unchanged.

# ETF structured data foundation (Phase 6E)

ETF data is a separate domain from company fundamentals and company dividend
events. The security master retains identity and stable classification only;
daily NAV, AUM, units, distributions, and holdings must not be stored there.

## Official dataset classification

| Class | Dataset | Fields and units | Frequency / history | Decision |
|---|---|---|---|---|
| A | TWSE OpenAPI `opendata/t187ap47_L` (基金基本資料彙總表) | product name/type, benchmark, inception/listing dates; `發行單位數/轉換數` retained as issued units | current report with date-only `出表日期`; no proven publication time | profile is production-usable; current issued units are retained with `available_at=None` and are historically unavailable; outstanding units remain unknown |
| B | TWSE e添富 NAV chart and `ETF/etfDiv` report | dated NAV/premium-discount observations; ETF ex/basis/payment dates and distribution amount per unit | historical values are queryable, but no record publication timestamp, stable revision identity, or approved availability rule exists | historical reference only; not parsed into PIT history |
| B | TPEx ETF information-center product service and monthly report | recent per-unit NAV, four recent payments, current/month-end scale fields | recent or monthly partial history; no exact record availability or revision identity | reference only; capability remains `data_insufficient` |
| C | Issuer holdings pages or top-holding displays | holdings/weights vary by issuer and may be partial | completeness, cash/derivative coverage, schema, and availability are not consistently proven | no production holdings ingest |

## Semantic boundaries

- `nav` means final per-unit NAV; `estimated_nav`/iNAV is a separate field;
  neither is substituted for market close.
- Premium/discount is calculated as `market_price / nav - 1` only when both
  values use the same date and compatible currency/unit semantics.
- AUM (TWD), outstanding ETF units (units), issued units, and beneficiary units
  are distinct concepts. No value is inferred from `AUM / NAV`.
- Distribution uses TWD per ETF unit and is not a company dividend lifecycle.
- Holdings require an explicit `full`, `partial`, or `unknown` coverage marker;
  a top-N list is never a complete portfolio.
- Missing markers become `None`; numeric zero remains a real zero; malformed
  numbers are schema/parse errors.
- Historical selection is strict: `query_at > available_at`. Date-only source
  metadata does not create an `available_at`, so it cannot enter backtests.
- Leveraged/inverse direction and multiplier remain absent unless an explicit
  official field proves them; ticker/name suffixes are not evidence in this ETF
  structured-data domain.

Phase 6I verified TWSE historical NAV chart responses for `0050`, `00631L`, and
`00632R`, and TWSE distribution rows for `0050` back to 2020. It also verified
the genuine TPEx ETF `006201` through the official ETF information-center API.
These sources remain reference-only because none supplies an exact publication
timestamp, approved `available_at`, and stable revision identity. No ETF history
provider or store was added. The endpoint and rejection evidence is recorded in
[`taiwan-etf-history-phase-6i.md`](taiwan-etf-history-phase-6i.md).

## Current/reference product surface (Phase 6J)

The authoritative machine-readable capability matrix is
`app.taiwan.current_data.CAPABILITIES`. It separates domain status from intended
usage through these values:

- `current_reference`: may be shown as the latest official reference value but
  is not eligible for historical queries;
- `historical_reference`: official historical evidence exists, but there is no
  production ingest or verified point-in-time availability;
- `pit_historical`: the production record has a verified availability seam;
- `unsupported`: no production capability exists.

`status=data_insufficient` and `usage_scope=current_reference` are intentionally
compatible. A missing `available_at` still means historically unavailable; it
does not hide a current/reference value from the dedicated product surface.

The Taiwan-specific API exposes the code matrix without network access at
`GET /api/taiwan/capabilities`. `GET /api/taiwan/data/{symbol}` resolves the
instrument type through Security Master and returns isolated sections:

- stocks: monthly revenue, financial statement, valuation, and share capital;
- ETFs: official profile and the current/reference snapshot.

The API reuses `TaiwanHybridProvider`, `TaiwanOfficialFundamentals`, and
`TaiwanOfficialETFData`; it does not duplicate parsing. Dividend lifecycle events
remain on their explicit time-window seam and are not fetched by every current
request. Phase 6I historical NAV and distribution evidence remains documentation
only. Current/reference sections do not enter verified factors, the historical
screener, turnover history, or backtests.

# Taiwan fundamentals point-in-time rule

`TaiwanFundamentalStore` (`app/taiwan/fundamentals.py`) is the point-in-time
seam. `latest_as_of` returns the newest revision whose `available_at` is set and
strictly earlier than `query_at`, so a record with `available_at=None` is never
returned and a later revision cannot replace an earlier one before its own
availability time. `app/taiwan/research_context.py` is its consumer.

The verified-factor layer that once sat on top of this store — a fixed EPS / ROE
/ PE / PB / dividend-yield factor table, the
missing / data_insufficient / unsupported / malformed status taxonomy, and the
per-status coverage report — was removed. It had no caller outside its own test,
and no screener, API or job ever read it. Only the point-in-time rule above
survives.
