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

ETF classification for price limits:

- TWSE ETFs come from `t187ap47_L`. When 「是否包含國外成分股」 is 是 or 否, every fund type except
  futures trusts (index, active, balanced, linked, offshore, foreign-currency listing, bond) is
  `official_metadata` with scope `foreign` or `domestic`. 「指數股票型期貨信託基金」 maps to the
  `futures` category (`inverse` when the multiplier is negative, e.g. 00716R) and is confirmed
  only when the flag is 是; otherwise it stays unconfirmed (fail-closed).
- The ISIN path with injected TWSE product metadata carries the same flag in
  `OfficialEtfProductMeta.underlying_scope` and uses it for futures trusts (same rule as above)
  and for bond fund types (scope from the flag; `foreign` when the flag is absent). Leveraged and
  inverse fund types on that path still take scope from index/name keywords.
- TPEx ETFs come from the ISIN table and are classified by CFI code. CFI attributes carry no
  country, so `CEOGEU` stays `unknown`. `CEOIBU`, `CEOJBU` and `CEOGBU` are `bond` with foreign
  scope; every listed TPEx bond ETF is 「國外債券」 on the TPEx ETF product spec.
- Audited TPEx entries in `OFFICIAL_ETF_RULE_PROFILES` override the CFI result:
  006201, 00888, 00928 are domestic-component (10%); 00877, 00886, 00887, 009822, 00411A,
  00858, 00955, 009806, 009807, 009814, 009815, 009823, 009825, 00998A are foreign-component
  (no limit; the last ten are the CEOIEU entries of the TPEx 「國外成分股ETF」 list). 00980T stays
  unknown. Evidence: the TPEx ETF product spec API
  (`https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=<domestic|foreign>&code=<code>&response=json`,
  fields 「ETF類別」 and 「升降幅度」, checked 2026-10-07) and the 2026-09-23 TPEx daily quotes, where
  only 006201, 00888 and 00928 had real limit prices and the other ETFs showed 9999.95/0.01
  (`docs/audit-2026-09-24/sections/A05-limits-signals.md`).
- Price limits follow component scope only, per the
  [TWSE trading rules](https://www.twse.com.tw/zh/products/system/trading.html): domestic-component
  ETFs (index or active) 10%, domestic leveraged/inverse 10% times the multiplier; foreign-component
  ETFs (including active), offshore ETFs and futures trusts on foreign commodity futures have no
  limit. A bond ETF has no limit because it is foreign-component, not because it is a bond ETF.

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
- TPEx ETF directory: the ISIN table `isin.twse.com.tw/isin/C_public.jsp?strMode=4`
- TPEx ETF product spec (management style): `www.tpex.org.tw/www/zh-tw/ETF/detail`
- TWSE market open/closed schedule: `rwd/zh/holidaySchedule/holidaySchedule` (see Trading calendar and holidays)

TPEx ETF identity is retained from the official ISIN directory. Product
classification remains `data_insufficient` until an official structured metadata
source is integrated; code/name heuristics are not used to fill this gap. This is
the Phase 6E structured product data; the price-limit classification above and the
security master's management style (see Security master refresh) are separate.

## Normalized contract

- `volume` is shares/units. TPEx monthly `成交仟股` is multiplied by 1,000.
- `amount` is TWD. TPEx monthly `成交仟元` is multiplied by 1,000.
- ROC dates such as `115/08/30` and `1150830` normalize to `2026-08-30`.
- Security master `listing_date` is ISO `YYYY-MM-DD`. The directories publish ROC
  7-digit (TWSE ETFs), `YYYYMMDD` (companies) and `YYYY/MM/DD` (TPEx ISIN) dates; all
  three are normalized when written, an older cache is normalized in memory when read
  (the file is not rewritten), and an unparseable date becomes null, which leaves an
  ordinary stock's price limit unknown.
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

## Security master refresh

`TaiwanSecurityMaster.refresh` (`app/taiwan/universe/service.py`) rebuilds
`data/taiwan/security_master.parquet` from the official company directories
(`t187ap03_L`, `mopsfin_t187ap03_O`), the TWSE ETF directory (`t187ap47_L`) and the
TPEx ISIN table.

- When it runs: a full-market daily update (the 16:30 and 21:40 scheduled jobs, or
  the CLI without `--symbols`) and the settings page update button refresh the master
  before fetching daily bars, but only when the target session is today
  (Asia/Taipei), the time is 16:00 or later and today is a potential trading day.
  `data/taiwan/security_master.refresh.json` records the last success, so later runs
  on the same day skip the refresh; only a success whose TPEx management style lookups
  all finished counts (`styles_complete`), so a first load or a run whose lookups
  stopped early is followed by another refresh that day. A failed or busy refresh only adds a note; the
  update continues with the previous list.
- CLI: `python -m app.taiwan.daily_update --refresh-master` refreshes at any time and
  then runs the update; `--master-dry-run` fetches the official lists and prints the
  changes as JSON without writing anything or running the update. `--force` with
  either lets the refresh accept count drops and removals beyond the limits below;
  with `--refresh-master` it also makes the daily update refetch its whole date range.
- Validation happens before anything is written. Both company directories must be
  non-empty and every record well-formed. Against the previous master, each
  exchange's active stock and ETF counts must stay at or above 90%, and at most
  max(20, 2% of the previous count) symbols per exchange may disappear. A failed
  refresh leaves the cache file and the loaded master unchanged.
- Delisting: a previously active symbol missing from the official list stays in the
  master with `listing_status="delisted"` and `delisted_at` (the refresh day, kept
  while it stays missing). `get_instrument` still finds it; universes leave it out;
  market, industry and abnormal-move statistics count it for target days before
  `delisted_at`. A symbol that reappears becomes active again.
- Writing: every row of one refresh carries one Asia/Taipei `updated_at`. The file is
  written to a temporary file and atomically replaced; the replace is retried briefly
  while a Windows reader holds the file. A lock file next to the cache makes a second
  refresh in this or another process return busy. Each process reloads the master
  when the file changes on disk.
- `data/taiwan/security_master.refresh.json` holds `last_attempt`, `last_success` and
  `error`, and after a success `styles_complete`, the per-exchange counts, `added`, `removed`, the number
  of reclassified symbols and the management style lookups.
- First load: only a missing cache file is downloaded, through the same refresh
  without the management style lookups. An unreadable cache is never replaced by a
  download on first load or by the automatic refresh; the API, strategy endpoints
  included, answers 503 instead. A manual `--refresh-master` with an unreadable cache
  refreshes against an empty baseline (no count comparison) and overwrites it.
- The bootstrap data package keeps a local master whose newest `updated_at` is at
  least as recent as the bundled one, carries local delisted rows and management
  styles into a bundled one it installs, and reloads the master at once.
- TPEx ETF management style: the ISIN table has no active/passive marker, and the
  bond ETF tax exemption needs one. A refresh carries a known style forward and looks
  up only TPEx ETFs without one on the TPEx ETF product spec
  (`https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=<bond|domestic|foreign>&code=<code>&response=json`,
  field 「ETF類別」: 主動 is active, 被動 is passive). Lookups are one per second and
  stop after three consecutive request failures. An ETF whose style stays unknown
  pays the ordinary ETF rate; in the 2026-10-07 review 00838B and 00992B could not be
  resolved. TWSE ETFs take the style from the official fund type.
- Known gap: a symbol delisted before the first master snapshot was never recorded,
  so for target days before that snapshot the universe may be undercounted.

### New listings

Every full-market daily run, and the update button, looks at the newest 20 stored
partitions (`REPAIR_WINDOW_PARTITIONS`) for days that lack a row of a supported
active stock or ETF whose `listing_date` falls inside that window, and refetches
those days through the snapshot path. Only days before the listing's first stored
row count: once it has a row, a later day without one is treated as a halt, not a
gap. A listing day that could not be fetched makes the daily status partial with a
note; it is retried on the next run only while it precedes the listing's first
stored row (otherwise refetch it with `--symbols`). A symbol listed before the
window, halted or delisted never triggers a refetch. When the refresh changed the
master, the update button also republishes enriched; the scheduled runs publish
every time, and the recorded limit rules decide whether every partition is
rewritten (see Corporate actions and forward adjustment).

### Taiwan depositary receipts

- A TDR is `instrument_type="stock"` with `security_class="tdr"`. The class comes from
  the company directory's industry code 91, a six-digit code starting with 91, or a
  name ending in -DR; a cache written before the column existed derives it on read.
  Code 91 marks TDRs and is not an industry, so a TDR's industry is null: industry
  statistics count it as UNCLASSIFIED, RELATIVE_STRENGTH_OUTLIER is not applicable,
  and the research context marks its industry not applicable.
- The official share count of a TDR is the underlying company's issued shares, not
  receipts, so TDR turnover and market cap are null (`as_instruments` of the share
  capital store leaves TDRs out). Par value keeps the official text and its currency;
  `par_value_twd` is set only when the text states NTD.
- Securities transaction tax is 0.1%
  ([MOF Q&A](https://www.etax.nat.gov.tw/etwmain/tax-info/understanding/tax-q-and-a/national/securities-transaction-tax/filing/LeJ1WWV),
  ruling 台財稅第811672688號函). Day trades also pay 0.1%: that is our reading of
  Securities Transaction Tax Act art. 2-2, whose reduced rate covers listed or OTC
  stocks only.
  The initial-listing no-limit window does not apply; the limit is 10%. Strategies
  that trade only stocks still include TDRs.

### Per-symbol daily refresh

`python -m app.taiwan.daily_update --symbols ...` fetches TPEx symbols from the TPEx
daily quotes of each day (`www/zh-tw/afterTrading/dailyQuotes`, the snapshot source), so
volume and amount are exact shares and TWD including block trades. TWSE symbols keep
the per-symbol provider. A TPEx symbol only has to be a supported stock or ETF in the
master, whatever its listing status, so a delisted one can be backfilled for the days
it traded. The path only fills missing rows: stored rows, including a provisional
partition, are never rewritten. Symbols that could not be refreshed are listed with
their reason in the daily note. When rows were written, enriched is republished from
the earliest written day, not only the recent window.

### Daily data integrity report

`app/services/data_integrity.py` checks the newest 20 sessions before today. The
calendar is the dates holding a stored TAIEX (`t00.TWSE`) bar or a daily partition,
from the first stored partition on, so holidays are never gaps. Each day is reported
as `missing` (a TAIEX date without a daily partition), `one_exchange` (no TWSE or no
TPEx rows), `provisional` (written on its own day, except the newest stored day) or
`enriched_stale` (enriched partition absent or older than the daily partition). The
report only feeds the log written 30 seconds after startup: nothing is repaired,
enabling realtime quotes is not blocked, and the legacy A-share `kline_*` tables are
not read. Taiwan bars are not written to or read from the legacy `kline_daily` table
either: the default start of backtest streams and the minute chart's trading day take
their dates from the `taiwan/daily` partition names.

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
  last full publish and, under `limit_rules`, each stored symbol's price-limit rule
  (limit percentage, tick table and initial-listing window). Every enriched partition
  is rewritten as one `EnrichedPublication` when the factors change, when the previous
  full publish never committed, when a symbol with rows before the refreshed window
  changed its limit rule (for example after a security master reclassification), or
  when a published partition outside the window has another schema than this run's
  output (only parquet footers are read). A record saved before `limit_rules` existed
  adopts the current rules as its baseline without a rebuild, so a rule change that
  happened before that first run needs `python -m app.taiwan.enriched_refresh --full`.
  Corrections of an event's official `ref_limit_up/down` are not part of the record and
  do not trigger a rebuild. The record is saved before the publication is committed,
  so a record that cannot be saved leaves the publication incomplete. While a
  publication is incomplete, daily bars are served from the memory cache and the
  screener service answers 503 instead of an empty result; the next scheduled run or
  the settings update button rewrites everything and commits. A full rewrite of the
  history since 2024-01 took 48 to 116 seconds in the 2026-10 logs, and in the dividend
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

台股的 `float_shares` 只是換手率分母的欄位名稱：由 `TaiwanShareCapitalStore.as_instruments()` 提供，值是已發行普通股扣除私募股（2026-10-10 第 4 階段起），不是自由流通股；ETF、TDR 沒有分母，換手率為空值。

Relative-volume indicators are unit-invariant. VWAP first converts volume to
shares; matrix cache metadata preserves the resolved unit across build, load,
slice, copy, and live append paths. Mixed Taiwan/CN matrices fail closed because
one matrix cannot truthfully expose a single `volume_unit` for both contracts.
The current execution engine has no absolute-volume participation cap; volume is
only used to identify non-trading rows, while slippage remains a configured bps
model.

## Trading calendar and holidays

Exchange holidays come from the TWSE market schedule
(`https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=json&date=YYYY0101`,
module `app/taiwan/realtime/holidays.py`). The payload lists every closure and two kinds of
session markers; columns are located by the field names 「日期」 and 「名稱」, and a payload
without them is treated as unrecognised, never guessed.

- A name ending in 「交易日」 (e.g. 「國曆新年開始交易日」, 「農曆春節前最後交易日」) is a trading
  day unless it contains 「無交易」; every other listed date is a closure, including the
  settlement-only days 「市場無交易，僅辦理結算交割作業」.
- Each year is cached at `data/taiwan/holidays/{year}.json` (atomic write) with the closures,
  the listed trading days and the fetch date. Lookups only read this cache; they never fetch.
- The scheduler job `taiwan_holiday_refresh` runs 30 s after start and every 3 hours. It fetches
  the current year, and from November also the next year, until one fetch per year per day
  succeeds or confirms the year is not published. A failed or unrecognised fetch keeps the
  cached file and is retried on the next run.
- A year with no cached schedule is unverified, never confirmed trading: weekdays behave as
  before (quote polling runs, the MIS trade-date check rejects stale quotes).

The default `TaiwanTradingCalendar()` reports a cached closure as a non-trading day, so the
latest-trading-date resolution, refresh loops, `get_market_status` and quote polling skip it.
The 16:30 and 21:40 scheduled updates still run on a cached closure (the cron is Monday to
Friday): the run targets the previous trading session and finalizes it. The publish takes the
newest stored session as "today", so a holiday or after-midnight republish no longer nulls that
session's turnover.

Unscheduled closures (typhoon days) are not in the schedule. The settings switch
「今日休市（停止盤中輪詢）」 (`PUT /api/settings/preferences/market-closed-today`, stored as
`taiwan_market_closed_date` in preferences) marks today as closed: quote polling stops and
alerts treat the session as closed. It applies only on the date it was set and does not
change the trading calendar or the scheduled updates.

On a weekday the cached schedule does not list as closed, polling also probes for an
unscheduled closure: while no today-dated MIS quote has been seen this session and it is past
09:05 Asia/Taipei, quotes are polled every 5 minutes instead of every cycle. Once one today-dated
quote has arrived, polling stays at the normal interval for the rest of the session.

## Intraday quotes and the closing auction

The session model is pre-open, continuous trading until 13:25, the closing auction
13:25–13:30:00, then post-close. During the closing auction quotes are still polled, but
monitor rules and alerts pause and are evaluated once on the 13:30 result.

MIS quotes are classified by their own quote time and trade date, not by the wall clock:

- `ts=1` is a trial match (`TRIAL_MATCH`) during either auction; it is shown but never counts
  as a live trade.
- A today-dated quote with `ts=0` and a quote time at or after 13:30:00 is the closing result
  (`CLOSE_FINAL`). It is accepted as today's bar after the close, its volume becomes its own
  13:30 minute bar, and it is never marked stale.
- Any other today-dated quote during continuous trading or the closing auction is realtime;
  everything else, including a quote dated on an earlier day, is an official snapshot and
  never becomes today's bar.

Final sync: after 13:30 the closing round is complete only when the fetch succeeded and every
pool symbol that traded today has a `CLOSE_FINAL` quote; a STOCK_DAY_ALL or daily-K fallback
never counts. Untraded symbols do not block it, and neither does a symbol settled without a
close (`settled_no_close`: it traded today, but after 13:30 MIS only has an official snapshot
stamped before 13:30). A round whose records carry no status field at all (a provider that
reports none) counts as complete once the fetch succeeds. The round is retried on every poll until
14:30, then given up with `closing_quote_missing` and one partial alert pass. If MIS publishes
the closing results in batches, the round can finish once every traded symbol has one, before
the exchange has published them all.

These rules were checked against MIS samples recorded on 2026-10-08 at 08:45, 09:00, 09:32
and 14:56 (`backend/tests/fixtures/mis_2026_10_08/`). No sample from 13:25–13:30 was
recorded, so the closing-auction quotes in the tests are edited from those samples. A live
check was planned for 2026-10-12; no result had been recorded as of 2026-10-25.

Official close-style quotes (STOCK_DAY_ALL and the TPEx close quotes) are cached for 10 minutes
per URL with single-flight loading (a failure is cached for 30 seconds). They are a fallback
only after the close and on non-trading days; during pre-open, continuous trading and the
closing auction the realtime chain is MIS, then Yahoo, with no official download. Yahoo's open
is the session bar's `indicators.quote[0].open`, or null when that bar is not today's.

Intraday volume is MIS board-lot (整股) volume: it excludes odd lots, block trades and
after-hours fixed-price trades. Intraday views label it 「盤中為整股量（不含零股、鉅額、盤後定價）」,
turnover and volume ratios use it until the official daily bar is stored, and the intraday
amount is shown as 「—」 (MIS gives none). Once the official bar is published, volume, amount
and turnover come from it. The opening volume ratio is extrapolated to a full session from
the elapsed session time, counted in whole minutes and at least 15 minutes (a factor of at
most 18); the value and `signal_volume_surge` both use it and the chart labels it 「推估」.

Minute bars: when more than 60 seconds of trading time passed between two polls, the
cumulative volume change is stored as `unattributed_volume` instead of being credited to one
minute; after a restart the stored total is the baseline. Volume before the first snapshot of
the day is unattributed too. The intraday average-price line shows its coverage (attributed /
(attributed + unattributed)) and is hidden below 80%; charts no longer connect missing minutes.

## Intraday enriched rows

Taiwan intraday enriched rows come from the pipeline that publishes the enriched table
(`app/taiwan/live_enriched.py`): up to 1,200 stored sessions before today plus today's live bar
run through `compute_taiwan_enriched`, so today's row matches the row the evening publish
writes, limit signals and streaks included. Non-Taiwan rows keep the incremental
`compute_enriched_today`. Until the history is prepared after a start, Taiwan rows are skipped
rather than computed from a short history.

Today's corporate actions are listed only after the session, so a quoted previous close that
differs from the stored close stands in for the event's reference price. It is used only when
the store holds the previous trading session for that exchange, no listed event falls after
the symbol's last stored session, and the difference is at least 0.005; the kind is guessed
from the price factor (below 1 a capital reduction, above the split factor a par-value change,
otherwise a dividend). When the store is behind, no stand-in is made and a warning is logged
once a day.
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

財報（`financial_statement`）的欄位只有官方最新一季，屬目前參考值，不建立歷史、不進回測。損益欄位是年初至今累計（`period_start` 為該年 1 月 1 日），會計類別由 `t187ap06` 六個類別（ci、basi、bd、fh、ins、mim）中唯一有該公司資料的那份決定。多個官方欄位名依序取第一個非空白的值：

| 欄位 | 官方欄位（依序） | 說明 |
| --- | --- | --- |
| `revenue` | 營業收入、收益合計、淨收益 | 累計 |
| `gross_profit` | 營業毛利（毛損）淨額、營業毛利（毛損） | 累計；已查證一般業（ci）有此欄，其他類別未查明；沒有此欄時為 null，不是 0 |
| `operating_income` | 營業利益（損失） | 累計 |
| `net_income` | 本期淨利（淨損）、本期稅後淨利（淨損） | 累計 |
| `cumulative_eps` | 基本每股盈餘（元） | 累計，單位元 |
| `assets`、`liabilities`、`equity` | 資產總計／資產總額、負債總計／負債總額、權益總計／權益總額（`t187ap07`） | 季末時點值 |

毛利率不另存，由前端以 `gross_profit ÷ revenue` 計算；毛利缺值、營收缺值或營收為 0 時顯示 `--`。

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

第 4 階段（2026-10-10）起，每個交易日在自己的盤後發佈時算出的換手率會保留，之後的重新發佈沿用已發佈的值；重算更早的日子仍然 fail-closed，不用目前股本回推。細節見上述文件的「第 4 階段」小節。

個股頁「股本資料」的來源：

- 先即時查詢官方公司資料（`source` 為 `twse:share_capital_record` 或 `tpex:share_capital_record`），來源列顯示「即時查詢」與取得時間。
- 只有清單取不到時（非 JSON、HTTP 錯誤、逾時、格式改變，包括失敗快取期間），才退回盤後排程落地的 `data/taiwan/share_capital.parquet`（`TaiwanShareCapitalStore`，接點在 `TaiwanOfficialFundamentals.share_capital`）。這時 `source` 為 `twse:share_capital_store` 或 `tpex:share_capital_store`，`retrieved_at` 是該次盤後刷新的時間，`period_end` 是出表日期，`values.live_error` 記錄即時查詢失敗的原因。前端來源列顯示「即時查詢失敗，改用盤後存檔」與刷新時間。
- 存檔只保存已發行普通股數，所以退回存檔時 `capital_twd` 與面額欄位是 null，畫面顯示 `--`。
- 清單取到了但沒有該代號時，維持 `LookupError`，不退回存檔：官方當日清單沒有這檔，代表它可能已下市或改代號，存檔的舊值不該掩蓋這件事。存檔也沒有該代號或檔案不存在時，維持 error 段。
- 兩種來源都是目前參考值：`status=data_insufficient`、`available_at=None`，不進歷史或回測。

官方 JSON 清單的共用取值（`TaiwanOfficialFundamentals._rows`）：

- 回應無法解析為 JSON 時（例如 TWSE 回 HTTP 200 的 HTML 資安阻擋頁），錯誤原因寫成 `upstream returned non-JSON response (<Content-Type>)`。
- 取值失敗後，同一個 URL 在同一個台北日期內 5 分鐘（`FAILURE_CACHE_SECONDS`）不再重打，直接回報同一個錯誤。
- 成功的清單仍然每個台北日期只下載一次；空清單仍然不快取。


## 季度財報快照（僅供顯示）

`TaiwanQuarterlyFinancialStore`（`app/taiwan/quarterly_financials_store.py`）從上線起每季保存官方 OpenAPI 的最新一季財報，供個股頁「季度財報」區塊顯示。官方清單只有最新一季，無法回補，季數只能逐季累積；PD2 決定不使用 MOPS 舊站（`docs/stock-positioning-signals-plan/README.md`）。

邊界：

- 用途標記為 `historical_reference`（能力矩陣 `quarterly_financial_snapshot`），`status=data_insufficient`、`available_at=None`。
- 不得進入回測、選股歷史、比較頁的歷史比較日期與任何 as-of 查詢。`tests/test_taiwan_quarterly_financials.py` 以 import 掃描確認 `app/backtest`、`app/strategy` 與選股相關模組都沒有直接或間接引用這個 store 與服務。
- 不寫 `financials/metrics/part.parquet`（回測財務因子會讀這個路徑）。
- 畫面標示「回溯取得，可能為更正後數值」。

來源與刷新：

- 端點：TWSE `opendata/t187ap06_L_{類別}`（損益）、`opendata/t187ap07_L_{類別}`（資產負債），TPEx `mopsfin_t187ap06_O_{類別}`、`mopsfin_t187ap07_O_{類別}`。類別為 ci、basi、bd、fh、ins、mim，共 24 份清單。
- 時點：接在盤後排程 16:30 與 21:40 兩班的最後（`app/jobs/daily_pipeline.py` 的 `refresh_quarterly_financials`），不論日 K 更新成功與否都會執行。失敗只記錄，不影響同班其他工作。
- 取值：經 `TaiwanOfficialFundamentals.market_rows`，與個股頁「財務報表」卡共用同一個實例的每日快取與 300 秒失敗快取，不另做全市場下載。每份清單每個台北日期最多成功下載一次；同日第二班與個股頁都直接用快取。
- 某類別的損益或資產負債清單失敗時，略過該類別；同一公司出現在多個類別時不寫入並記錄。
- 存檔：`data/taiwan/quarterly_financials/year=YYYY/quarter=Q/part.parquet`，一季一個分區，以暫存檔加 `os.replace` 原子取代（`write_atomically`；Windows 上檔案被開啟時短暫重試，仍失敗就拋出，原檔不動）。既有分區讀不出來時不覆寫。

每列欄位（金額單位元，官方仟元已換算；EPS 單位元）：

| 欄位 | 官方欄位（依序取第一個非空白） | 說明 |
| --- | --- | --- |
| `symbol`、`exchange`、`accounting_category` | 公司代號／SecuritiesCompanyCode；清單類別 | |
| `fiscal_year`、`fiscal_quarter`、`period_end` | 年度／Year、季別／Season（民國年加 1911） | 期末日為該季最後一天 |
| `revenue` | 營業收入、收益合計、淨收益、收益、收入 | 累計；銀行業（basi）沒有營收總額欄位，為 null |
| `gross_profit` | 營業毛利（毛損）淨額、營業毛利（毛損） | 累計；只有一般業（ci）有，其他類別為 null |
| `operating_income` | 營業利益（損失）、營業利益 | 累計；ci、ins、bd 有，其他類別為 null |
| `net_income` | 本期淨利（淨損）、本期稅後淨利（淨損） | 累計 |
| `parent_net_income` | 淨利（淨損）歸屬於母公司業主、淨利（損）歸屬於母公司業主 | 累計；後者為 basi、bd 的欄名 |
| `cumulative_eps` | 基本每股盈餘（元） | 累計 |
| `assets`、`liabilities`、`equity` | 資產總計／資產總額、負債總計／負債總額、權益總計／權益總額 | 季末時點值 |
| `parent_equity` | 歸屬於母公司業主之權益合計、歸屬於母公司業主權益合計、歸屬於母公司業主之權益 | 季末時點值；後兩者分別為 bd、fh 的欄名 |
| `source`、`source_url` | `twse_openapi`／`tpex_openapi`；損益與資產負債清單的 URL | 日後若放入第三方回補資料，以 `source` 區分 |
| `first_retrieved_at`、`last_retrieved_at`、`revised_at`、`revised_fields` | | 台北時間 ISO 字串 |

欄位名稱為 2026-10-29 對 24 份清單實測的結果。資產負債列的年度季別與損益列不同時，資產負債欄位為 null。

同一（symbol、年度、季別）再次取得：

- 比較會計類別、期末日與所有數值欄位。相同時只更新 `last_retrieved_at`。
- 不同時以新值覆蓋，`revised_at` 記錄取得時間，`revised_fields` 記錄變動欄位，並以 warning 記下新舊值。

查詢與口徑（`GET /api/taiwan/stocks/{symbol}/quarterly-financials`，`TaiwanQuarterlyFinancialsService` 只讀存檔，不觸發抓取）：

- 單季值：本季累計減上一季累計；第 1 季等於累計。缺上一季時單季值全為 null，`single_missing_reason=previous_quarter_missing`，前端顯示 `--` 並附說明。單季 EPS 為累計 EPS 相減。
- 三率：毛利率、營業利益率、稅後淨利率為對應金額除以營收，單位百分數（`53.2` 表示 53.2%）；營收缺值或不大於 0 時為 null。單季模式用單季金額，累計模式用官方累計值。稅後淨利率的分子是 `net_income`（本期淨利），不是歸屬母公司淨利。
- ROE：單季、不年化，單季歸屬母公司淨利除以（上一季末與本季末歸屬母公司權益的平均）；缺期初時為 null，`roe_missing_reason=opening_equity_missing`；有期初但缺期末時也為 null，`roe_missing_reason=closing_equity_missing`。累計模式不提供 ROE。
- `year_ago` 為去年同季的累計與單季值，前端依此上色並在滑過時顯示差距（三率為百分點，金額與 EPS 為變動率）。
- `annual_eps`：各年度第 4 季的累計 EPS。
- `accumulating`：已存季數少於 `target_quarters`（8）。
- ETF 回 `applicable=false`、`not_applicable_reason=etf`；TDR 回 `tdr`。證券主檔沒有的代號回 404，存檔讀取失敗回 500。

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
- `historical_reference`: 沒有經驗證的可用時間，僅供顯示或參考，不得進入回測與 as-of 查詢；
  是否已正式匯入看 `production_supported`；
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
