# Dead code 清單（2026-10-22 全專案審查）

> 基準：HEAD `1724b12`。「沒有引用」是否定結論：每項都經另一位驗證者重新搜尋整個 repo（含動態載入、測試、文件、打包設定），並以已知被使用的符號做正向對照。
> 刪除前仍要再搜一次，並跑受影響的測試；「只有測試引用」的項目要連同測試一起處理。

## 完全沒有引用（285 項）

| ID | 位置 | 符號 | 種類 | 狀態 | 可安全移除 | 驗證 |
|---|---|---|---|---|---|---|
| D001 | `Dockerfile:71` | COPY README.md /README.md | config | unreferenced | likely | 確認 |
| D002 | `backend/app/api/backtest.py:82` | GET /api/backtest/status | endpoint | unreferenced | likely | 確認 |
| D003 | `backend/app/api/backtest.py:156` | DELETE /api/backtest/candidates/{candidate_id} | endpoint | unreferenced | likely | 確認 |
| D004 | `backend/app/api/data.py:75` | invalidate_storage_cache | function | unreferenced | likely | 確認 |
| D005 | `backend/app/api/data.py:571` | GET /api/data/status（含 _get_table_stats 等聚合） | endpoint | unreferenced | likely | 確認 |
| D006 | `backend/app/api/data.py:613` | POST /api/data/refresh-cache | endpoint | unreferenced | likely | 確認 |
| D007 | `backend/app/api/ext_data.py:303` | _refresh_views | function | unreferenced | likely | 確認 |
| D008 | `backend/app/api/intraday.py:27` | _fallback_index_quotes_from_daily | function | unreferenced | yes | 確認 |
| D009 | `backend/app/api/intraday.py:210` | GET /api/intraday/indices 與 _fallback_index_quotes_from_daily | endpoint | unreferenced | likely | 確認 |
| D010 | `backend/app/api/intraday.py:210` | GET /api/intraday/indices | endpoint | unreferenced | yes | 確認 |
| D011 | `backend/app/api/kline.py:43` | compute_enriched_single (import) | other | unreferenced | yes | 確認 |
| D012 | `backend/app/api/kline.py:878` | DailyBatchRequest | class | unreferenced | yes | 確認 |
| D013 | `backend/app/api/mining.py:144` | GET /api/backtest/mining/runs | endpoint | unreferenced | unknown | 確認 |
| D014 | `backend/app/api/mining.py:223` | GET /api/backtest/mining/runs/{run_id} | endpoint | unreferenced | unknown | 確認 |
| D015 | `backend/app/api/mining.py:229` | POST /api/backtest/mining/runs/{run_id}/cancel | endpoint | unreferenced | unknown | 確認 |
| D016 | `backend/app/api/mining.py:364` | GET /api/backtest/mining/config | endpoint | unreferenced | unknown | 確認 |
| D017 | `backend/app/api/pipeline.py:20` | GET /api/pipeline/jobs/{job_id}（含唯一的 reap_stale 呼叫） | endpoint | unreferenced | likely | 確認 |
| D018 | `backend/app/api/pipeline.py:20` | GET /api/pipeline/jobs/{job_id} | endpoint | unreferenced | likely | 確認 |
| D019 | `backend/app/api/pipeline.py:31` | POST /api/pipeline/jobs/{job_id}/cancel | endpoint | unreferenced | likely | 確認 |
| D020 | `backend/app/api/regime.py:19` | import time | other | unreferenced | yes | 確認 |
| D021 | `backend/app/api/regime.py:28` | _CACHE_TTL / _cache / _cache_ts / _cache_lock / invalidate_regime_cache | function | unreferenced | yes | 確認 |
| D022 | `backend/app/api/regime.py:34` | invalidate_regime_cache / _cache / _cache_ts / _CACHE_TTL / _cache_lock | function | unreferenced | yes | 確認 |
| D023 | `backend/app/api/regime.py:34` | invalidate_regime_cache 與 _cache 變數（:28-39） | function | unreferenced | yes | 確認 |
| D024 | `backend/app/api/regime.py:34` | invalidate_regime_cache | function | unreferenced | likely | 確認 |
| D025 | `backend/app/api/regime.py:58` | GET /api/regime/latest 與 GET /api/regime/coverage | endpoint | unreferenced | likely | 確認 |
| D026 | `backend/app/api/regime.py:58` | GET /api/regime/latest | endpoint | unreferenced | likely | 確認 |
| D027 | `backend/app/api/regime.py:69` | GET /api/regime/coverage | endpoint | unreferenced | likely | 確認 |
| D028 | `backend/app/api/routes.py:33` | POST /api/capabilities/redetect | endpoint | unreferenced | likely | 確認 |
| D029 | `backend/app/api/screener.py` | run_custom | endpoint | unreferenced | unknown | 確認 |
| D030 | `backend/app/api/screener.py:249` | POST /api/screener/run | endpoint | unreferenced | yes | 確認 |
| D031 | `backend/app/api/settings.py` | save_tickflow_key / clear_tickflow_key | endpoint | unreferenced | unknown | 確認 |
| D032 | `backend/app/api/settings.py` | update_pipeline_pull_types | endpoint | unreferenced | likely | 確認 |
| D033 | `backend/app/api/settings.py` | update_pipeline_regime_enabled | endpoint | unreferenced | likely | 確認 |
| D034 | `backend/app/api/settings.py` | update_regime_batch_params | endpoint | unreferenced | likely | 確認 |
| D035 | `backend/app/api/settings.py` | update_minute_sync | endpoint | unreferenced | unknown | 確認 |
| D036 | `backend/app/api/settings.py` | update_realtime_watchlist | endpoint | unreferenced | likely | 確認 |
| D037 | `backend/app/api/settings.py` | update_enriched_batch_size / update_index_daily_batch_size | endpoint | unreferenced | likely | 確認 |
| D038 | `backend/app/api/settings.py` | get_screener_result_columns / update_screener_result_columns | endpoint | unreferenced | unknown | 確認 |
| D039 | `backend/app/api/settings.py` | update_webhook_enabled_default | endpoint | unreferenced | likely | 確認 |
| D040 | `backend/app/api/settings.py` | update_realtime_quote_scope | endpoint | unreferenced | unknown | 確認 |
| D041 | `backend/app/api/settings.py` | update_mainline_filter | endpoint | unreferenced | unknown | 確認 |
| D042 | `backend/app/api/settings.py` | update_pipeline_index_symbols | endpoint | unreferenced | unknown | 確認 |
| D043 | `backend/app/api/settings.py:98` | POST/DELETE /api/settings/tickflow-key（:98、:179） | endpoint | unreferenced | likely | 確認 |
| D044 | `backend/app/api/settings.py:98` | POST/DELETE /api/settings/tickflow-key | endpoint | unreferenced | likely | 確認 |
| D045 | `backend/app/api/settings.py:378` | DatasetFieldMapItem | class | unreferenced | yes | 確認 |
| D046 | `backend/app/api/settings.py:763` | GET/PUT /api/settings/preferences/screener-result-columns（:763、:771） | endpoint | unreferenced | likely | 確認 |
| D047 | `backend/app/api/settings.py:780` | PUT minute-sync、realtime-quote-scope、realtime-watchlist、pipeline-pull-types、pipeline-regime-enabled、regime-batch-params、mainline-filter、pipeline-index-symbols、webhook-enabled-default、enriched-batch-size、index-daily-batch-size（:780、869、881、950、963、977、1008、1020、1128、1188、1200） | endpoint | unreferenced | likely | 確認 |
| D048 | `backend/app/api/signals.py:95` | /options 回應中的 kinds | API 回應欄位 | unreferenced | likely | 確認 |
| D049 | `backend/app/api/stock_analysis.py:44` | _build_series（回傳的 series／dates／summary） | other | unreferenced | likely | 確認 |
| D050 | `backend/app/api/stock_analysis.py:133` | /api/stock-analysis/levels 回傳的 dates、series（_build_series） | endpoint | unreferenced | likely | 確認 |
| D051 | `backend/app/api/strategy.py` | _get_monitor | function | unreferenced | likely | 確認 |
| D052 | `backend/app/api/strategy.py` | MonitorStartRequest | class | unreferenced | likely | 確認 |
| D053 | `backend/app/api/strategy.py` | StrategyMonitorService (import) | dependency | unreferenced | likely | 確認 |
| D054 | `backend/app/api/strategy.py:56` | _get_monitor(56) / MonitorStartRequest(315) | function | unreferenced | yes | 確認 |
| D055 | `backend/app/api/strategy.py:346` | GET /api/strategies/{id}、DELETE /config/{id}、POST /build、/ai/generate、/code/validate、/code/save、/composite/save、/ai/save（:346、548、942、1002、1014、1023、1151、1159） | endpoint | unreferenced | unknown | 確認 |
| D056 | `backend/app/api/taiwan.py` | stream_taiwan_bootstrap_job | endpoint | unreferenced | unknown | 確認 |
| D057 | `backend/app/api/taiwan.py:111` | GET /api/taiwan/bootstrap/stream/{job_id} | endpoint | unreferenced | likely | 確認 |
| D058 | `backend/app/backtest/factor.py:24` | FACTOR_COLUMNS 各項的 'group' 與 'desc' 鍵 | constant | unreferenced | likely | 確認 |
| D059 | `backend/app/backtest/matrix.py:3074` | shift / _valid_shift_kernel 的負 periods 分支 | branch | unreferenced | likely | 確認 |
| D060 | `backend/app/backtest/matrix.py:3646` | valid_ewm_adjust_false / _valid_ewm_kernel | function | unreferenced | unknown | 確認 |
| D061 | `backend/app/backtest/matrix.py:3684` | safe_divide | function | unreferenced | likely | 確認 |
| D062 | `backend/app/backtest/mining.py:32` | MiningProfile | type | unreferenced | yes | 確認 |
| D063 | `backend/app/backtest/mining.py:408` | FoldMiningResult.inner_score | other | unreferenced | likely | 確認 |
| D064 | `backend/app/backtest/mining_runtime.py:171` | MatcherCandidateEvaluator.evaluate_candidate | method | unreferenced | unknown | 確認 |
| D065 | `backend/app/backtest/regime_alignment.py:20` | three_level_regime | function | unreferenced | likely | 確認 |
| D066 | `backend/app/backtest/regime_alignment.py:20` | three_level_regime / REGIME_THREE_LEVEL_MAP | function | unreferenced | yes | 確認 |
| D067 | `backend/app/backtest/strategy.py:116` | FeaturePlan（required_features 欄位） | type | unreferenced | likely | 確認 |
| D068 | `backend/app/backtest/strategy.py:125` | ResolvedFeaturePlan.intermediate_columns | other | unreferenced | likely | 確認 |
| D069 | `backend/app/backtest/strategy.py:1151` | _resolve_composite_feature_plan 中的 composite_mod 匯入與「_ = composite_mod」 | other | unreferenced | yes | 確認 |
| D070 | `backend/app/backtest/strategy.py:2359` | _build_entry_mask | method | unreferenced | likely | 確認 |
| D071 | `backend/app/backtest/strategy.py:2359` | StrategyBacktestService._build_entry_mask | method | unreferenced | yes | 確認 |
| D072 | `backend/app/capabilities.py:50` | CapabilitySet.require, CapabilitySet.grant | method | unreferenced | yes | 確認 |
| D073 | `backend/app/capabilities.py:59` | CapabilitySet.grant | method | unreferenced | likely | 確認 |
| D074 | `backend/app/config.py:182` | Settings.use_free_mode | function | unreferenced | likely | 確認 |
| D075 | `backend/app/data_providers/custom/config.py:50` | CustomSourceConfig.has_dataset | method | unreferenced | likely | 確認 |
| D076 | `backend/app/data_providers/custom/config.py:50` | CustomSourceConfig.has_dataset, DatasetName | method | unreferenced | yes | 確認 |
| D077 | `backend/app/data_providers/custom/loader.py:301` | is_custom_provider | function | unreferenced | yes | 確認 |
| D078 | `backend/app/data_providers/normalizer.py:85` | normalize_instruments | function | unreferenced | likely | 確認 |
| D079 | `backend/app/data_providers/normalizer.py:85` | normalize_instruments, INSTRUMENT_COLS | function | unreferenced | yes | 確認 |
| D080 | `backend/app/data_providers/schemas.py` | DAILY_COLUMNS, ADJ_FACTOR_COLUMNS, INSTRUMENT_COLUMNS, MINUTE_COLUMNS（整個模組） | module | unreferenced | yes | 確認 |
| D081 | `backend/app/data_providers/schemas.py:1` | app.data_providers.schemas（DAILY_COLUMNS、ADJ_FACTOR_COLUMNS、INSTRUMENT_COLUMNS、MINUTE_COLUMNS） | module | unreferenced | yes | 確認 |
| D082 | `backend/app/desktop.py:234` | _signal_done（巢狀函式）與 window 區域變數(274) | function | unreferenced | yes | 確認 |
| D083 | `backend/app/desktop.py:234` | _signal_done, server.config.callback_notify | function | unreferenced | yes | 確認 |
| D084 | `backend/app/enriched_generation.py:181` | get_enriched_generation 的 initialize=False 分支 | branch | unreferenced | likely | 確認 |
| D085 | `backend/app/enriched_generation.py:252` | EnrichedPublication.mark_changed | method | unreferenced | likely | 確認 |
| D086 | `backend/app/indicators/levels.py:27` | PriceLevel | class | unreferenced | yes | 確認 |
| D087 | `backend/app/indicators/pipeline.py:231` | ENRICHED_COLUMNS_BY_CATEGORY 的 'storage'、'signals'、'join' 鍵 | constant | unreferenced | likely | 確認 |
| D088 | `backend/app/indicators/pipeline.py:350` | _math_half_up | function | unreferenced | likely | 確認 |
| D089 | `backend/app/indicators/pipeline.py:467` | _INDICATOR_DEPS['vol_ratio_5d'] → '_vol_ma5'（以及 :592-593 的 _vol_ma5 計算） | other | unreferenced | yes | 確認 |
| D090 | `backend/app/indicators/pipeline.py:1567` | compute_enriched_single | function | unreferenced | likely | 確認 |
| D091 | `backend/app/main.py:101` | app.state.datastore, app.state.extension_load_errors（442） | other | unreferenced | likely | 確認 |
| D092 | `backend/app/rate_limits.py:122` | min_batch | function | unreferenced | likely | 確認 |
| D093 | `backend/app/repository.py:470` | _etf_live_agg_cache, _etf_live_agg_cache_date | other | unreferenced | yes | 確認 |
| D094 | `backend/app/repository.py:2135` | _merge_cached_and_scan(2135) / symbols_lagging(2253) / append_daily_asset(2333) / append_enriched_asset(2342) / save_index_instruments(2351) / save_etf_instruments(2363) / refresh_index_views(2376) / flush_live_enriched(2624) | method | unreferenced | likely | 確認 |
| D095 | `backend/app/repository.py:2135` | _merge_cached_and_scan | method | unreferenced | yes | 確認 |
| D096 | `backend/app/repository.py:2253` | symbols_lagging | method | unreferenced | yes | 確認 |
| D097 | `backend/app/repository.py:2303` | append_index_daily, append_index_enriched, append_etf_daily, append_etf_enriched | method | unreferenced | yes | 確認 |
| D098 | `backend/app/repository.py:2333` | append_daily_asset, append_enriched_asset | method | unreferenced | yes | 確認 |
| D099 | `backend/app/repository.py:2351` | save_index_instruments, save_etf_instruments | method | unreferenced | yes | 確認 |
| D100 | `backend/app/repository.py:2376` | refresh_index_views | method | unreferenced | yes | 確認 |
| D101 | `backend/app/repository.py:2624` | flush_live_enriched | method | unreferenced | yes | 確認 |
| D102 | `backend/app/secrets_store.py:102` | get_env_backed_secret | function | unreferenced | no | 確認 |
| D103 | `backend/app/services/ai_provider.py:78` | _FOCUS_BLOCKLIST | constant | unreferenced | likely | 確認 |
| D104 | `backend/app/services/ai_provider.py:89` | sanitize_focus | function | unreferenced | likely | 確認 |
| D105 | `backend/app/services/ext_data.py:333` | infer_fields_from_df / detect_symbol_candidates / _CODE_PAT / _SYMBOL_PAT / _POLARS_TYPE_MAP | function | unreferenced | yes | 確認 |
| D106 | `backend/app/services/ext_data.py:511` | infer_fields_from_df(333) / detect_symbol_candidates(343) / parse_upload_file(511) / delete_ext_parquet(621) / fix_symbol_format(639) | function | unreferenced | likely | 確認 |
| D107 | `backend/app/services/ext_data.py:511` | parse_upload_file / ensure_utf8_csv | function | unreferenced | yes | 確認 |
| D108 | `backend/app/services/ext_data.py:621` | delete_ext_parquet / fix_symbol_format | function | unreferenced | yes | 確認 |
| D109 | `backend/app/services/extend_history.py:102` | run_extend_history（整個模組） | module | unreferenced | likely | 確認 |
| D110 | `backend/app/services/extend_history.py:102` | extend_history（整個模組，含 run_extend_history、_resolve_universe、compute_offset、_refresh_single_view） | module | unreferenced | likely | 確認 |
| D111 | `backend/app/services/kline_sync.py:179` | sync_and_persist_daily_batch | function | unreferenced | likely | 確認 |
| D112 | `backend/app/services/kline_sync.py:321` | sync_adj_factor | function | unreferenced | likely | 確認 |
| D113 | `backend/app/services/kline_sync.py:1047` | sync_and_persist_minute 的 extend_backward 分支（:1047-1059）＋ _earliest_minute_datetime | branch | unreferenced | likely | 確認 |
| D114 | `backend/app/services/market_phase.py:36` | PHASE_LABELS / _PHASE_PRIORITY | constant | unreferenced | yes | 確認 |
| D115 | `backend/app/services/pipeline_jobs.py:40` | STALE_JOB_TIMEOUT_S / JobStatus | constant | unreferenced | yes | 確認 |
| D116 | `backend/app/services/preferences.py:409` | set_sentiment_exclude_st | function | unreferenced | yes | 確認 |
| D117 | `backend/app/services/preferences.py:424` | 已死 settings 端點專用的 setter，以及 get_financial_sync_times/set_financial_sync_time（:888） | function | unreferenced | likely | 確認 |
| D118 | `backend/app/services/preferences.py:482` | set_sentiment_exclude_st(409) / set_pipeline_schedule(482) / set_instruments_schedule(498) / set_sse_refresh_pages(649) / set_financial_sync_time(893) | function | unreferenced | likely | 確認 |
| D119 | `backend/app/services/preferences.py:649` | set_sse_refresh_pages | function | unreferenced | yes | 確認 |
| D120 | `backend/app/services/preferences.py:888` | get_financial_sync_times / set_financial_sync_time | function | unreferenced | yes | 確認 |
| D121 | `backend/app/services/quote_service.py:251` | QuoteService._fetch_ms | other | unreferenced | yes | 確認 |
| D122 | `backend/app/services/quote_service.py:349` | QuoteService.pause/resume/paused | method | unreferenced | likely | 確認 |
| D123 | `backend/app/services/quote_service.py:368` | QuoteService.paused / pause / resume | method | unreferenced | likely | 確認 |
| D124 | `backend/app/services/quote_service.py:448` | notify_depth_updated(448) / clear_pending_alerts(463) / push_review_event(467) / get_quotes_compat(518) / _get_strategy_monitor(1828) / self._strategy_monitor(246) | method | unreferenced | likely | 確認 |
| D125 | `backend/app/services/quote_service.py:448` | QuoteService.notify_depth_updated / QuoteSubscriber.notify_depth | method | unreferenced | likely | 確認 |
| D126 | `backend/app/services/quote_service.py:448` | QuoteService.notify_depth_updated | method | unreferenced | likely | 確認 |
| D127 | `backend/app/services/quote_service.py:460` | QuoteService.push_alerts | method | unreferenced | likely | 確認 |
| D128 | `backend/app/services/quote_service.py:463` | QuoteService.clear_pending_alerts / QuoteSubscriber.clear_alerts | method | unreferenced | likely | 確認 |
| D129 | `backend/app/services/quote_service.py:467` | QuoteService.push_review_event / QuoteSubscriber.push_review | method | unreferenced | likely | 確認 |
| D130 | `backend/app/services/quote_service.py:518` | QuoteService.get_quotes_compat | method | unreferenced | likely | 確認 |
| D131 | `backend/app/services/quote_service.py:1828` | QuoteService._get_strategy_monitor / _strategy_monitor | method | unreferenced | yes | 確認 |
| D132 | `backend/app/services/regime_builder.py:620` | latest_phase_transition(620) / earliest_enriched_date(683) | function | unreferenced | likely | 確認 |
| D133 | `backend/app/services/regime_builder.py:620` | latest_phase_transition / earliest_enriched_date / STATE_LABELS | function | unreferenced | yes | 確認 |
| D134 | `backend/app/services/regime_builder.py:620` | latest_phase_transition | function | unreferenced | likely | 確認 |
| D135 | `backend/app/services/screener.py:311` | ScreenerService.load_prior_consecutive | method | unreferenced | likely | 確認 |
| D136 | `backend/app/services/strategy_pick_history.py:104` | RecordOutcome.run_id | dataclass 欄位 | unreferenced | likely | 確認 |
| D137 | `backend/app/services/watchlist.py:387` | fetch_quotes | function | unreferenced | likely | 確認 |
| D138 | `backend/app/strategy/builtin/bottom_reversal.py:80` | META['scoring_directions'] 中不在 scoring 的欄位（bottom_reversal 的 momentum_5d；n_day_low_reversal.py:44 的 rsi_14；oversold_reversal.py:52 的 momentum_5d） | config | unreferenced | likely | 確認 |
| D139 | `backend/app/strategy/monitor.py` | StrategyMonitorService | class | unreferenced（main.py 有建立實例，但沒有任何地方使用） | likely | 確認 |
| D140 | `backend/app/strategy/monitor.py` | StrategyAlert | class | unreferenced | likely | 確認 |
| D141 | `backend/app/strategy/monitor.py:465` | MonitorRuleEngine.add_rule | method | unreferenced | yes | 確認 |
| D142 | `backend/app/strategy/monitor.py:1270` | _default_message 參數 sym | other | unreferenced | likely | 確認 |
| D143 | `backend/app/taiwan/__init__.py:11` | 套件層級再匯出（EtfCategory…TaiwanOfficialQuoteProvider、MarketStatus…taipei_today、market_rules 與 universe 的名稱） | other | unreferenced | likely | 確認 |
| D144 | `backend/app/taiwan/abnormal_diagnostics.py:288` | df_p20_daily（區域變數） | other | unreferenced | yes | 確認 |
| D145 | `backend/app/taiwan/abnormal_diagnostics.py:288` | df_p20_daily（連同 :286 p20_start，以及 _resolve_prior_sessions 回傳的 prior_20_dates） | other | unreferenced | yes | 確認 |
| D146 | `backend/app/taiwan/abnormal_diagnostics.py:332` | mean_abs_dealer_20d（group_by 聚合欄） | other | unreferenced | yes | 確認 |
| D147 | `backend/app/taiwan/bootstrap.py:406` | _run_job / start_bootstrap 的 force 參數 | other | unreferenced | likely | 確認 |
| D148 | `backend/app/taiwan/bootstrap.py:688` | get_bootstrap_service | function | unreferenced | unknown | 確認 |
| D149 | `backend/app/taiwan/bootstrap.py:688` | get_bootstrap_service / _bootstrap_service_instance | function | unreferenced | likely | 確認 |
| D150 | `backend/app/taiwan/comparison_ai_research.py:42` | 未使用的 import：app.taiwan.ai_research.ObservationItem | dependency | unreferenced | yes | 確認 |
| D151 | `backend/app/taiwan/daily_refresh.py:14` | from pathlib import Path | other | unreferenced | yes | 確認 |
| D152 | `backend/app/taiwan/daily_refresh.py:407` | refresh_today(407) / fetch_sample(415) | method | unreferenced | likely | 確認 |
| D153 | `backend/app/taiwan/daily_refresh.py:407` | TaiwanDailyRefreshService.refresh_today | method | unreferenced | likely | 確認 |
| D154 | `backend/app/taiwan/daily_refresh.py:411` | TaiwanDailyRefreshService.refresh_range | method | unreferenced | likely | 確認 |
| D155 | `backend/app/taiwan/daily_refresh.py:415` | TaiwanDailyRefreshService.fetch_sample | method | unreferenced | likely | 確認 |
| D156 | `backend/app/taiwan/daily_store.py:33` | normalize_taiwan_daily（import） | other | unreferenced | yes | 確認 |
| D157 | `backend/app/taiwan/daily_store.py:43` | DEFAULT_TAIWAN_METADATA | constant | unreferenced | likely | 確認 |
| D158 | `backend/app/taiwan/daily_store.py:254` | TaiwanDailyStore.read_latest | method | unreferenced | likely | 確認 |
| D159 | `backend/app/taiwan/daily_update.py:39` | taipei_today（import） | other | unreferenced | yes | 確認 |
| D160 | `backend/app/taiwan/daily_update.py:39` | taipei_today (import) | other | unreferenced | yes | 確認 |
| D161 | `backend/app/taiwan/detail_models.py:19` | 未使用的 import：typing.Any | dependency | unreferenced | yes | 確認 |
| D162 | `backend/app/taiwan/detail_models.py:110` | TaiwanInstitutionalData.trend、TaiwanMarginData.trend | other | unreferenced | likely | 確認 |
| D163 | `backend/app/taiwan/detail_service.py:17` | 未使用的 import：asyncio、typing.Optional | dependency | unreferenced | yes | 確認 |
| D164 | `backend/app/taiwan/detail_service.py:769` | _aggregate_recent_alerts 中 a.get("rule_symbol") 的比對條件 | branch | unreferenced | yes | 確認 |
| D165 | `backend/app/taiwan/enriched_refresh.py:677` | refresh_taiwan_enriched | function | unreferenced | likely | 確認 |
| D166 | `backend/app/taiwan/enrichment/__init__.py` | 套件層級重新匯出（compute_*、Tpex*/Twse* adapters、MarketQuote 等） | other | unreferenced | likely | 確認 |
| D167 | `backend/app/taiwan/enrichment/index.py` | 未使用匯入 json、re（:15、:17）；quote.py 未使用匯入 json、urllib.request（:16、:18） | other | unreferenced | yes | 確認 |
| D168 | `backend/app/taiwan/enrichment/models.py` | ClassificationSource | class | unreferenced | yes | 確認 |
| D169 | `backend/app/taiwan/enrichment/models.py` | DatasetType.DAILY（及 StalePolicy.THRESHOLDS 的 DAILY／QUOTE 項） | constant | unreferenced | likely | 確認 |
| D170 | `backend/app/taiwan/fundamentals.py` | TaiwanOfficialFundamentals.dividends | method | unreferenced | likely | 確認 |
| D171 | `backend/app/taiwan/industry_intelligence.py:23` | 未使用的 import：typing.Any | dependency | unreferenced | yes | 確認 |
| D172 | `backend/app/taiwan/institutional_margin_refresh.py` | 未使用匯入 TwseInstitutionalAdapter、TpexInstitutionalAdapter、TwseMarginAdapter、TpexMarginAdapter（:27-28、:32-33） | other | unreferenced | yes | 確認 |
| D173 | `backend/app/taiwan/institutional_store.py` | TaiwanInstitutionalStore.read_latest_date_rows | method | unreferenced | likely | 確認 |
| D174 | `backend/app/taiwan/institutional_store.py` | TAIWAN_INSTITUTIONAL_COLUMNS、DEFAULT_INSTITUTIONAL_METADATA、未使用的 typing.Any（:21）與 except 中未使用的 exc（:187） | constant | unreferenced | yes | 確認 |
| D175 | `backend/app/taiwan/margin_store.py` | TaiwanMarginStore.read_latest_date_rows | method | unreferenced | likely | 確認 |
| D176 | `backend/app/taiwan/margin_store.py` | TaiwanMarginStore.known_symbols | method | unreferenced | likely | 確認 |
| D177 | `backend/app/taiwan/margin_store.py` | TAIWAN_MARGIN_COLUMNS、DEFAULT_MARGIN_METADATA、未使用的 typing.Any（:21）與 exc（:187） | constant | unreferenced | yes | 確認 |
| D178 | `backend/app/taiwan/market_rules.py:111` | SecuritiesTaxModel.foreign_etf_rate | other | unreferenced | likely | 確認 |
| D179 | `backend/app/taiwan/market_rules.py:315` | TickSizeModel.round_to_valid_tick | method | unreferenced | likely | 確認 |
| D180 | `backend/app/taiwan/providers/__init__.py` | AmountUnit/FinMindAdapter/PriceSemantics/SourceMetadata/TaiwanHybridProvider/VolumeUnit/YahooFinanceAdapter/normalize_taiwan_daily 重新匯出 | other | unreferenced | likely | 確認 |
| D181 | `backend/app/taiwan/providers/base.py` | TaiwanSourceAdapter | type | unreferenced | yes | 確認 |
| D182 | `backend/app/taiwan/providers/base.py` | PriceSemantics.ADJUSTED | constant | unreferenced | likely | 確認 |
| D183 | `backend/app/taiwan/providers/base.py` | SourceMetadata.rate_limit_rpm / supports_history / supports_etf / supports_tpex | config | unreferenced | unknown | 確認 |
| D184 | `backend/app/taiwan/providers/base.py:62` | TaiwanSourceAdapter (Protocol) | class | unreferenced | likely | 確認 |
| D185 | `backend/app/taiwan/providers/snapshot_provider.py` | 未使用匯入 datetime、TAIPEI、parse_number、Exchange、parse_symbol（:20、:28、:29） | other | unreferenced | yes | 確認 |
| D186 | `backend/app/taiwan/realtime/calendar.py:81` | TaiwanTradingCalendar.add_holiday | method | unreferenced | unknown | 確認 |
| D187 | `backend/app/taiwan/realtime/models.py:19` | RealtimeStatus.DELAYED | constant | unreferenced | likely | 確認 |
| D188 | `backend/app/taiwan/realtime/models.py:37` | MarketStatus.REGULAR_OPEN / MarketStatus.POST_CLOSE_SESSION | constant | unreferenced | likely | 確認 |
| D189 | `backend/app/taiwan/realtime/service.py:9` | timedelta（import） | other | unreferenced | yes | 確認 |
| D190 | `backend/app/taiwan/realtime/service.py:65` | TaiwanRealtimeService.clear_cache | method | unreferenced | likely | 確認 |
| D191 | `backend/app/taiwan/realtime/yahoo_provider.py:15` | MarketStatus（import） | other | unreferenced | yes | 確認 |
| D192 | `backend/app/taiwan/research_context.py:46` | 未使用的 import：typing.Any、TaiwanETFProfile、TaiwanETFSnapshot、FundamentalRecord、TaiwanFundamentalStore、latest_as_of、IndustryMetrics、MarketBreadthStats、PriceLimitModel、TaiwanInstrument | dependency | unreferenced | yes | 確認 |
| D193 | `backend/app/taiwan/screener.py:22` | 未使用的 import：datetime.date、datetime.datetime、typing.Any、app.taiwan.symbol.parse_symbol | dependency | unreferenced | yes | 確認 |
| D194 | `backend/app/taiwan/screener_nl.py:22` | 未使用的 import：json、re | dependency | unreferenced | yes | 確認 |
| D195 | `backend/app/taiwan/watchlist_enrichment.py:42` | _TAIWAN_ROW_FIELDS | constant | unreferenced | yes | 確認 |
| D196 | `backend/app/tickflow/client.py:8` | FREE_ENDPOINT, _should_use_free_server, get_async_client | function | unreferenced | yes | 確認 |
| D197 | `backend/app/tickflow/client.py:12` | _should_use_free_server(12) / get_async_client(24) / FREE_ENDPOINT(8) | function | unreferenced | likely | 確認 |
| D198 | `backend/app/tickflow/pools.py:33` | get_pool / DEMO_SYMBOLS | function | unreferenced | likely | 確認 |
| D199 | `backend/app/tickflow/scheduler.py` | Scheduler, _Bucket（整個模組） | module | unreferenced | yes | 確認 |
| D200 | `backend/app/tickflow/scheduler.py:39` | app.tickflow.scheduler（整個模組，含 Scheduler、_Bucket） | module | unreferenced | yes | 確認 |
| D201 | `backend/pyproject.toml:32` | platformdirs | dependency | unreferenced | likely | 確認 |
| D202 | `backend/pyproject.toml:34` | plyer | dependency | unreferenced | likely | 確認 |
| D203 | `backend/pyproject.toml:61` | mypy（dev extra） | dependency | unreferenced | likely | 確認 |
| D204 | `backend/scripts/cleanup_halt_days.py:2` | cleanup_halt_days | module | unreferenced | likely | 確認 |
| D205 | `backend/tests/test_stage3_minute_gaps.py:27` | OLD_COLUMNS | constant | unreferenced | yes | 確認 |
| D206 | `backend/tests/test_stage4_detail_turnover.py:27` | UNPUBLISHED | constant | unreferenced | yes | 確認 |
| D207 | `backend/tests/test_stage4_indicator_conventions.py:29` | NEW_BARS | constant | unreferenced | yes | 確認 |
| D208 | `backend/tests/test_taiwan_bootstrap.py:44` | _create_dummy_security_master | test_helper | unreferenced | yes | 確認 |
| D209 | `backend/tests/test_taiwan_industry_intelligence.py:44` | mock_calendar | test_helper | unreferenced | yes | 確認 |
| D210 | `frontend/package.json:19` | class-variance-authority | dependency | unreferenced | yes | 確認 |
| D211 | `frontend/package.json:22` | echarts-for-react | dependency | unreferenced | yes | 確認 |
| D212 | `frontend/package.json:24` | lightweight-charts | dependency | unreferenced | likely | 確認 |
| D213 | `frontend/package.json:24` | lightweight-charts 依賴（含 vite manualChunks 分包規則） | dependency | unreferenced | likely | 確認 |
| D214 | `frontend/src/components/AlertToast.tsx:50` | pushAlertToast | function | unreferenced | yes | 確認 |
| D215 | `frontend/src/components/EChartsCandlestick.tsx:379` | INDICATORS | constant | unreferenced | yes | 確認 |
| D216 | `frontend/src/components/EChartsCandlestick.tsx:395` | Props.onToggleMarkers / Props.symbol | other | unreferenced | yes | 確認 |
| D217 | `frontend/src/components/Layout.test.tsx:32` | useCapabilities mock | test_helper | unreferenced | yes | 確認 |
| D218 | `frontend/src/components/ListColumnCustomizer.tsx:37` | extFieldFilter 與 showExtColumns=false 路徑 | other | unreferenced | likely | 確認 |
| D219 | `frontend/src/components/StockDailyKChart.tsx:43` | Props：showIndicatorControls、showMA、showInfoBar、markers、ranges | other | unreferenced | likely | 確認 |
| D220 | `frontend/src/components/StockPanel.tsx:21` | Props：className、onSelectDate、markers、ranges、showLimitMarkers、showMarkerToggle、onMonitor、inWatchlist、onAddToWatchlist、onRemoveFromWatchlist、watchlistPending、refetchIntervalMs | other | unreferenced | likely | 確認 |
| D221 | `frontend/src/components/WatchlistAddMenu.tsx:34` | WatchlistGroupMenu 的 includeAll/counts/total/disableEmpty 路徑 | branch | unreferenced | likely | 確認 |
| D222 | `frontend/src/components/WatchlistAddMenu.tsx:76` | align 屬性與 align === 'left' 分支（24、44、76） | 元件屬性分支 | unreferenced | likely | 確認 |
| D223 | `frontend/src/components/monitor/TaiwanAlertsList.tsx:18` | TaiwanAlertsListProps.onSelectSymbol | other | unreferenced | no | 確認 |
| D224 | `frontend/src/components/monitor/TaiwanQuotePanel.tsx:19` | TaiwanQuotePanelProps.onSelectSymbol | other | unreferenced | likely | 確認 |
| D225 | `frontend/src/components/monitor/TaiwanQuotePanel.tsx:19` | onSelectSymbol prop | other | unreferenced | likely | 確認 |
| D226 | `frontend/src/components/signals/CustomSignalDialog.tsx:13` | defaultKind 與 onSaved 屬性（13、15、23、94） | 元件屬性 | unreferenced | likely | 確認 |
| D227 | `frontend/src/components/stock-table/StockDataTable.tsx:15` | export type { SortState }（re-export） | type | unreferenced | yes | 確認 |
| D228 | `frontend/src/components/taiwan/TaiwanDataQuality.tsx:41` | SOURCE_DISPLAY_NAMES['twse:stock_day_all'] | constant | unreferenced | yes | 確認 |
| D229 | `frontend/src/lib/api.ts:231` | ScreenerStrategy、OverviewMarket(:474)、MiningEvent(:1940)、EnrichedField(:3513) | type | unreferenced | yes | 確認 |
| D230 | `frontend/src/lib/api.ts:474` | OverviewMarket/OverviewDimensionRankItem/IndexQuote | type | unreferenced | yes | 確認 |
| D231 | `frontend/src/lib/api.ts:515` | REGIME_STATE_LABELS、REGIME_STATE_COLORS(:523) | constant | unreferenced | yes | 確認 |
| D232 | `frontend/src/lib/api.ts:2327` | api.health/redetectCapabilities/updatePipelineIndexSymbols/updateRealtimeQuoteScope/updateWebhookDefault/watchlistSetGroup/refreshCache/backtestStatus | function | unreferenced | likely | 確認 |
| D233 | `frontend/src/lib/api.ts:2327` | health, saveTickflowKey, clearTickflowKey, updatePipelineIndexSymbols, updateRealtimeQuoteScope, updateWebhookDefault, updateScreenerResultColumns, redetectCapabilities, watchlistSetGroup, screenerRunPreset, screenerRunCustom, screenerRunAll, mainlineFilterUpdate, refreshCache | function | unreferenced | likely | 確認 |
| D234 | `frontend/src/lib/api.ts:2351` | api.saveTickflowKey/clearTickflowKey 與 SaveTickflowKeyResult（2156） | function | unreferenced | likely | 確認 |
| D235 | `frontend/src/lib/api.ts:2351` | api.saveTickflowKey / api.clearTickflowKey | function | unreferenced | likely | 確認 |
| D236 | `frontend/src/lib/api.ts:2570` | api.screenerResultColumns/updateScreenerResultColumns | function | unreferenced | likely | 確認 |
| D237 | `frontend/src/lib/api.ts:2754` | api.screenerRunPreset/screenerRunCustom/screenerRunAll/screenerCachedSummary/screenerCachedResult 與 ScreenerStrategy/ScreenerResultSummary/ScreenerCachedSummary/ScreenerCachedResult 型別 | function | unreferenced | likely | 確認 |
| D238 | `frontend/src/lib/api.ts:2806` | api.regimeCoverage/mainlineFilterUpdate 與 RegimeState/REGIME_STATE_LABELS/REGIME_STATE_COLORS/RegimeRow/RegimeCoverage/MarketPhase/MainlineFilter（513-586） | function | unreferenced | likely | 確認 |
| D239 | `frontend/src/lib/api.ts:2816` | api.miningRuns/miningAvailability/miningRun/miningStart/miningResult/miningCancel/miningPromote/miningPublish/miningConfig/updateMiningConfig 與 Mining* 型別（1744-1952） | function | unreferenced | likely | 確認 |
| D240 | `frontend/src/lib/api.ts:2874` | api.researchCandidates/researchCandidateCreate/researchCandidateUpdate/researchCandidateDelete 與 ResearchCandidate* 型別（1954-1978） | function | unreferenced | likely | 確認 |
| D241 | `frontend/src/lib/api.ts:2903` | api.pipelineJob 與 QK.pipelineJob（queryKeys.ts:72） | function | unreferenced | yes | 確認 |
| D242 | `frontend/src/lib/api.ts:2910` | api.refreshCache | function | unreferenced | likely | 確認 |
| D243 | `frontend/src/lib/api.ts:2967` | api.strategyList/strategyGet/strategyRun/strategyRunAll/strategySaveConfig/strategyPatchConfig/strategyResetConfig/strategyDelete/strategyReload | function | unreferenced | likely | 確認 |
| D244 | `frontend/src/lib/api.ts:2967` | strategyList, strategyGet, strategyRun, strategyRunAll, strategySaveConfig, strategyPatchConfig, strategyResetConfig, strategyDelete, strategyReload, strategyGetSource, strategyBuild, strategyBuildStream, strategyValidateCode, strategySaveCodeV2, strategySaveComposite, strategySaveCode | function | unreferenced | likely | 確認 |
| D245 | `frontend/src/lib/api.ts:3072` | api.taiwanCapabilities、TaiwanDatasetCapability（1084）、QK.taiwanCapabilities（queryKeys.ts:101） | function | unreferenced | likely | 確認 |
| D246 | `frontend/src/lib/api.ts:3196` | api.strategyGetSource/strategyBuild/strategyBuildStream/strategyValidateCode/strategySaveCodeV2/strategySaveComposite/strategySaveCode 與 StrategyBuildResult/StrategyBuildStreamEvent/StrategyCodeSaveResult（641-660） | function | unreferenced | likely | 確認 |
| D247 | `frontend/src/lib/api.ts:3513` | EnrichedField | type | unreferenced | yes | 確認 |
| D248 | `frontend/src/lib/benchmarks.ts:7` | BENCHMARK_NAMES/benchmarkName | function | unreferenced | yes | 確認 |
| D249 | `frontend/src/lib/benchmarks.ts:18` | benchmarkName、BENCHMARK_NAMES(:7) | function | unreferenced | yes | 確認 |
| D250 | `frontend/src/lib/colors.ts:11` | color（整個模組） | module | unreferenced | yes | 確認 |
| D251 | `frontend/src/lib/format.ts:37` | formatNumber、formatDuration(:43)、formatScheduleDatePart(:53)、formatScheduleTimePart(:58)、isToday(:63)、formatLogTime(:71) | function | unreferenced | yes | 確認 |
| D252 | `frontend/src/lib/format.ts:37` | formatNumber/formatDuration/formatScheduleDatePart/formatScheduleTimePart/isToday/formatLogTime | function | unreferenced | yes | 確認 |
| D253 | `frontend/src/lib/list-columns.ts:11` | ColumnSource 的 computed 變體（含 ListColumnCustomizer.tsx:170 的判斷） | 型別變體與分支 | unreferenced | likely | 確認 |
| D254 | `frontend/src/lib/list-columns.ts:223` | createExtColumn（以及 watchlist-columns.ts:164 的同名包裝） | function | unreferenced | yes | 確認 |
| D255 | `frontend/src/lib/miningTask.ts:236` | miningTask.ts（startMining/cancelMining/attachMiningRun/tryReconnectMining/clearMiningTask/useMiningTask） | module | unreferenced | likely | 確認 |
| D256 | `frontend/src/lib/miningTask.ts:236` | miningTask module (startMining, useMiningTask) | module | unreferenced | likely | 確認 |
| D257 | `frontend/src/lib/miningTask.ts:236` | miningTask.ts | module | unreferenced | likely | 確認 |
| D258 | `frontend/src/lib/monitorBadge.ts:106` | resetBadge | function | unreferenced | yes | 確認 |
| D259 | `frontend/src/lib/queryKeys.ts:23` | QK.overviewMarket/indexQuotes | constant | unreferenced | yes | 確認 |
| D260 | `frontend/src/lib/queryKeys.ts:23` | QK.overviewMarket | constant | unreferenced | yes | 確認 |
| D261 | `frontend/src/lib/queryKeys.ts:42` | QK.screener/screenerCachedSummary/screenerCachedResult/screenerKlineBatch | constant | unreferenced | yes | 確認 |
| D262 | `frontend/src/lib/queryKeys.ts:44` | QK.screenerCachedSummary / QK.screenerCachedResult（及 api.screenerCachedSummary/screenerCachedResult） | constant | unreferenced | likely | 確認 |
| D263 | `frontend/src/lib/queryKeys.ts:56` | QK.backtestStatus/miningRuns/miningAvailability/miningRun/miningResult/miningConfig/researchCandidates | constant | unreferenced | yes | 確認 |
| D264 | `frontend/src/lib/queryKeys.ts:64` | QK.strategyLinkOptions/strategyDetail | constant | unreferenced | yes | 確認 |
| D265 | `frontend/src/lib/queryKeys.ts:107` | QK.regimeLatest/regimeCoverage | constant | unreferenced | yes | 確認 |
| D266 | `frontend/src/lib/signals.ts:336` | ABNORMAL_SIGNAL_LABELS | constant | unreferenced | likely | 確認 |
| D267 | `frontend/src/lib/storage.ts:28` | storage.strategyPool/screenerResultColumns/screenerCandle/screenerIntraday/screenerCardSize | constant | unreferenced | yes | 確認 |
| D268 | `frontend/src/lib/storage.ts:76` | storage.strategyDraft/strategyModify/strategyBuilderDraft/strategyRules | constant | unreferenced | yes | 確認 |
| D269 | `frontend/src/lib/storage.ts:88` | storage.strategyBacktestQuickRanges/strategyBacktestLast | constant | unreferenced | yes | 確認 |
| D270 | `frontend/src/lib/storage.ts:119` | storage.dataCardVisible/dataCardOrder | constant | unreferenced | yes | 確認 |
| D271 | `frontend/src/lib/useSharedQueries.ts:72` | useDataStatus | hook | unreferenced | yes | 確認 |
| D272 | `frontend/src/lib/useSharedQueries.ts:72` | useDataStatus、api.dataStatus（api.ts:2909）、DataStatus（api.ts:3461）、QK.dataStatus（queryKeys.ts:70） | hook | unreferenced | yes | 確認 |
| D273 | `frontend/src/lib/voiceBroadcast.ts:209` | stopVoice | function | unreferenced | likely | 確認 |
| D274 | `frontend/src/lib/watchlist-columns.ts:164` | createExtColumn（及 list-columns.ts:223 的 createExtColumn） | function | unreferenced | yes | 確認 |
| D275 | `frontend/src/pages/Branding.tsx:79` | MOCK_NAV（'監控'、'財務分析' 兩項） | constant | unreferenced | yes | 確認 |
| D276 | `frontend/src/pages/Monitor.tsx:71` | enterTsRef | other | unreferenced | yes | 確認 |
| D277 | `frontend/src/pages/settings/Account.tsx:14` | SettingsAccountPanel 的 _props | other | unreferenced | likely | 確認 |
| D278 | `frontend/src/pages/settings/DataSources.tsx:174` | RouteCtx.displayOf | other | unreferenced | likely | 確認 |
| D279 | `frontend/src/pages/settings/Monitoring.tsx:29` | SettingsMonitoringPanel 的 _props.highlight | other | unreferenced | likely | 確認 |
| D280 | `frontend/src/pages/settings/Monitoring.tsx:629` | ToggleRow 的 icon prop | other | unreferenced | yes | 確認 |
| D281 | `frontend/src/pages/settings/Monitoring.tsx:673` | Card 的 badge 與 right props | other | unreferenced | yes | 確認 |
| D282 | `frontend/src/vite-env.d.ts:3` | declare module '*.css'（default export string） | type | unreferenced | likely | 確認 |
| D283 | `frontend/vite.config.ts:48` | manualChunks lightweight-charts 分支 | branch | unreferenced | yes | 確認 |
| D284 | `packaging/generate_icon_designs.py:1` | generate_icon_designs | module | unreferenced | likely | 確認 |
| D285 | `packaging/tickflow.spec:104` | _safe_metadata("tickflow") | config | unreferenced | yes | 確認 |

## A 股／TickFlow 遺留、台股模式不可達（137 項）

| ID | 位置 | 符號 | 種類 | 狀態 | 可安全移除 | 驗證 |
|---|---|---|---|---|---|---|
| D286 | `backend/app/api/data.py:55` | invalidate_data_cache | function | legacy_unreachable | unknown | 確認 |
| D287 | `backend/app/api/data.py:218` | A 股表統計：_safe_aggregate_index_daily / _index_enriched / _index_instruments / _etf_instruments / _etf_enriched / _etf_daily / _adj_factor / _instruments / _financials | function | legacy_unreachable | likely | 確認 |
| D288 | `backend/app/api/data.py:595` | status 的 next_instruments_run / next_pipeline_run / last_instruments_run / last_pipeline_run 與 _last_finished | branch | legacy_unreachable | yes | 確認 |
| D289 | `backend/app/api/financials.py:23` | _financial_allowed 的 custom 後備與 _require_financial | function | legacy_unreachable | likely | 確認 |
| D290 | `backend/app/api/financials.py:23` | _financial_allowed 的拒絕分支 | branch | legacy_unreachable | likely | 確認 |
| D291 | `backend/app/api/intraday.py:276` | quote_stream 的 review_progress 迴圈與 depth_updated 事件 | branch | legacy_unreachable | yes | 確認 |
| D292 | `backend/app/api/intraday.py:302` | quote_stream 的 depth_updated 事件分支 | branch | legacy_unreachable | likely | 確認 |
| D293 | `backend/app/api/kline.py:62` | _minute_allowed 的 custom provider 後備判斷 | branch | legacy_unreachable | likely | 確認 |
| D294 | `backend/app/api/kline.py:139` | _get_stock_info 的 legacy instruments 快取路徑 | branch | legacy_unreachable | unknown | 確認 |
| D295 | `backend/app/api/kline.py:207` | _get_asset_info 與 daily-batch／minute 的 etf、index 分支 | function | legacy_unreachable | no | 確認 |
| D296 | `backend/app/api/kline.py:971` | get_minute_batch 的 Pro+ 403 守衛 | branch | legacy_unreachable | likely | 確認 |
| D297 | `backend/app/api/kline.py:1188` | get_minute 內的 `if trade_date is None` 分支 | branch | legacy_unreachable | yes | 確認 |
| D298 | `backend/app/api/settings.py:1161` | update_quote_interval: if not qs 分支 | branch | legacy_unreachable | yes | 確認 |
| D299 | `backend/app/backtest/engine.py:314` | MatcherConfig 無 market_profile 的 A 股預設路徑（lot_size 回傳 100、fees_pct 雙邊佣金、can_exit_after_sessions 直接放行） | branch | legacy_unreachable | no | 確認 |
| D300 | `backend/app/backtest/engine.py:338` | MatcherConfig.sell_transaction_cost 的 stamp_tax_pct 固定稅率分支 | branch | legacy_unreachable | no | 確認 |
| D301 | `backend/app/backtest/fundamentals.py:31` | 整個模組（FUNDAMENTAL_FACTORS 與財務因子接入） | module | legacy_unreachable | no | 可能成立 |
| D302 | `backend/app/backtest/matrix.py:1525` | _populate_matrix_derived_arrays 的非台股 raw_close 複製，以及由 float_shares 推算換手率的分支（含 _write_turnover_rate_matrix 的 10_000 手換算） | branch | legacy_unreachable | no | 確認 |
| D303 | `backend/app/backtest/matrix.py:2304` | _limit_lock_matrices 的自行推算漲跌停分支（以及 MAIN_BOARD_ST_LIMIT_CHANGE_DATE、numpy_limit_pct_vectors、numpy_limit_price 的 import，_instrument_axis_values 的 limit_up/limit_down） | branch | legacy_unreachable | no | 確認 |
| D304 | `backend/app/backtest/matrix.py:4035` | _build_basic_filter_mask_uncached 的 exclude_st 區塊 | branch | legacy_unreachable | likely | 可能成立 |
| D305 | `backend/app/backtest/matrix.py:4420` | vwap_bias 的 lots 分支（volume × 100） | branch | legacy_unreachable | likely | 確認 |
| D306 | `backend/app/backtest/matrix.py:4659` | _symbol_in_boards 的 A 股板塊分支（沪主板/深主板/创业板/科创板/北交所） | branch | legacy_unreachable | likely | 確認 |
| D307 | `backend/app/backtest/optimizer.py:204` | OptimizeConfig.max_workers（含 WalkForwardConfig.max_workers） | config | legacy_unreachable | no | 確認 |
| D308 | `backend/app/backtest/strategy.py:87` | BENCHMARK_SYMBOL / BENCHMARK_NAME（000001.SH 上证指数）與 _load_benchmark_closes 的 get_index_daily 分支 | constant | legacy_unreachable | no | 確認 |
| D309 | `backend/app/backtest/walkforward.py:236` | backtest_kwargs.get("asset_type") 分支 | branch | legacy_unreachable | no | 確認 |
| D310 | `backend/app/data_providers/custom/loader.py:509` | _load_builtin_plugins, install_plugin, uninstall_plugin, probe_plugin_key 等內建外掛機制 | function | legacy_unreachable | unknown | 確認 |
| D311 | `backend/app/data_providers/custom/provider.py:193` | GenericHTTPProvider.get_financials | method | legacy_unreachable | likely | 確認 |
| D312 | `backend/app/indicators/pipeline.py:88` | _get_custom_signal_exprs_today / _custom_signal_exprs_today(:71) | function | legacy_unreachable | no | 確認 |
| D313 | `backend/app/indicators/pipeline.py:224` | ENRICHED_COLUMNS 的 JOIN 條目 name／total_shares／float_shares（来自 instruments） | constant | legacy_unreachable | unknown | 可能成立 |
| D314 | `backend/app/indicators/pipeline.py:876` | _statutory_limit_prices 的非台股分支（polars_limit_price 分位四捨五入） | branch | legacy_unreachable | no | 確認 |
| D315 | `backend/app/indicators/pipeline.py:998` | compute_limit_signals 的 A 股維表權威漲跌停分支（:998-1019）與 _is_st（:934-943） | branch | legacy_unreachable | no | 確認 |
| D316 | `backend/app/indicators/pipeline.py:1222` | run_pipeline | function | legacy_unreachable | likely | 確認 |
| D317 | `backend/app/indicators/pipeline.py:1537` | _load_recent_history / _load_factors(:1526) / _select_storage_cols(:1216) | function | legacy_unreachable | likely | 確認 |
| D318 | `backend/app/indicators/pipeline.py:1598` | compute_enriched_today / _compute_limit_signals_today(:1943) | function | legacy_unreachable | no | 確認 |
| D319 | `backend/app/indicators/pipeline.py:1598` | compute_enriched_today | function | legacy_unreachable | unknown | 確認 |
| D320 | `backend/app/jobs/daily_pipeline.py:173` | _reprobe_capabilities 排程 | function | legacy_unreachable | likely | 確認 |
| D321 | `backend/app/main.py:453` | capability_denied_handler | function | legacy_unreachable | likely | 確認 |
| D322 | `backend/app/price_limits.py:26` | A 股板別與 ST 規則：MAIN_BOARD_ST_LIMIT_CHANGE_DATE、LEGACY_MAIN_BOARD_ST_LIMIT、GROWTH_BOARD_LIMIT、BEIJING_BOARD_LIMIT、is_risk_warning_name、polars_is_risk_warning_name、polars_limit_price、numpy_limit_pct_vectors 的 legacy 向量 | constant | legacy_unreachable | no | 確認 |
| D323 | `backend/app/repository.py:230` | DataStore._migrate_legacy_data_dir | method | legacy_unreachable | likely | 確認 |
| D324 | `backend/app/repository.py:323` | depth5 與 financials_* DuckDB 視圖、depth5 子目錄 | other | legacy_unreachable | likely | 確認 |
| D325 | `backend/app/repository.py:813` | _restore_missing_latest_rows | method | legacy_unreachable | unknown | 確認 |
| D326 | `backend/app/repository.py:874` | _build_live_agg / get_live_agg | method | legacy_unreachable | no | 確認 |
| D327 | `backend/app/services/ext_presets.py:231` | ext_presets（整個模組：ensure_builtin_presets、fetch_preset、get_preset、_seed_one、_fetch_json、_presets、_concept_preset、_industry_preset、shy313.com URL 常數） | module | legacy_unreachable | likely | 確認 |
| D328 | `backend/app/services/ext_pull.py:98` | _apply_preset_flatten 的非直通分支（ext_gn_ths／ext_hy_ths） | branch | legacy_unreachable | likely | 確認 |
| D329 | `backend/app/services/kline_sync.py:130` | sync_daily_batch 的 TickFlow 分支（:130-176）＋ _normalize_daily ＋ CANONICAL_DAILY_COLS ＋ failed_out 參數 | branch | legacy_unreachable | likely | 確認 |
| D330 | `backend/app/services/kline_sync.py:280` | _normalize_adj_factor | function | legacy_unreachable | likely | 確認 |
| D331 | `backend/app/services/kline_sync.py:662` | sync_minute_batch 的 TickFlow 分段迴圈（:662-735，含 vulture 報的未使用變數 seg_idx :685、seg_total :691） | branch | legacy_unreachable | likely | 確認 |
| D332 | `backend/app/services/kline_sync.py:815` | fetch_intraday_monitor_batch 的 TickFlow 分支（:815-834）＋ _normalize_intraday_raw | branch | legacy_unreachable | likely | 確認 |
| D333 | `backend/app/services/kline_sync.py:858` | fetch_minute_single 的 TickFlow 分支（:858-877） | branch | legacy_unreachable | likely | 確認 |
| D334 | `backend/app/services/kline_sync.py:880` | fetch_adj_factor_single | function | legacy_unreachable | likely | 確認 |
| D335 | `backend/app/services/mining_candidates.py:424` | commission_pct 0.0002 與 stamp_tax_pct 0.0005 的預設值 | constant | legacy_unreachable | no | 確認 |
| D336 | `backend/app/services/mining_candidates.py:430` | _promotion_config 的 name[:80] 截斷與 or signature 後備 | branch | legacy_unreachable | unknown | 確認 |
| D337 | `backend/app/services/mining_jobs.py:26` | 狀態 skipped_prerequisite | constant | legacy_unreachable | no | 確認 |
| D338 | `backend/app/services/pipeline_jobs.py:159` | JobStore.create/start/succeed/fail/progress（job_store 生產端） | method | legacy_unreachable | no | 確認 |
| D339 | `backend/app/services/preferences.py:297` | get_pipeline_pull_a_share / get_pipeline_pull_etf / get_pipeline_pull_index / get_pipeline_pull_types / set_pipeline_pull_types / _PIPELINE_PULL_KEYS | function | legacy_unreachable | likely | 確認 |
| D340 | `backend/app/services/preferences.py:312` | get_regime_batch_days / get_regime_warmup_days / get_pipeline_regime_enabled | function | legacy_unreachable | unknown | 確認 |
| D341 | `backend/app/services/preferences.py:368` | get_mainline_max_members / get_mainline_min_members / get_mainline_blacklist / get_mainline_filter_config / set_mainline_filter_config | function | legacy_unreachable | likely | 確認 |
| D342 | `backend/app/services/preferences.py:465` | get_pipeline_index_symbols / set_pipeline_index_symbols | function | legacy_unreachable | likely | 確認 |
| D343 | `backend/app/services/preferences.py:476` | get_pipeline_schedule / get_instruments_schedule / set_pipeline_schedule / set_instruments_schedule | function | legacy_unreachable | likely | 確認 |
| D344 | `backend/app/services/preferences.py:521` | get_index_daily_batch_size / set_index_daily_batch_size / get_minute_sync_enabled | function | legacy_unreachable | unknown | 確認 |
| D345 | `backend/app/services/preferences.py:735` | set_webhook_enabled_default / get_webhook_enabled_default | function | legacy_unreachable | likely | 確認 |
| D346 | `backend/app/services/quote_service.py:142` | QuoteSubscriber.notify_depth 與 _depth_updated 旗標 | method | legacy_unreachable | likely | 確認 |
| D347 | `backend/app/services/quote_service.py:221` | TIER_MIN_INTERVAL / _current_tier | constant | legacy_unreachable | likely | 確認 |
| D348 | `backend/app/services/quote_service.py:326` | is_realtime_allowed() 為 False 與 watchlist 模式的分支 | branch | legacy_unreachable | likely | 確認 |
| D349 | `backend/app/services/quote_service.py:690` | _holding_for_suspected_closure 中的 is_trading_day(now.date()) is True | branch | legacy_unreachable | unknown | 確認 |
| D350 | `backend/app/services/quote_service.py:814` | _fetch_full_market_quotes 的 TickFlow universes 分支（:814-895） | branch | legacy_unreachable | likely | 確認 |
| D351 | `backend/app/services/quote_service.py:1035` | _fetch_watchlist_quotes | method | legacy_unreachable | likely | 確認 |
| D352 | `backend/app/services/quote_service.py:1035` | QuoteService._fetch_watchlist_quotes | method | legacy_unreachable | likely | 確認 |
| D353 | `backend/app/share_capital.py:10` | load_share_history，以及 apply_historical_float_shares 的非台股 coalesce 分支（:116-118） | function | legacy_unreachable | no | 確認 |
| D354 | `backend/app/strategy/custom_signals.py:213` | build_expressions 的 allow_shift=False 分支（193-215） | 條件分支 | legacy_unreachable | no | 可能成立 |
| D355 | `backend/app/strategy/engine.py:1684` | _basic_filter_expr 的 A 股板塊分支（沪主板、深主板、创业板、科创板、北交所、柜买） | branch | legacy_unreachable | likely | 確認 |
| D356 | `backend/app/strategy/monitor.py:89` | StrategyMonitorService.on_quote_update(89) / stop_all(80) / add_rule(465) | method | legacy_unreachable | likely | 確認 |
| D357 | `backend/app/strategy/monitor.py:415` | _rule_state_signature 的 abnormal_window | branch | legacy_unreachable | yes | 確認 |
| D358 | `backend/app/strategy/monitor.py:661` | evaluate 中的 rule type "abnormal" 分支 | branch | legacy_unreachable | likely | 確認 |
| D359 | `backend/app/strategy/monitor.py:926` | _apply_scope 的 scope == "sector" 分支 | branch | legacy_unreachable | no | 確認 |
| D360 | `backend/app/strategy/monitor.py:1298` | _default_message 的 new_entry/dropped 事件文案 | branch | legacy_unreachable | likely | 確認 |
| D361 | `backend/app/strategy/monitor.py:1343` | _format_conditions_text 的 op_map（gte/lte/gt/lt/eq） | branch | legacy_unreachable | no | 確認 |
| D362 | `backend/app/strategy/monitor_rules.py:34` | 策略規則的 direction 欄位與 DIRECTIONS | config | legacy_unreachable | unknown | 確認 |
| D363 | `backend/app/strategy/monitor_rules.py:282` | normalize 的 webhook_url / webhook_enabled 預設 | config | legacy_unreachable | unknown | 確認 |
| D364 | `backend/app/taiwan/ai_research.py:142` | market_context 與 industry_context 狀態判斷中的 "current" | branch | legacy_unreachable | yes | 確認 |
| D365 | `backend/app/taiwan/ai_research.py:265` | build_evidence_registry 中 fundamentals_context.status == "available" 分支 | branch | legacy_unreachable | no | 確認 |
| D366 | `backend/app/taiwan/bootstrap.py:133` | safe_extract_zip 的 AttributeError 後備分支 | branch | legacy_unreachable | yes | 確認 |
| D367 | `backend/app/taiwan/daily_refresh.py:258` | refresh_symbols 的 symbols is None / end=date.today() / start=self._start_date 後備分支 | branch | legacy_unreachable | unknown | 確認 |
| D368 | `backend/app/taiwan/detail_service.py:683` | _aggregate_market_context 的 if indices: 分支，以及只為它存在的 index_provider 依賴（:42 import、:69 參數、:80 屬性） | branch | legacy_unreachable | likely | 確認 |
| D369 | `backend/app/taiwan/detail_service.py:683` | _aggregate_market_context 的 `if indices:` 分支 | branch | legacy_unreachable | no | 確認 |
| D370 | `backend/app/taiwan/enrichment/index.py` | TaiwanIndexProvider.parse_taiex_rows / parse_tpex_rows | method | legacy_unreachable | no | 確認 |
| D371 | `backend/app/taiwan/market_intelligence.py:573` | get_snapshot 中 daily_status = "stale" 分支 | branch | legacy_unreachable | likely | 確認 |
| D372 | `backend/app/taiwan/providers/hybrid_provider.py` | primary != 'official' 分支（:127 的 else [self.finmind, self.yahoo]）與 finmind_token 參數 | branch | legacy_unreachable | likely | 確認 |
| D373 | `backend/app/taiwan/providers/snapshot_provider.py` | _parse_twse_table 的 `if vol_val > 0 and (...): pass`（:241-244）與 `float(amt_val) if amt_val is not None else None`（:254、:340） | branch | legacy_unreachable | yes | 確認 |
| D374 | `backend/app/taiwan/screener.py:460` | ScreenerResultItem.foreign_net_5d、investment_trust_net_5d，以及 :460-461 的 null 佔位 | other | legacy_unreachable | likely | 確認 |
| D375 | `backend/app/taiwan/universe/adapters.py:396` | TwseInstrumentAdapter.get_instruments 中的 `if prod_types is None and html_content is None` | branch | legacy_unreachable | yes | 確認 |
| D376 | `backend/app/tickflow/client.py:9` | PAID_ENDPOINT, _base_url | constant | legacy_unreachable | likely | 確認 |
| D377 | `backend/app/tickflow/pools.py` | get_pool, DEMO_SYMBOLS, PoolId, _pool_cache_path | module | legacy_unreachable | likely | 確認 |
| D378 | `backend/app/volume_units.py:24` | volume_shares_expr 的非台股 ×100 分支，以及 volume_unit_for_symbols 的 'lots' | branch | legacy_unreachable | no | 確認 |
| D379 | `backend/pyproject.toml:23` | fastexcel | dependency | legacy_unreachable | unknown | 確認 |
| D380 | `backend/tests/test_intraday_monitor_signals.py:163` | test_intraday_batch_provider_is_normalized_without_network | function | legacy_unreachable | likely | 確認 |
| D381 | `frontend/src/components/AlertToast.tsx:96` | SOURCE_BADGE.new_entry / SOURCE_BADGE.dropped | constant | legacy_unreachable | likely | 確認 |
| D382 | `frontend/src/components/DimensionMembersDialog.tsx:61` | stockName/stockSymbol 的簡體欄名備援（'股票简称'、'名称'、'股票代码'、'代码'） | function | legacy_unreachable | unknown | 確認 |
| D383 | `frontend/src/components/EChartsCandlestick.tsx:656` | ranges（markArea 區間高亮）路徑 | branch | legacy_unreachable | unknown | 確認 |
| D384 | `frontend/src/components/EChartsIntraday.tsx:39` | showLimitLines / showAvgLine props 與 !showLimitLines 指數分支 | branch | legacy_unreachable | likely | 確認 |
| D385 | `frontend/src/components/Layout.tsx:191` | pipelineJobs query + dataSyncJustDone + '/data' 指示分支 | branch | legacy_unreachable | likely | 確認 |
| D386 | `frontend/src/components/Layout.tsx:264` | isWatchlistMode 相關分支（自選即時模式提示、handleToggle 的 mode==='watchlist' 導頁） | branch | legacy_unreachable | likely | 確認 |
| D387 | `frontend/src/components/Layout.tsx:268` | 'tickflow' 比較（realtimeProvider / activeProvider） | branch | legacy_unreachable | likely | 確認 |
| D388 | `frontend/src/components/Layout.tsx:590` | 「即時行情不可用」區塊 | branch | legacy_unreachable（僅首次載入、prefs 未到時短暫顯示） | unknown | 確認 |
| D389 | `frontend/src/components/ListColumnCustomizer.tsx:76` | isStrategies / renderStrategiesConfig | branch | legacy_unreachable | likely | 確認 |
| D390 | `frontend/src/components/ListColumnCustomizer.tsx:220` | addExtColumn 插入到 builtin:action 之前的分支（220-225） | 條件分支 | legacy_unreachable | likely | 確認 |
| D391 | `frontend/src/components/StockInfoBar.tsx:173` | 財務指標欄位（eps、bps、roe、gross_margin、net_margin、debt_ratio、revenue_yoy、net_income_yoy、pe_ttm、pb）與 StockPanel 的 useFinancialMetrics 門控 | branch | legacy_unreachable | unknown | 確認 |
| D392 | `frontend/src/components/StockInfoBar.tsx:243` | 自選 Star、加監控 RadioTower 按鈕分支（含 WatchlistAddMenu 匯入） | branch | legacy_unreachable | likely | 確認 |
| D393 | `frontend/src/components/StockPreviewDialog.tsx:77` | isTaiwanSymbol A 股防護分支 | branch | legacy_unreachable（未完全證實） | unknown | 可能成立 |
| D394 | `frontend/src/components/StrategyPickPerformance.tsx:317` | BACKFILL_REASONS.as_of_mismatch | 對照表項目 | legacy_unreachable | likely | 確認 |
| D395 | `frontend/src/components/WatchlistGroupCards.tsx:121` | r.rt_price 與 r.rt_name 的回退（121、128、130、139） | 欄位讀取分支 | legacy_unreachable | likely | 確認 |
| D396 | `frontend/src/components/monitor/RuleEditor.tsx:554` | simple 模式的「編輯監控」標題分支 | branch | legacy_unreachable | likely | 確認 |
| D397 | `frontend/src/components/monitor/TaiwanAlertsList.tsx:88` | 舊 TaiwanAlertEvent 欄位分支（alert_id、triggered_at、trigger_value、threshold、source==='twse:mis'、severity 'warning'） | branch | legacy_unreachable | likely | 確認 |
| D398 | `frontend/src/components/signals/CustomSignalDialog.tsx:280` | FieldPicker 的 filteredFields 平面清單回退（280-284、354-366） | 渲染分支 | legacy_unreachable | likely | 確認 |
| D399 | `frontend/src/lib/queryKeys.ts:123` | SSE_INVALIDATE_PREFIXES 中的 'watchlist-quotes'、'index-quotes'、'overview-market' | constant | legacy_unreachable | likely | 確認 |
| D400 | `frontend/src/lib/queryKeys.ts:127` | SSE_INVALIDATE_PREFIXES 的 'overview-market' | config | legacy_unreachable | yes | 確認 |
| D401 | `frontend/src/lib/stock-table.ts:22` | SIGNAL_FIELDS 中的 'signal_broken_board_recovery'（反包） | constant | legacy_unreachable | yes | 確認 |
| D402 | `frontend/src/lib/stock-table.ts:22` | SIGNAL_FIELDS 中的 signal_broken_board_recovery | constant | legacy_unreachable | yes | 確認 |
| D403 | `frontend/src/lib/stock-table.ts:65` | rt_price/rt_pct/rt_amount 備援（stock-table.ts:65-71；watchlistGroupStats.ts:38） | branch | legacy_unreachable | likely | 確認 |
| D404 | `frontend/src/lib/useQueryConfig.ts:26` | storage.queryConfig 自訂設定路徑（loadConfig 的非預設分支、DEFAULT_QUERY_CONFIG export） | config | legacy_unreachable | likely | 確認 |
| D405 | `frontend/src/lib/useQuoteStream.ts:175` | depth_updated 監聽與 source==='depth' 分支（84-91），以及 StrategyAlertEvent.source 'depth'（api.ts:2312） | branch | legacy_unreachable | likely | 確認 |
| D406 | `frontend/src/lib/useQuoteStream.ts:175` | depth_updated 事件監聽 | branch | legacy_unreachable | likely | 確認 |
| D407 | `frontend/src/lib/watchlist-columns.ts:36` | 流通值欄 builtin:float_val（stock-table.ts:71 排序；stock-info-fields.ts:25 builtin:float_market_cap） | constant | legacy_unreachable | no | 確認 |
| D408 | `frontend/src/lib/watchlist-columns.ts:73` | builtin:limit_ups/limit_downs（連續漲停/跌停；stock-table.ts:99-100） | constant | legacy_unreachable | no | 確認 |
| D409 | `frontend/src/lib/watchlistGroupStats.ts:38` | rowPct 內 row.rt_pct 的回退讀取 | 欄位讀取分支 | legacy_unreachable | likely | 確認 |
| D410 | `frontend/src/pages/Branding.tsx:83` | Branding | component | legacy_unreachable | likely | 可能成立 |
| D411 | `frontend/src/pages/Dashboard.tsx:107` | ev.symbol?.replace(/\.(SH／SZ／BJ)$/, '') | branch | legacy_unreachable | likely | 確認 |
| D412 | `frontend/src/pages/Settings.tsx:108` | TabDef.badge 與其渲染分支 | branch | legacy_unreachable | likely | 確認 |
| D413 | `frontend/src/pages/Watchlist.tsx:84` | renderExtValue / renderExtCell / StockCard extCols 與 DimensionMembersDialog 點擊 | function | legacy_unreachable | no | 確認 |
| D414 | `frontend/src/pages/Watchlist.tsx:342` | 搜尋結果 asset_type==='index'「指數」徽章 | branch | legacy_unreachable | likely | 確認 |
| D415 | `frontend/src/pages/Watchlist.tsx:493` | r.rt_price / r.rt_pct / r.rt_name / r.rt_amount 回退 | branch | legacy_unreachable | likely | 確認 |
| D416 | `frontend/src/pages/Watchlist.tsx:851` | minuteSymbols 的 index 排除與分時欄 index 佔位分支 | branch | legacy_unreachable | no | 確認 |
| D417 | `frontend/src/pages/Watchlist.tsx:1099` | excludeST / toggleExcludeST 與「排除ST」按鈕 | branch | legacy_unreachable | likely | 確認 |
| D418 | `frontend/src/pages/settings/AI.tsx:193` | handlePreset 的非 OpenAI、非自訂分支 | branch | legacy_unreachable | likely | 確認 |
| D419 | `frontend/src/pages/settings/MenuSettings.tsx:27` | NavEntry.type（固定為 'builtin'）與「類型」欄 | type | legacy_unreachable | likely | 確認 |
| D420 | `frontend/src/pages/settings/MenuSettings.tsx:34` | 指向 ASHARE_LEGACY_NAV 與「中國 A 股功能」小節的註解 | other | legacy_unreachable | yes | 確認 |
| D421 | `frontend/src/pages/settings/System.tsx:40` | 「Google 中國大陸偏好」註解（40、214 行） | other | legacy_unreachable | yes | 確認 |
| D422 | `frontend/src/router.tsx:112` | settings/queries 舊路由重新導向 | config | legacy_unreachable | likely | 可能成立 |

## 只有測試引用（111 項）

| ID | 位置 | 符號 | 種類 | 狀態 | 可安全移除 | 驗證 |
|---|---|---|---|---|---|---|
| D423 | `backend/app/api/backtest.py:120` | /api/backtest/candidates CRUD（candidates_list、candidate_create、candidate_update、candidate_delete） | endpoint | test_only | likely | 確認 |
| D424 | `backend/app/api/backtest.py:331` | strategy_stream / strategy_cancel / optimize_stream / optimize_cancel / walkforward_stream / walkforward_cancel 及其輔助（_BacktestJob、_running_jobs、_make_job_key、_make_opt_job_key、_make_wf_job_key、_opt_backtest_kwargs、_OPT_BT_FIELDS、_json_safe、_TRUE_QUERY_VALUES、STRATEGY_DEFAULT_DAYS、WALKFORWARD_MAX_SERVER_SESSIONS） | endpoint | test_only | likely | 確認 |
| D425 | `backend/app/api/backtest.py:331` | strategy/stream、strategy/cancel、optimize/stream、optimize/cancel、walkforward/stream、walkforward/cancel、candidates GET/POST/PATCH（:331、555、689、877、919、1123、120、128、144） | endpoint | test_only | unknown | 確認 |
| D426 | `backend/app/api/kline.py:241` | _get_price_limit_info 的 A 股分支與 _apply_instrument_limit_prices | branch | test_only | likely | 確認 |
| D427 | `backend/app/api/mining.py:42` | 整個 /api/backtest/mining router | module | test_only | unknown | 確認 |
| D428 | `backend/app/api/mining.py:122` | availability、POST runs、result、events、promote、publish、PATCH config（:122、166、240、270、336、349、369） | endpoint | test_only | unknown | 確認 |
| D429 | `backend/app/api/screener.py` | strategies | endpoint | test_only | unknown | 確認 |
| D430 | `backend/app/api/screener.py:219` | strategies、run_preset、cached-summary、cached-result、run_all（:219、269、432、466、821） | endpoint | test_only | likely | 確認 |
| D431 | `backend/app/api/settings.py` | update_mining_schedule | endpoint | test_only | unknown | 確認 |
| D432 | `backend/app/api/settings.py:710` | PUT /api/settings/preferences/mining-schedule | endpoint | test_only | likely | 確認 |
| D433 | `backend/app/api/strategy.py:357` | run、run-all、POST/PATCH config、source、build/stream、DELETE /{id}、reload（:357、405、470、483、865、964、1177、1292） | endpoint | test_only | unknown | 確認 |
| D434 | `backend/app/api/watchlist.py:203` | PUT /api/watchlist/{symbol}/group | endpoint | test_only | likely | 確認 |
| D435 | `backend/app/backtest/engine.py:1001` | BacktestEngine._load_panel_inner（columns=None 分支：get_enriched_range 快取與 compute_all） | branch | test_only | likely | 確認 |
| D436 | `backend/app/backtest/engine.py:1071` | BacktestEngine.simulate_independent_candidates | method | test_only | no | 確認 |
| D437 | `backend/app/backtest/engine.py:1549` | BacktestEngine._resolve_minute_fill 的 VWAP 分支 | branch | test_only | unknown | 確認 |
| D438 | `backend/app/backtest/engine.py:1641` | simulate_portfolio(1641) / simulate_independent_candidates(1071) | method | test_only | no | 確認 |
| D439 | `backend/app/backtest/engine.py:1641` | BacktestEngine.simulate_portfolio | method | test_only | no | 確認 |
| D440 | `backend/app/backtest/engine.py:2467` | BacktestEngine._calc_portfolio_stats | method | test_only | likely | 確認 |
| D441 | `backend/app/backtest/matrix.py:643` | build_market_data_matrix 的 raw_close 缺欄時以 close 代替 | branch | test_only | likely | 確認 |
| D442 | `backend/app/backtest/matrix.py:656` | 矩陣欄位 price_limit_pct（:656-664、:948-949、:1025-1032、:1196-1203） | branch | test_only | unknown | 確認 |
| D443 | `backend/app/backtest/matrix.py:3450` | rolling_quantile | function | test_only | no | 確認 |
| D444 | `backend/app/backtest/matrix.py:3564` | ewm_adjust_false | function | test_only | no | 確認 |
| D445 | `backend/app/backtest/mining.py:38` | JsonDataclassMixin.to_json | method | test_only | likely | 確認 |
| D446 | `backend/app/backtest/mining.py:98` | NestedValidationConfig.embargo_bars / ValidationFold.embargo_labels | config | test_only | unknown | 確認 |
| D447 | `backend/app/backtest/mining.py:267` | MiningRequest.for_profile | method | test_only | likely | 確認 |
| D448 | `backend/app/backtest/mining.py:475` | compute_rank_correlation 的 start/end 參數 | branch | test_only | likely | 確認 |
| D449 | `backend/app/backtest/mining.py:640` | beam_search_factor_combinations 的 train_start/train_end 參數 | branch | test_only | likely | 確認 |
| D450 | `backend/app/backtest/mining.py:923` | MiningService.run 的 evaluator=None 代理分支與 factor_metrics 參數 | branch | test_only | no | 確認 |
| D451 | `backend/app/backtest/mining.py:1630` | _evaluate 與非 labels 的評估路徑（:899-904、:1058-1063、:1200-1206、:1266-1282） | function | test_only | no | 確認 |
| D452 | `backend/app/backtest/mining_runtime.py:116` | RuntimeRequest.stamp_tax_pct / RuntimeRequest.correlation_threshold | other | test_only（stamp_tax_pct）；unreferenced（correlation_threshold） | likely | 確認 |
| D453 | `backend/app/backtest/mining_runtime.py:129` | TrainingMetricProvider.calls | other | test_only | likely | 確認 |
| D454 | `backend/app/backtest/mining_runtime.py:857` | forward_horizon 為 1 或 3 的分支 | branch | test_only | no | 確認 |
| D455 | `backend/app/backtest/mining_runtime.py:885` | require_regime=False 分支（:552、:885、:945、:1144） | branch | test_only | no | 確認 |
| D456 | `backend/app/backtest/optimizer.py:116` | count_combinations | function | test_only | likely | 確認 |
| D457 | `backend/app/backtest/regime_alignment.py:74` | align_regime_t_minus_one(first_day_boundary_ok=...) | branch | test_only | likely | 確認 |
| D458 | `backend/app/enriched_generation.py:217` | bump_enriched_generation | function | test_only | no | 確認 |
| D459 | `backend/app/extensions/registry.py:62` | has_customizations | method | test_only | no | 確認 |
| D460 | `backend/app/price_limits.py:38` | taiwan_price_limit_pct，以及 board_limit_pct（:63-64）、price_limit_pct（:79-80）的台股分支 | function | test_only | likely | 確認 |
| D461 | `backend/app/price_limits.py:238` | numpy_price_limit_matrix | function | test_only | unknown | 確認 |
| D462 | `backend/app/repository.py:1417` | get_live_agg_date | method | test_only | no | 確認 |
| D463 | `backend/app/repository.py:2250` | _bump_matrix_data_generation | method | test_only | no | 確認 |
| D464 | `backend/app/repository.py:2294` | append_enriched | method | test_only | no | 確認 |
| D465 | `backend/app/services/data_integrity.py:142` | scan_recent_integrity | function | test_only | unknown | 確認 |
| D466 | `backend/app/services/ext_data.py:277` | ExtConfigStore.delete | method | test_only | likely | 確認 |
| D467 | `backend/app/services/heavy_job_limiter.py:38` | HeavyJobLimiter.in_use / available | method | test_only | no | 確認 |
| D468 | `backend/app/services/heavy_job_limiter.py:105` | timeout 參數與 HeavyJobLimitTimeoutError | branch | test_only | no | 確認 |
| D469 | `backend/app/services/heavy_job_limiter.py:121` | heavy_job_limiter（別名） | constant | test_only | likely | 確認 |
| D470 | `backend/app/services/market_phase.py:172` | market_phase（整個模組） | module | test_only | unknown | 確認 |
| D471 | `backend/app/services/mining_schedule.py:28` | BEIJING_TZ / beijing_now / beijing_date / iso_week | function | test_only | likely | 確認 |
| D472 | `backend/app/services/mining_schedule.py:53` | build_default_request | function | test_only | likely | 確認 |
| D473 | `backend/app/services/mining_schedule.py:121` | schedule_claim | function | test_only | likely | 確認 |
| D474 | `backend/app/services/mining_schedule.py:126` | run_weekly_mining（週期自動 mining 排程） | function | test_only | unknown | 確認 |
| D475 | `backend/app/services/mining_schedule.py:126` | run_weekly_mining | function | test_only | no | 確認 |
| D476 | `backend/app/services/mining_schedule.py:174` | _prerequisite_error / _record_skipped_prerequisite / _CLAIM_LOCK | function | test_only | likely | 確認 |
| D477 | `backend/app/services/pipeline_jobs.py:159` | JobStore 生產端生命週期（create / start / succeed / progress）＋ JobCancelledError ＋ request_cancel ＋ is_cancelled ＋ try_acquire_run_slot ＋ release_run_slot | method | test_only（request_cancel/release_run_slot 經 terminate 屬正式可達但實為 no-op） | unknown | 可能成立 |
| D478 | `backend/app/services/pipeline_jobs.py:159` | JobStore.create/reap_stale/terminate 與 run slot | function | test_only（create、try_acquire_run_slot）；reap_stale、terminate 另有已死端點呼叫 | likely | 可能成立 |
| D479 | `backend/app/services/pipeline_jobs.py:457` | try_acquire_run_slot | function | test_only | likely | 確認 |
| D480 | `backend/app/services/quote_service.py:1165` | QuoteService._split_records_by_asset | method | test_only | likely | 確認 |
| D481 | `backend/app/services/regime_builder.py:637` | compute_regime_incremental | function | test_only | unknown | 確認 |
| D482 | `backend/app/services/regime_builder.py:637` | regime 計算路徑（compute_regime_incremental、run_regime_batch、_aggregate_daily、_scan_enriched_fallback、_compute_batch、_load_index_pct、load_risk_warning_symbols、classify_state、upsert_regime_history、refresh_phase_labels、detect_stale_dates、enriched_date_set） | function | test_only | unknown | 確認 |
| D483 | `backend/app/services/strategy_pick_history.py:48` | signal_raw_close | parquet 欄位（寫入於 :451） | test_only | likely | 確認 |
| D484 | `backend/app/services/strategy_pick_performance.py:354` | _prepare_picks 的 provenance 補值，以及 :512-513、:529-531 的欄位存在判斷、:550 的 runs is None 分支 | 防禦性分支 | test_only | no | 確認 |
| D485 | `backend/app/services/watchlist.py:311` | set_group（以及 api/watchlist.py:203 的 assign_group、frontend/src/lib/api.ts:2702 的 watchlistSetGroup） | 服務函式與路由 | test_only | likely | 確認 |
| D486 | `backend/app/strategy/engine.py:956` | StrategyEngine.realtime_matrix_stats | method | test_only | unknown | 確認 |
| D487 | `backend/app/strategy/monitor.py:471` | MonitorRuleEngine.remove_rule | method | test_only | likely | 確認 |
| D488 | `backend/app/strategy/monitor.py:490` | MonitorRuleEngine.clear | method | test_only | likely | 確認 |
| D489 | `backend/app/taiwan/daily_store.py:222` | TaiwanDailyStore.read_latest_date_rows | method | test_only | likely | 確認 |
| D490 | `backend/app/taiwan/daily_store.py:399` | TaiwanDailyStore.has_symbol_date | method | test_only | likely | 確認 |
| D491 | `backend/app/taiwan/daily_store.py:411` | TaiwanDailyStore.known_symbols | method | test_only | likely | 確認 |
| D492 | `backend/app/taiwan/dividend_events.py:100` | get_dividend_events_as_of | function | test_only | unknown | 確認 |
| D493 | `backend/app/taiwan/dividend_events.py:117` | TaiwanDividendEventStore | class | test_only | no | 確認 |
| D494 | `backend/app/taiwan/dividend_events.py:117` | TaiwanDividendEventStore（含 get_as_of、DividendLifecycleEvent.to_dict/from_dict） | class | test_only | unknown | 確認 |
| D495 | `backend/app/taiwan/enriched_refresh.py:475` | TaiwanEnrichedRefreshService._add_limit_signals | method | test_only | no | 確認 |
| D496 | `backend/app/taiwan/enrichment/factors.py` | compute_chip_factors | function | test_only | likely | 確認 |
| D497 | `backend/app/taiwan/enrichment/factors.py` | compute_margin_factors | function | test_only | likely | 確認 |
| D498 | `backend/app/taiwan/enrichment/factors.py:18` | compute_chip_factors / compute_margin_factors | function | test_only | likely | 確認 |
| D499 | `backend/app/taiwan/enrichment/models.py` | MarketQuote | class | test_only | likely | 確認 |
| D500 | `backend/app/taiwan/enrichment/quote.py` | TaiwanOfficialQuoteProvider | module | test_only | likely | 確認 |
| D501 | `backend/app/taiwan/enrichment/quote.py:167` | get_quote_with_fallback | method | test_only | unknown | 確認 |
| D502 | `backend/app/taiwan/etf_data.py:87` | TaiwanETFDistribution | class | test_only | unknown | 確認 |
| D503 | `backend/app/taiwan/etf_data.py:106` | TaiwanETFHolding | class | test_only | unknown | 確認 |
| D504 | `backend/app/taiwan/etf_data.py:128` | calculate_premium_discount | function | test_only | unknown | 確認 |
| D505 | `backend/app/taiwan/etf_data.py:141` | latest_snapshot_as_of | function | test_only | unknown | 確認 |
| D506 | `backend/app/taiwan/etf_data.py:152` | calculate_premium_discount(128) / latest_snapshot_as_of(141) / TaiwanETFSnapshotStore(152) / TaiwanETFDistribution(87) / TaiwanETFHolding(106) | class | test_only | no | 確認 |
| D507 | `backend/app/taiwan/etf_data.py:152` | TaiwanETFSnapshotStore（含 TaiwanETFSnapshot.to_dict/from_dict） | class | test_only | unknown | 確認 |
| D508 | `backend/app/taiwan/fundamentals.py` | normalize_dividend_status | function | test_only | likely | 確認 |
| D509 | `backend/app/taiwan/fundamentals.py` | ShareCapital | class | test_only | likely | 確認 |
| D510 | `backend/app/taiwan/fundamentals.py` | TaiwanFundamentalStore（含 get_as_of） | class | test_only | unknown | 確認 |
| D511 | `backend/app/taiwan/fundamentals.py` | latest_as_of | function | test_only | unknown | 確認 |
| D512 | `backend/app/taiwan/institutional_store.py` | TaiwanInstitutionalStore.known_symbols | method | test_only | likely | 確認 |
| D513 | `backend/app/taiwan/market_intelligence.py:483` | MarketTrendStats／_calc_trend（:210、:483-543、:677-691）與 change_distribution／_calc_change_distribution（:449-481） | other | test_only（仍經 /api/taiwan/market-intelligence 序列化輸出，repo 內無消費者） | no | 確認 |
| D514 | `backend/app/taiwan/market_rules.py:34` | TaxClass.FOREIGN_ETF | constant | test_only | unknown | 確認 |
| D515 | `backend/app/taiwan/market_rules.py:69` | EXAMPLE_BROKER_MIN_COMMISSION | constant | test_only | unknown | 確認 |
| D516 | `backend/app/taiwan/market_rules.py:444` | PriceLimitModel.numpy_limit_prices / TickSizeModel.numpy_tick_size / _numpy_ladder | method | test_only | unknown | 確認 |
| D517 | `backend/app/taiwan/market_rules.py:481` | SettlementModel 與 TaiwanMarketProfile.settlement / tick_size_model / price_limit_model | class | test_only | unknown | 確認 |
| D518 | `backend/app/taiwan/realtime/holidays.py:90` | parse_schedule | function | test_only | no | 確認 |
| D519 | `backend/app/taiwan/realtime/service.py:244` | TaiwanRealtimeService.get_quote | method | test_only | no | 確認 |
| D520 | `backend/app/taiwan/symbol.py:65` | InstrumentType | class | test_only | no | 確認 |
| D521 | `backend/app/taiwan/symbol.py:198` | from_provider_symbol | function | test_only | no | 確認 |
| D522 | `backend/app/taiwan/symbol.py:253` | format_display / extract_code | function | test_only | no | 確認 |
| D523 | `backend/app/taiwan/universe/adapters.py:348` | fetch_official_twse_etf_products | function | test_only | likely | 確認 |
| D524 | `backend/app/taiwan/universe/adapters.py:381` | TwseInstrumentAdapter.fetch_live_html | method | test_only | likely | 確認 |
| D525 | `backend/app/taiwan/universe/adapters.py:442` | TpexInstrumentAdapter.fetch_etf_management_styles | method | test_only | likely | 確認 |
| D526 | `backend/app/taiwan/universe/models.py:370` | MarketProfileBridge.get_price_limit_class | method | test_only | no | 確認 |
| D527 | `backend/app/taiwan/universe/service.py:461` | TaiwanSecurityMaster.load_from_adapters | method | test_only | no | 確認 |
| D528 | `backend/app/taiwan/universe/service.py:492` | TaiwanSecurityMaster.save_cache | method | test_only | no | 確認 |
| D529 | `backend/scripts/probe_tickflow_pro.py:1` | probe_tickflow_pro (evaluate_gate/run_dry_run/run_live) | module | test_only | likely | 確認 |
| D530 | `backend/scripts/probe_tickflow_pro.py:161` | probe_tickflow_pro（腳本） | module | test_only | unknown | 確認 |
| D531 | `frontend/src/components/stock-analysis/AnalysisKChart.tsx:115` | AnalysisKChart | component | test_only | likely | 確認 |
| D532 | `frontend/src/components/stock-analysis/AnalysisKChart.tsx:115` | AnalysisKChart（含 LevelOverview、collectPriceLines、strengthColor、levelKey、CURVE_DEFS） | component | test_only | likely | 確認 |
| D533 | `frontend/src/pages/settings/DataSourceEditor.tsx:86` | DataSourceEditor 的 initial 非 null 路徑 | other | test_only | no | 確認 |

## 重複實作（31 項）

| ID | 位置 | 符號 | 種類 | 狀態 | 可安全移除 | 驗證 |
|---|---|---|---|---|---|---|
| D534 | `backend/app/api/intraday.py:172` | search_taiwan_instruments（/api/intraday/taiwan/search）與 kline._search_taiwan_instruments | endpoint | duplicate_impl | no | 確認 |
| D535 | `backend/app/api/mining.py:364` | GET/PATCH /api/backtest/mining/config | endpoint | duplicate_impl | likely | 確認 |
| D536 | `backend/app/api/signals.py:72` | _GROUP_LABELS 與 quote_fields（72-83） | 常數與分組邏輯 | duplicate_impl | no | 確認 |
| D537 | `backend/app/backtest/worker.py:129` | encode_optimize_config（與 walkforward 的內聯編碼） | function | duplicate_impl | likely | 確認 |
| D538 | `backend/app/indicators/pipeline.py:908` | compute_limit_signals 在台股發佈中算出的漲跌停、炸板、翹板、連板 | branch | duplicate_impl | no | 確認 |
| D539 | `backend/app/jobs/daily_pipeline.py:23` | _refresh_single_view | function | duplicate_impl | no | 確認 |
| D540 | `backend/app/main.py:448` | 重複匯入 Request/JSONResponse（448-449）、StrategyMonitorService（199） | other | duplicate_impl | yes | 確認 |
| D541 | `backend/app/price_limits.py:141` | TAIWAN_SYMBOL_SUFFIXES | constant | duplicate_impl | likely | 確認 |
| D542 | `backend/app/services/mining_manager.py:31` | _SUCCESS_STATUSES | constant | duplicate_impl | yes | 確認 |
| D543 | `backend/app/strategy/ai_generator.py:56` | _MATRIX_SCORING_FIELDS | 常數（matrix 評分欄位白名單） | duplicate_impl | no | 確認 |
| D544 | `backend/app/strategy/builtin/pullback_resume.py:88` | _liquid | function | duplicate_impl | unknown | 確認 |
| D545 | `backend/app/strategy/custom_signals_ai.py:19` | _GROUP_LABELS | 常數（欄位分組中文標籤） | duplicate_impl | no | 確認 |
| D546 | `backend/app/strategy/custom_signals_ai.py:26` | _QUOTE_FIELDS | 常數（行情欄位集合） | duplicate_impl | no | 確認 |
| D547 | `backend/app/strategy/monitor.py:238` | _is_signal_field / _SIGNAL_PREFIXES | function | duplicate_impl | yes | 確認 |
| D548 | `backend/app/taiwan/enriched_reader.py:19` | ENRICHED_DIR_NAME / _enriched_root（與 enriched_refresh 重複） | function | duplicate_impl（兩份都有使用，不是 dead code） | unknown | 確認 |
| D549 | `backend/app/taiwan/enrichment/index.py` | _parse_tw_date / _clean_float | function | duplicate_impl | likely | 確認 |
| D550 | `backend/app/taiwan/margin_store.py` | TaiwanMarginStore（與 TaiwanInstitutionalStore 幾乎逐行相同） | class | duplicate_impl | no | 確認 |
| D551 | `backend/tests/backtest/test_stage4_mining_warmup.py:22` | _weekdays | test_helper | duplicate_impl | no | 確認 |
| D552 | `backend/tests/test_intraday_quotes_price_limit.py:19` | _cached_security_master | test_helper | duplicate_impl | no | 確認 |
| D553 | `backend/tests/test_stage3_holiday_turnover.py:48` | _instrument | test_helper | duplicate_impl | no | 確認 |
| D554 | `backend/tests/test_stage4_coverage_wording.py:98` | share_capital | test_helper | duplicate_impl | no | 確認 |
| D555 | `frontend/src/components/EChartsCandlestick.tsx:1124` | updateCompactPresentation 內的 markPoint 組裝 | function | duplicate_impl | no | 確認 |
| D556 | `frontend/src/components/EChartsCandlestick.tsx:1221` | initialHTML（與 getInfoBarHTML 重複的資訊列 HTML 組裝） | function | duplicate_impl | likely | 確認 |
| D557 | `frontend/src/components/stock-analysis/AnalysisKChart.tsx:35` | LevelType / PriceLevel / ChartMarker / ChartRange（型別匯出） | type | duplicate_impl | likely | 確認 |
| D558 | `frontend/src/pages/Dashboard.tsx:21` | fmtPrice（本地版） | function | duplicate_impl | likely | 確認 |
| D559 | `frontend/src/pages/Dashboard.tsx:26` | fmtStockPct | function | duplicate_impl | likely | 確認 |
| D560 | `frontend/src/pages/TaiwanScreener.tsx:578` | 市場情報面板的內嵌資料品質徽章 | component | duplicate_impl | likely | 確認 |
| D561 | `frontend/src/pages/Watchlist.boardFilter.test.ts:14` | initialBoardFilter / filterIsActive（測試內複製的實作） | test_helper | duplicate_impl | no | 確認 |
| D562 | `frontend/src/pages/Watchlist.tsx:61` | getBoardType | function | duplicate_impl | likely | 確認 |
| D563 | `frontend/src/pages/settings/DataSources.tsx:386` | editExisting | other | duplicate_impl | likely | 確認 |
| D564 | `frontend/src/pages/settings/MenuSettings.tsx:181` | orderedEntries 的重排邏輯（與 allEntries 重複） | other | duplicate_impl | no | 確認 |

## 其他（可達但無效果、只供已死端點使用等）（17 項）

| ID | 位置 | 符號 | 種類 | 狀態 | 可安全移除 | 驗證 |
|---|---|---|---|---|---|---|
| D565 | `backend/app/api/financials.py:38` | GET /api/financials/metrics | endpoint | reachable_always_empty | no | 確認 |
| D566 | `backend/app/api/kline.py:607` | get_daily 的 TickFlow live 後備與 Cap.ADJ_FACTOR 因子拉取 | branch | reachable_noop | no | 可能成立 |
| D567 | `backend/app/api/kline.py:1028` | get_minute_batch 的 Step 2 實時補拉 | branch | reachable_noop | no | 確認 |
| D568 | `backend/app/api/pipeline.py:43` | GET /api/pipeline/jobs（job_store 已無生產者） | endpoint | reachable_always_inactive | no | 確認 |
| D569 | `backend/app/backtest/optimizer.py:219` | optimizer.py 與 walkforward.py 模組 | module | walkforward.py 為 test_only；optimizer.py 的功能是 test_only，但線上路徑會硬匯入它 | unknown | 可能成立 |
| D570 | `backend/app/repository.py:350` | _register_unified_views, _has_parquet（kline_daily_all / kline_enriched_all / kline_minute_all / instruments_all） | function | called_but_output_unused | likely | 確認 |
| D571 | `backend/app/services/notify_adapter.py:70` | notify (icon 參數) | function | dead_parameter | likely | 確認 |
| D572 | `backend/app/services/screener.py:522` | ScreenerService.run | function | endpoint_only：僅被無呼叫端的已註冊端點 POST /api/screener/run 使用 | yes | 確認 |
| D573 | `backend/app/strategy/prompt_builder.py:25` | build_step1/build_step2，以及 AIStrategyGenerator.generate/stream/repair_code | function | endpoint_only＋測試：僅被無前端呼叫端的 /build、/build/stream、/ai/generate 與測試使用 | unknown | 確認 |
| D574 | `backend/app/tickflow/client.py:20` | get_client | function | reachable_with_custom_provider | no | 可能成立 |
| D575 | `frontend/src/components/taiwan/TaiwanReferenceData.tsx:184` | 「流通股數」資料列 | branch | 恆顯示 '--'（列會渲染，但沒有資料來源） | likely | 確認 |
| D576 | `frontend/src/lib/api.ts:2327` | 46 個沒有元件呼叫的 wrapper | function | 數量修正：169 個中有 36 個全無引用、11 個只和 queryKeys.ts/storage.ts 的鍵同名、4 個只被已死的 miningTask.ts 使用 | likely | 確認 |
| D577 | `frontend/src/pages/Watchlist.tsx:1099` | excludeST（排除ST 過濾） | branch | legacy_ineffective | likely | 確認 |
| D578 | `frontend/src/pages/settings/AI.tsx:156` | clear 成功後寫入快取的 ai_codex_model、ai_codex_command、ai_codex_reasoning_effort | other | dead_write | likely | 確認 |
| D579 | `frontend/src/pages/settings/DataSources.tsx:72` | 'tickflow' 名稱判斷分支（72、354、455、460、506、619、681、738、740） | branch | mostly_unreachable | likely | 可能成立 |
| D580 | `frontend/src/pages/settings/JobTimeoutCard.tsx:32` | JobTimeoutCard（含 Timeout.tsx 的 SettingsTimeoutPanel） | component | reachable_ui_no_runtime_effect | unknown | 確認 |
| D581 | `frontend/src/router.tsx:112` | settings/queries 舊路由重導 | branch | legacy_target_missing | likely | 確認 |

