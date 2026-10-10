# Phase 6H Taiwan historical share-capital investigation

Investigation date: 2026-08-31 (Asia/Taipei).

## Decision

The official company-profile datasets safely support a current/reference record
for issued common shares, paid-in capital, and common-share par value. They do
not provide historical snapshots, exact publication timestamps, effective dates,
or revision chains. `total_shares` and `float_shares` therefore remain null.

No official source investigated in this phase supports a complete production
historical share-capital mapping. Historical Taiwan turnover now fails closed
instead of applying today's instrument `float_shares` to past dates.

## Source inventory and classification

| Source | Fields / meaning | Unit | History and timing | Revision semantics | Class |
| --- | --- | --- | --- | --- | --- |
| TWSE `opendata/t187ap03_L` | `已發行普通股數或TDR原股發行股數`, `實收資本額`, `普通股每股面額`, `出表日期` | shares, TWD, TWD/share | current report; date-level only | none exposed | B: current/reference |
| TPEx `mopsfin_t187ap03_O` | `IssueShares`, `Paidin.Capital.NTDollars`, `ParValueOfCommonStock`, `Date` | shares, TWD, TWD/share | current report; date-level only | none exposed | B: current/reference |
| MOPS company increase/decrease table (`IRB160`) | monthly capital-change report | report fields; not a universal share snapshot | historical monthly query, no exact record availability verified | no stable chain to company-profile snapshots | C |
| MOPS issuance/capital-change announcements | event-specific issuance, reduction, cancellation, conversion or exchange details | varies by form | some events have exact material-information timestamps | no universal structured resulting-share/effective-date schema | C for generic ingest |
| TWSE capital-reduction reference report (`TWTAUU`) | resume date, reduction reason and trading reference prices | dates/prices; detail may include exchange ratio | historical event report | no exact publication/revision chain to profile snapshot | C for share history |
| TWSE/TPEx security directory | stable security identity | identity metadata | current | not a time-varying capital source | C for capital |

Official entry points:

- TWSE OpenAPI schema: `https://openapi.twse.com.tw/v1/swagger.json`
- TPEx OpenAPI schema: `https://www.tpex.org.tw/openapi/swagger.json`
- MOPS: `https://mops.twse.com.tw/`
- MOPS capital increase/decrease query: `https://mops.twse.com.tw/mops/web/IRB160`
- MOPS issuance announcement query: `https://mops.twse.com.tw/mops/web/t59sb09`
- TWSE capital-reduction report: `https://www.twse.com.tw/exchangeReport/TWTAUU?response=html`

## Concept mapping

- `issued_shares` maps only from the explicit issued-common-share field.
- `capital_twd` maps only from paid-in capital, a monetary amount.
- `par_value_twd` preserves the explicit common-share par value as metadata.
- `total_shares` stays null because issued common shares are not necessarily all
  share classes. The 2881 profile demonstrates this boundary: paid-in capital
  includes capital beyond its issued-common-share count.
- `float_shares` stays null because none of the profile fields defines public
  float, free float, circulating shares, or listed tradable shares.

No value is derived by dividing capital by par value. Private shares, preferred
shares, listed shares, issued shares, and ETF units are not substituted for float.

## Effective date and availability

The company-profile `Date` / `出表日期` is a report date. It is neither an exact
publication timestamp nor an event effective date. Current/reference records use:

```text
published_at = None
available_at = None
status = data_insufficient
```

Capital events can be announced before their effective date. A future historical
event model must independently retain both values and must not apply a known
future event before its effective date. The current official source set does not
provide a sufficiently uniform identity and time model to implement that seam.

## Live verification

| Symbol | Raw source fields | issued_shares | total_shares | float_shares | capital_twd | par_value_twd | Historical status |
| --- | --- | ---: | --- | --- | ---: | ---: | --- |
| `2330.TWSE` | TWSE company profile | 25,932,370,067 | null | null | 259,323,700,670 | 10 | unavailable |
| `6488.TPEX` | TPEx company profile | 478,113,725 | null | null | 4,781,137,250 | 10 | unavailable |
| `2881.TWSE` | TWSE company profile | 14,007,364,952 | null | null | 156,073,549,520 | 10 | unavailable |
| `6176.TWSE` | TWSE company profile after an official 2026 capital-reduction event | 348,770,447 | null | null | 3,487,704,470 | 10 | unavailable |
| `0050.TWSE` | ETF control | unsupported | unsupported | unsupported | unsupported | unsupported | unsupported |

The 6176 TWSE reduction report proves that a capital-change event occurred and
that shares are time-varying. It does not by itself prove a complete before/after
share snapshot with an exact publication and revision chain, so it is not ingested
as historical capital.

## Consumer boundary

The existing generic financial-share parquet and current instruments originate
outside this Taiwan official foundation. For `.TWSE` and `.TPEX` rows:

- current-day calculations may retain a current instrument denominator;
- historical calculations require a future verified Taiwan-specific PIT seam;
- date-only `period_end` rows and current instrument values cannot enable
  historical turnover;
- missing verified history produces null turnover rather than a plausible but
  look-ahead-biased value.

The existing non-Taiwan fallback behavior remains unchanged.

## Rejected inference

- current issued or float shares backward-filled across history;
- `capital_twd / par_value_twd` as authoritative shares;
- market cap divided by price;
- issued, listed, private, preferred, or outstanding shares as public float;
- stock-dividend arithmetic as a resulting share count;
- same issuer/date/amount or subject similarity as event identity;
- report date, effective date, retrieval time, or HTTP headers as exact availability;
- ETF issued or beneficiary units as company share capital.

## Same-day turnover denominator (2026-09-22)

The findings above stand. What changed is that the current issued-share count is
now supplied for the **current trading day only**, so `turnover_rate` exists for
today instead of not existing at all.

`TaiwanShareCapitalStore` refreshes issued common shares in bulk from the two
endpoints already inventoried above and writes
`data/taiwan/share_capital.parquet`. `TaiwanEnrichedRefreshService` passes it to
`compute_enriched` as the `instruments` frame, whose `float_shares` column is the
turnover denominator.

This does not reopen the rejected inference, but the guarantee holds per path
rather than globally, and saying otherwise would be the more dangerous mistake:

- **Daily pipeline.** `apply_historical_float_shares` withdraws the denominator
  from every Taiwan row whose date is not today, so a past session's turnover
  stays absent rather than being recomputed against a count that did not apply
  then.
- **Backtest matrix.** This path never calls that helper. An instrument
  `float_shares` arrives there as a single value broadcast over the whole time
  axis, which across several bars would price 2024 with a count announced in
  2026. A Taiwan matrix spanning more than one bar is refused: it writes null
  instead. A single-bar matrix keeps the existing snapshot behaviour, since
  there is no second date for the count to spread onto.

  What this does **not** cover: a single-bar matrix built for a past date still
  takes the current count, because the matrix is handed a bare `float_shares`
  column with no as-of date to check it against. That predates this change and
  is recorded here rather than left to be rediscovered; closing it means
  carrying `report_date` into the instruments frame.

Both are pinned by regression tests, because these guarantees are what make
supplying the number acceptable at all. Until the refusal was added, only the
absence of `data/instruments` stood between the matrix and that look-ahead, and
this change is precisely what created the Taiwan share source it was missing.

Two consequences that must not be papered over:

- （2026-10-10 第 4 階段起，分母改為已發行普通股扣除私募股，見文末新增的小節；
  下面這段原文保留作紀錄。）The denominator is **issued** shares, not free float. Every Taiwan turnover
  figure is therefore a share of issued capital and reads lower than a
  float-based one, by more for a company whose insiders hold more of it.
  Taiwan market data conventionally uses issued shares because nothing else is
  published, so figures stay comparable between listings — but none of them is a
  true float turnover, and the field description says so.
- Historical turnover remains unavailable, so a strategy that scores on
  `turnover_rate` still cannot be backtested. It can screen today.
  （第 4 階段起，每天的換手率從該日自己的發佈起保留，換手率歷史會逐日累積；回測改依覆蓋率規則判斷，見文末。）

Before this, four built-in strategies scored on `turnover_rate` and could not run
at all; the field was absent from the Taiwan enriched table rather than null.
（第 4 階段起，內建策略的預設評分只剩 `high_turnover_surge` 用 `turnover_rate`。）

## 第 4 階段：分母扣除私募股，換手率逐日保留（2026-10-10）

上面的發現仍然成立：官方來源沒有歷史股本快照，也沒有自由流通股。第 4 階段改了三件事。

### 分母

- 分母改為已發行普通股減去私募股。私募股數取自同一批官方公司資料：TWSE 的「私募股數」、TPEx 的 `PrivateStock.shares`；沒有這個欄位的列用已發行股數。實作在 `TaiwanShareCapitalStore.as_instruments()`（`backend/app/taiwan/share_capital_store.py`）。
- 這仍然不是自由流通股：內部人與大股東的持股還在分母裡，所以換手率仍比以流通股計算的數字低。
- TDR 排除：官方股數是原公司的股數，不是存託憑證的單位數。ETF 沒有公司股本資料。兩者的換手率都是空值。
- 2026-10-09 稽核抽查 7 家，其中 4 家與第三方網站的分母相同（等於已發行減私募）。不同的例子如 2434：新口徑 31.6%，第三方 17.69%。
- 2026-10-10 在 2026-10-08 的資料上比較：1,946 筆換手率中 266 筆因扣除私募而改變，中位數放大 1.24 倍，最大 14.36 倍（4419.TPEX）。

### 逐日保留

- 每天的換手率只在該日還是最新交易日時，於盤後發佈用當時的股本計算。之後的重新發佈（包括全量重寫）沿用分區裡已發佈的值，不重算、不回推（`backend/app/taiwan/enriched_refresh.py` 的 `_with_published_turnover`）。
- 換手率開始逐日保存之前的日子沒有換手率，也不會補算。本機資料從 2026-10-08 起才有換手率，之後逐日累積。

### 策略與回測

- 內建策略的預設評分只剩 `high_turnover_surge` 用換手率。門檻改為 2%，依 2026-10-08 單日分布約第 90 百分位校準。`bullish_alignment`、`low_volatility_leader`、`pullback_to_support` 改用 `vol_ratio_5d`。
- 覆蓋率規則（選股、監控與回測共用）：合格股票是股本檔中有分母的股票。選股看目標交易日，回測看每個正式交易日；合格股票中有換手率的比例低於 50% 就拒絕執行。回測整體通過、但某一交易所低於 50% 時，附上該交易所的警告。
- 所以 `high_turnover_surge` 在換手率開始保存之前的日期，以及涵蓋這些日期的回測區間，都會被拒絕，要等換手率歷史累積。

### 已知限制

- 一天的換手率固定在它最後一次以「最新交易日」身分發佈時的值。例如錯過 21:40 那一班，D 日的換手率停在 16:30 用暫定成交量算出的值；下一次執行補正 D 日並存入 D+1 日後，D 日已不是最新交易日，沿用舊值，修正後的成交量不會反映到換手率。
- 某檔在該日以最新交易日發佈時沒有資料列，該日換手率永遠為空。整個交易所缺席時，該日的覆蓋率可能低於 50%，涵蓋它的回測會一直被拒絕。
- 重新發佈時某個分區暫時讀取失敗，該日的換手率會永久遺失（會記錄警告）。
- 刪除最新的日 K 分區後，前一天重新成為最新交易日，會用目前的股本重算。
