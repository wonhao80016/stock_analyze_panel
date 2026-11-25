from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.services import strategy_pick_backfill as backfill
from app.services import strategy_pick_history as history
from app.services.screener import ScreenerService
from app.strategy.engine import StrategyResult

DATES = [date(2026, 9, day) for day in (23, 24, 29, 30)] + [date(2026, 10, 1), date(2026, 10, 2)]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for d in DATES:
        path = tmp_path / 'taiwan' / 'daily' / f'date={d}' / 'part.parquet'
        path.parent.mkdir(parents=True)
        pl.DataFrame({'symbol': ['2330.TWSE', '6510.TPEX'], 'date': [d, d]}).write_parquet(path)
    strategy_file = tmp_path / 'strategy.py'
    strategy_file.write_text('META = {}', encoding='utf-8')
    from app.taiwan.realtime.calendar import TAIPEI_TZ
    monkeypatch.setattr(history, 'taipei_now', lambda: datetime(2026, 10, 5, 16, tzinfo=TAIPEI_TZ))
    strategy = SimpleNamespace(
        meta={'id': 'test', 'asset_types': ['stock'], 'timeframes': ['1d']},
        execution_backend='python_current', filter_history_fn=None,
        file_path=strategy_file, basic_filter={}, composite=None,
    )
    calls = []

    def run(context, **kwargs):
        calls.append(context.as_of)
        assert context.current['date'].max() == context.as_of
        assert context.history['date'].max() <= context.as_of
        return {'test': StrategyResult(as_of=context.as_of, strategy_id='test', total=1,
                                      rows=[{'symbol': '2330.TWSE', 'date': context.as_of, 'raw_close': 100.0}])}

    engine = SimpleNamespace(list_strategies=lambda: [strategy.meta], get=lambda sid: strategy,
                             required_history_bars=lambda *args, **kwargs: 2, run_all=run)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(ScreenerService, 'latest_date', lambda self: DATES[-1])

    def current(self, d):
        from app.services.screener import _report_input
        frame = pl.DataFrame({'symbol': ['2330.TWSE', '6510.TPEX'], 'date': [d, d]})
        _report_input(history.CURRENT, 'ok', frame, d)
        return frame

    def past(self, d, count):
        from app.services.screener import _report_input
        frame = pl.DataFrame({'symbol': ['2330.TWSE', '6510.TPEX'], 'date': [d, d]})
        _report_input(history.HISTORY, 'ok', frame, d)
        return frame

    monkeypatch.setattr(ScreenerService, '_load_enriched_for_date', current)
    monkeypatch.setattr(ScreenerService, '_load_enriched_history', past)
    return repo, engine, calls


def test_previous_trading_days_provenance_and_idempotence(setup):
    repo, engine, calls = setup
    result = backfill.run(repo, engine, 5)
    assert calls == DATES[:-1]
    assert result['dates'] == [str(d) for d in DATES[:-1]]
    assert result['written_runs'] == 5
    saved = history.read_picks(repo.store.data_dir)
    assert saved.picks['signal_date'].n_unique() == 5
    assert saved.picks['provenance'].unique().to_list() == ['reconstructed']
    assert saved.picks['recorded_late'].all()
    assert saved.runs['first_recorded_at'].min().date() > DATES[-1]
    version = history.strategy_versions(engine, {}, ['test'])['test']
    assert version and saved.runs['strategy_version'].unique().to_list() == [version]
    run_ids = saved.runs['run_id'].to_list()
    again = backfill.run(repo, engine, 5)
    assert again['written_runs'] == 0 and again['existing_runs'] == 5
    assert history.read_picks(repo.store.data_dir).runs['run_id'].to_list() == run_ids


def test_future_input_and_strategy_failure_do_not_create_empty_success(setup, monkeypatch):
    repo, engine, _ = setup
    original = ScreenerService.build_strategy_context

    def future(self, *args, **kwargs):
        context = original(self, *args, **kwargs)
        context.history = context.history.with_columns(pl.lit(DATES[-1]).alias('date'))
        return context

    monkeypatch.setattr(ScreenerService, 'build_strategy_context', future)
    result = backfill.run(repo, engine, 1)
    assert result['written_runs'] == 0
    assert result['failures'][0]['reason'] == 'future_input'
    monkeypatch.setattr(ScreenerService, 'build_strategy_context', original)
    engine.run_all = lambda *args, **kwargs: {}
    result = backfill.run(repo, engine, 1)
    assert result['failures'][0]['reason'] == 'strategy_failed'
    assert history.read_picks(repo.store.data_dir).runs.is_empty()


def test_zero_signals_has_manifest_and_missing_input_has_failure(setup, monkeypatch):
    repo, engine, _ = setup
    engine.run_all = lambda context, **kwargs: {'test': StrategyResult(
        as_of=context.as_of, strategy_id='test', total=0, rows=[])}
    result = backfill.run(repo, engine, 1)
    assert result['written_runs'] == 1 and result['zero_signal_runs'] == 1
    assert history.read_picks(repo.store.data_dir).picks.is_empty()
    monkeypatch.setattr(ScreenerService, '_load_enriched_for_date', lambda *args: pl.DataFrame())
    result = backfill.run(repo, engine, 2)
    assert result['failures'][0]['reason'] == 'input_unavailable'


@pytest.mark.parametrize('rows', [
    [{'symbol': '2330.TWSE', 'date': DATES[-1]}],
    [{'date': DATES[-2]}],
    [],
])
def test_invalid_strategy_rows_do_not_seal_a_zero_signal_manifest(setup, rows):
    repo, engine, _ = setup
    engine.run_all = lambda context, **kwargs: {'test': StrategyResult(
        as_of=context.as_of, strategy_id='test', total=1, rows=rows)}
    result = backfill.run(repo, engine, 1)
    assert result['written_runs'] == 0 and result['zero_signal_runs'] == 0
    assert result['failures'][0]['reason'] == 'invalid_result_rows'
    assert history.read_picks(repo.store.data_dir).runs.is_empty()


def test_invalid_days_and_no_daily_data(setup, tmp_path):
    repo, engine, _ = setup
    with pytest.raises(ValueError):
        backfill.run(repo, engine, 251)
    repo.store.data_dir = tmp_path / 'empty'
    with pytest.raises(ValueError, match='daily'):
        backfill.run(repo, engine, 5)


def test_recorded_snapshot_and_legacy_manifest_are_preserved(setup):
    repo, engine, _ = setup
    d = DATES[-2]
    report = history.RunReport(
        current=history.InputStatus(state='ok', rows=2, exchanges_at_t=frozenset({'TWSE', 'TPEX'}), dates=(d,)),
        input_groups={'test': frozenset({history.CURRENT})}, fingerprints={'test': 'original'})
    history.record(repo.store.data_dir, d, {'test': {'as_of': d, 'rows': [
        {'symbol': '6510.TPEX', 'date': d}]}}, report, '16:30', None)
    manifest = repo.store.data_dir / 'user_data' / 'strategy_picks' / f'date={d}' / 'runs.parquet'
    pl.read_parquet(manifest).drop('provenance').write_parquet(manifest)
    before = manifest.read_bytes()
    result = backfill.run(repo, engine, 1)
    assert result['written_runs'] == 0 and result['existing_runs'] == 1
    assert manifest.read_bytes() == before
    saved = history.read_picks(repo.store.data_dir).picks
    assert saved['provenance'].to_list() == ['recorded']
    assert saved['symbol'].to_list() == ['6510.TPEX']


def test_backfill_api_validation_and_status_are_scoped(setup):
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.screener import router

    repo, engine, _ = setup
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    app.state.strategy_engine = engine
    client = TestClient(app)
    assert client.post('/api/screener/pick-performance/backfill', json={'days': 0}).status_code == 422
    assert client.get('/api/screener/pick-performance/backfill/unknown').status_code == 404
    response = client.post('/api/screener/pick-performance/backfill', json={'days': 1})
    assert response.status_code == 202
    job_id = response.json()['job_id']
    for _ in range(100):
        payload = client.get(f'/api/screener/pick-performance/backfill/{job_id}').json()
        if payload['status'] != 'running':
            break
        time.sleep(0.01)
    assert payload['status'] == 'completed' and payload['written_runs'] == 1
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=repo.store.data_dir / 'other'))
    assert client.get(f'/api/screener/pick-performance/backfill/{job_id}').status_code == 404
    assert client.post('/api/screener/pick-performance/backfill', json={'days': 5}).status_code == 400


@pytest.fixture
def native_setup(setup, monkeypatch):
    repo, engine, calls = setup
    strategy = engine.get('test')
    strategy.execution_backend = 'matrix_native'
    strategy.meta['limit'] = 10
    strategy.matrix_strategy = SimpleNamespace(required_fields=lambda: {'close'})
    engine._matrix_field_columns = lambda *args: {'close', 'turnover_rate'}
    engine._result_limit = lambda strategy, overrides: overrides.get('display_limit', strategy.meta['limit'])
    original = ScreenerService._load_enriched_history

    def past(self, d, count):
        frame = original(self, d, count)
        prior = max(day for day in DATES if day < d)
        frame = pl.concat([frame.with_columns(pl.lit(prior).alias('date')), frame])
        return frame.with_columns(
            pl.lit(100.0).alias('close'), pl.lit(None, dtype=pl.Float64).alias('turnover_rate'))

    monkeypatch.setattr(ScreenerService, '_load_enriched_history', past)
    monkeypatch.setattr('app.backtest.matrix.build_market_data_matrix', lambda *args, **kwargs: None)
    return repo, engine, calls


def test_missing_filter_dependency_is_failure_not_zero_success(native_setup):
    repo, engine, calls = native_setup
    engine.get('test').matrix_strategy.required_fields = lambda: {'close', 'turnover_rate'}
    result = backfill.run(repo, engine, 1)
    assert result['written_runs'] == 0 and result['zero_signal_runs'] == 0 and calls == []
    assert result['failures'] == [{'date': str(DATES[-2]), 'strategy_id': 'test',
                                  'reason': 'input_unavailable', 'missing_fields': ['turnover_rate']}]
    assert history.read_picks(repo.store.data_dir).runs.is_empty()


def test_short_required_history_is_not_a_zero_signal_success(native_setup):
    repo, engine, calls = native_setup
    engine.required_history_bars = lambda *args, **kwargs: 61
    result = backfill.run(repo, engine, 1)
    assert result['written_runs'] == 0 and result['zero_signal_runs'] == 0 and calls == []
    assert result['failures'][0]['reason'] == 'input_unavailable'
    assert result['failures'][0]['missing_fields'] == ['history']
    assert history.read_picks(repo.store.data_dir).runs.is_empty()


def test_missing_scoring_preserves_full_candidates_with_null_score(native_setup):
    repo, engine, _ = native_setup
    engine.get('test').meta['scoring'] = {'turnover_rate': 1.0}
    result = backfill.run(repo, engine, 1)
    assert result['written_runs'] == 1 and result['scoring_unavailable_runs'] == 1
    saved = history.read_picks(repo.store.data_dir)
    assert saved.picks['symbol'].to_list() == ['2330.TWSE']
    assert saved.picks['score'].to_list() == [None]
    assert saved.runs['calculation_status'].to_list() == ['scoring_unavailable']
    assert saved.runs['missing_fields'].to_list() == ['["turnover_rate"]']


def test_missing_scoring_cannot_keep_a_possibly_truncated_selection(native_setup):
    repo, engine, _ = native_setup
    engine.get('test').meta.update(scoring={'turnover_rate': 1.0}, limit=1)
    result = backfill.run(repo, engine, 1)
    assert result['written_runs'] == 0 and result['failures'][0]['reason'] == 'scoring_selection_unavailable'
    assert history.read_picks(repo.store.data_dir).runs.is_empty()


def test_zero_is_valid_input_and_invalid_reconstruction_can_retry(native_setup, monkeypatch):
    repo, engine, _ = native_setup
    d = DATES[-2]
    engine.get('test').matrix_strategy.required_fields = lambda: {'close', 'turnover_rate'}
    original = ScreenerService._load_enriched_history
    monkeypatch.setattr(ScreenerService, '_load_enriched_history',
                        lambda self, day, count: original(self, day, count).with_columns(pl.lit(0.0).alias('turnover_rate')))
    result = backfill.run(repo, engine, 1)
    assert result['written_runs'] == 1 and result['failures'] == []
    path = repo.store.data_dir / 'user_data' / 'strategy_picks' / f'date={d}' / 'runs.parquet'
    pl.read_parquet(path).with_columns(pl.lit('input_unavailable').alias('calculation_status')).write_parquet(path)
    retry = backfill.run(repo, engine, 1)
    assert retry['written_runs'] == 1 and retry['existing_runs'] == 0
    assert history.read_picks(repo.store.data_dir).runs['calculation_status'].to_list() == ['ok']
