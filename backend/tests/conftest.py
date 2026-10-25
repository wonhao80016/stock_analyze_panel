"""Shared test setup.

The API suite drives endpoints through TestClient without a session cookie.
The auth middleware lets unauthenticated local requests through only while no
panel password is set, so the moment a developer sets one in the UI (it lands in
data/user_data/auth.json) every API test starts returning 401. Whether the
machine running the tests happens to have a password configured must not decide
whether the suite passes, so neutralise both conditions here.

Tests that exercise the auth logic itself opt out with @pytest.mark.real_auth.

Every session runs against a throwaway data directory and a synthetic security
master, so no test reads or writes the project's data/ directory. Sockets that
leave the machine and non-loopback DNS lookups are refused unless a test is
marked integration; those are deselected by default (see pyproject.toml).
"""
from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

_session_root: Path | None = None


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_auth: keep the real auth state instead of the unauthenticated-local stub"
    )
    config.addinivalue_line(
        "markers",
        "real_benchmark_refresh: keep the real benchmark index refresh instead of the offline stub",
    )
    _isolate_data_dir()
    _install_network_guard()


def pytest_unconfigure(config):
    if _session_root is None:
        return
    import logging

    # app.main attaches a file handler for backend.log under the session data dir.
    for handler in list(logging.getLogger().handlers):
        filename = getattr(handler, "baseFilename", None)
        if filename and Path(filename).is_relative_to(_session_root):
            logging.getLogger().removeHandler(handler)
            handler.close()
    shutil.rmtree(_session_root, ignore_errors=True)


# --- Data directory isolation ---------------------------------------------------------------


def _isolate_data_dir() -> None:
    """Point settings at a session temp dir; Settings() reads the environment only once, at import."""
    global _session_root
    if "app.config" in sys.modules:
        raise pytest.UsageError("app.config was imported before tests/conftest.py could isolate DATA_DIR")
    _session_root = Path(tempfile.mkdtemp(prefix="stock-panel-tests-"))
    data_dir = _session_root / "data"
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["CACHE_DIR"] = str(data_dir / "tmp")
    # A developer's .env (passwords, AI keys, paths) must not reach any Settings() built by a test.
    os.environ["TICKFLOW_ENV_FILE"] = str(_session_root / "absent.env")
    # numba's cache=True probes app/*/__pycache__; under a running uvicorn --reload that file
    # activity makes the dev server log to data/backend.log.
    os.environ["NUMBA_CACHE_DIR"] = str(Path(tempfile.gettempdir()) / "stock-panel-tests-numba-cache")
    _write_security_master(data_dir / "taiwan" / "security_master.parquet")


@pytest.fixture(scope="session", autouse=True)
def _dotenv_token_lookup_stays_in_session_dir():
    """Custom source tokens fall back to .env next to data_dir and the cwd; from backend/ that is the repo .env."""
    from app.data_providers.custom import provider

    session_cwd = _session_root / "cwd"

    class _SessionCwdPath(type(Path())):
        @classmethod
        def cwd(cls):
            return cls(session_cwd)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(provider, "Path", _SessionCwdPath)
        yield


def _instrument(symbol: str, name: str, instrument_type: str = "stock", **fields):
    from app.taiwan.universe.models import TaiwanInstrument

    code, exchange = symbol.split(".")
    values = {
        "symbol": symbol,
        "code": code,
        "exchange": exchange,
        "name": name,
        "instrument_type": instrument_type,
        "listing_status": "active",
        "listing_date": "2000-01-04",
        "isin": None,
        "industry": None,
        "cfi_code": None,
        "raw_category": "ETF" if instrument_type == "etf" else "股票",
        "is_supported": True,
        "source": "TWSE_ISIN" if exchange == "TWSE" else "TPEX_ISIN",
        "updated_at": "2026-08-28T00:00:00+08:00",
        **fields,
    }
    return TaiwanInstrument(**values)


def _etf(symbol: str, name: str, category: str, scope: str, **fields):
    return _instrument(
        symbol,
        name,
        "etf",
        etf_category=category,
        classification_source="official_metadata",
        underlying_scope=scope,
        etf_management_style="passive",
        **fields,
    )


def _synthetic_instruments() -> list:
    return [
        _instrument("2330.TWSE", "台積電", industry="半導體業", listing_date="1994-09-05"),
        _instrument("2303.TWSE", "聯電", industry="半導體業", listing_date="1985-07-16"),
        _instrument("2454.TWSE", "聯發科", industry="半導體業", listing_date="2001-07-23"),
        _instrument("2379.TWSE", "瑞昱", industry="半導體業", listing_date="1998-10-26"),
        _instrument("2317.TWSE", "鴻海", industry="其他電子業", listing_date="1991-06-18"),
        _instrument("1101.TWSE", "台泥", industry="水泥工業", listing_date="1962-02-09"),
        _instrument("1102.TWSE", "亞泥", industry="水泥工業", listing_date="1962-06-08"),
        _instrument("2881.TWSE", "富邦金", industry="金融保險業", listing_date="2001-12-19"),
        _instrument("2603.TWSE", "長榮", industry="航運業", listing_date="1987-09-21"),
        _instrument("2327.TWSE", "國巨", industry="電子零組件業", listing_date="1993-10-08"),
        _instrument("6196.TWSE", "帆宣", industry="其他電子業", listing_date="2003-08-18"),
        _instrument("6488.TPEX", "環球晶", industry="半導體業", listing_date="2015-09-25"),
        _instrument("5274.TPEX", "信驊", industry="半導體業", listing_date="2013-12-03"),
        _instrument("8069.TPEX", "元太", industry="光電業", listing_date="2004-03-29"),
        _etf("0050.TWSE", "元大台灣50", "domestic_equity", "domestic", listing_date="2003-06-30"),
        _etf("006208.TWSE", "富邦台50", "domestic_equity", "domestic", listing_date="2012-07-17"),
        _etf("00878.TWSE", "國泰永續高股息", "domestic_equity", "domestic", listing_date="2020-07-20"),
        _etf("00646.TWSE", "元大S&P500", "foreign_equity", "foreign", listing_date="2015-12-14"),
        _etf(
            "00631L.TWSE", "元大台灣50正2", "leveraged", "domestic",
            leverage_multiplier=2.0, listing_date="2014-10-31",
        ),
        _etf(
            "00632R.TWSE", "元大台灣50反1", "inverse", "domestic",
            leverage_multiplier=-1.0, listing_date="2014-10-31",
        ),
        _etf("00720B.TPEX", "元大投資級公司債", "bond", "foreign", listing_date="2018-01-19"),
        _etf("00885.TWSE", "富邦越南", "foreign_equity", "foreign", listing_date="2021-02-02"),
        _etf("00893.TWSE", "國泰智能電動車", "foreign_equity", "foreign", listing_date="2021-06-10"),
        # A name-only classification is unconfirmed, so its limit rule must stay unknown.
        _instrument(
            "00980T.TPEX", "主動野村臺灣優選", "etf", etf_category="unknown",
            classification_source="name_heuristic", underlying_scope="unknown",
            etf_management_style="active", listing_date="2025-05-07",
        ),
    ]


def _write_security_master(path: Path) -> None:
    from app.taiwan.universe.service import _instruments_frame

    path.parent.mkdir(parents=True, exist_ok=True)
    _instruments_frame(_synthetic_instruments()).write_parquet(path)


SYNTHETIC_LAST_SESSION = date(2026, 8, 28)


@pytest.fixture
def synthetic_taiwan_daily(tmp_path, monkeypatch):
    """Point settings.data_dir at tmp_path with 45 sessions of bars, flows and margin up to SYNTHETIC_LAST_SESSION."""
    import polars as pl

    from app.config import settings
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.institutional_store import TaiwanInstitutionalStore
    from app.taiwan.margin_store import TaiwanMarginStore

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    sessions: list[date] = []
    day = SYNTHETIC_LAST_SESSION
    while len(sessions) < 45:
        if day.weekday() < 5:
            sessions.append(day)
        day -= timedelta(days=1)
    sessions.reverse()
    bars, flows, margins = [], [], []
    for i, inst in enumerate(_synthetic_instruments()):
        base = 50.0 + 25.0 * i
        for j, session in enumerate(sessions):
            close = round(base * (1 + 0.004 * j + 0.01 * ((i + j) % 3 - 1)), 2)
            bars.append({
                "symbol": inst.symbol, "date": session, "open": close, "high": round(close * 1.01, 2),
                "low": round(close * 0.99, 2), "close": close, "volume": 1_000_000.0 + 1000.0 * j,
                "amount": close * (1_000_000.0 + 1000.0 * j), "quote_ts": None,
            })
            net = 1000 * ((i + j) % 5 - 2)
            flows.append({
                "symbol": inst.symbol, "date": session, "trade_date": session, "foreign_net": net,
                "investment_trust_net": 100, "dealer_net": -50, "official_net": net + 50,
                "computed_net": net + 50, "status": "official", "source": "synthetic",
            })
            margins.append({
                "symbol": inst.symbol, "date": session, "trade_date": session,
                "margin_balance": 100_000 + 100 * j, "margin_change": 100, "short_balance": 5000,
                "short_change": 0, "short_margin_ratio": 5.0, "unit": "shares", "status": "official",
                "source": "synthetic",
            })
    frame = pl.DataFrame(bars)
    TaiwanDailyStore().write_batch(frame)
    TaiwanInstitutionalStore().write_batch(pl.DataFrame(flows))
    TaiwanMarginStore().write_batch(pl.DataFrame(margins))
    return frame


@pytest.fixture
def offline_detail_sources(monkeypatch):
    """Stock detail services built in the test read no live quote, bar, flow, margin or index source."""
    from unittest.mock import MagicMock

    import polars as pl

    from app.taiwan import detail_service

    def offline(*args, **kwargs):
        raise RuntimeError("offline")

    offline_index = MagicMock(**{"read_range.return_value": pl.DataFrame()})
    offline_realtime = MagicMock(**{"get_quotes.return_value": {}})
    monkeypatch.setattr(detail_service, "get_index_daily_service", lambda: offline_index)
    monkeypatch.setattr(detail_service, "get_realtime_service", lambda: offline_realtime)
    monkeypatch.setattr(detail_service.TaiwanHybridProvider, "get_daily", lambda self, *a, **kw: pl.DataFrame())
    monkeypatch.setattr(detail_service.TaiwanInstitutionalProvider, "fetch_live_day", offline)
    monkeypatch.setattr(detail_service.TaiwanMarginProvider, "fetch_live_day", offline)


def _refuse_master_refresh(*args, **kwargs):
    raise RuntimeError("tests must not refresh the shared security master")


@pytest.fixture(autouse=True)
def _security_master_is_isolated(request, monkeypatch, tmp_path_factory):
    """Serve the synthetic master; integration tests get an empty cache that downloads the real one."""
    from app.taiwan import universe
    from app.taiwan.data_root import taiwan_data_root

    if request.node.get_closest_marker("integration"):
        cache = tmp_path_factory.mktemp("live-master") / "security_master.parquet"
        monkeypatch.setattr(universe, "_default_master", universe.TaiwanSecurityMaster(cache_path=cache))
        return

    master = universe.TaiwanSecurityMaster(cache_path=taiwan_data_root() / "security_master.parquet")
    assert master.load_cache(), "the synthetic security master was not written"
    monkeypatch.setattr(master, "refresh", _refuse_master_refresh)
    monkeypatch.setattr(universe, "_default_master", master)


@pytest.fixture(autouse=True)
def _data_bound_singletons_are_fresh(monkeypatch):
    """Services built by an earlier test keep that test's stores; each test builds its own."""
    from app.taiwan import chip_history, detail_service, index_daily

    monkeypatch.setattr(index_daily, "_service", None)
    monkeypatch.setattr(chip_history, "_service", None)
    monkeypatch.setattr(detail_service, "_detail_service", None)


# --- Network isolation ----------------------------------------------------------------------

_network_allowed = False
_blocked_hosts: Counter[str] = Counter()
_blocked_tests: set[str] = set()
_current_test = "<collection>"


def _is_local_host(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).strip("[]").split("%", 1)[0]
    if host in ("", "localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _refuse(host, error: type[OSError] = OSError):
    if _network_allowed or _is_local_host(host):
        return
    _blocked_hosts[str(host)] += 1
    _blocked_tests.add(_current_test)
    raise error(f"network access denied in tests: {host}")


def _host_of(address) -> object:
    return address[0] if isinstance(address, tuple) else address


def _install_network_guard() -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname = socket.gethostbyname
    real_gethostbyname_ex = socket.gethostbyname_ex

    def guarded_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            _refuse(_host_of(address))
        return real_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            _refuse(_host_of(address))
        return real_connect_ex(sock, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        _refuse(host, socket.gaierror)
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_gethostbyname(host):
        _refuse(host, socket.gaierror)
        return real_gethostbyname(host)

    def guarded_gethostbyname_ex(host):
        _refuse(host, socket.gaierror)
        return real_gethostbyname_ex(host)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_getaddrinfo
    socket.gethostbyname = guarded_gethostbyname
    socket.gethostbyname_ex = guarded_gethostbyname_ex

    if sys.platform == "win32":
        from asyncio import windows_events

        # The Proactor loop connects through ConnectEx, never through socket.connect.
        real_proactor_connect = windows_events.IocpProactor.connect

        def guarded_proactor_connect(proactor, conn, address):
            if conn.family in (socket.AF_INET, socket.AF_INET6):
                _refuse(_host_of(address))
            return real_proactor_connect(proactor, conn, address)

        windows_events.IocpProactor.connect = guarded_proactor_connect


@pytest.fixture(autouse=True)
def _network_guard(request):
    global _network_allowed, _current_test
    _current_test = request.node.nodeid
    _network_allowed = request.node.get_closest_marker("integration") is not None
    yield
    _network_allowed = False


@pytest.fixture
def deny_network(_network_guard):
    """Refuse non-loopback connections and DNS lookups even inside an integration test."""
    global _network_allowed
    _network_allowed = False


def pytest_terminal_summary(terminalreporter):
    if not _blocked_hosts:
        terminalreporter.write_line("network guard: no outbound connection or DNS lookup was attempted")
        return
    hosts = ", ".join(f"{host} x{count}" for host, count in _blocked_hosts.most_common(10))
    terminalreporter.write_line(
        f"network guard: refused {sum(_blocked_hosts.values())} attempts from "
        f"{len(_blocked_tests)} tests ({hosts})"
    )
    for nodeid in sorted(_blocked_tests):
        terminalreporter.write_line(f"  {nodeid}")


@pytest.fixture(autouse=True)
def _benchmark_refresh_is_offline(request, monkeypatch):
    """The scheduled and manual updates refetch TAIEX first; no test may reach the exchange."""
    if request.node.get_closest_marker("real_benchmark_refresh"):
        return

    from app.jobs import daily_pipeline

    monkeypatch.setattr(daily_pipeline, "refresh_benchmark_index", lambda _label: None)


@pytest.fixture(scope="session")
def _empty_holiday_root(tmp_path_factory):
    return tmp_path_factory.mktemp("holidays")


@pytest.fixture(autouse=True)
def _holiday_schedule_is_isolated(_empty_holiday_root, monkeypatch):
    """The shared holiday cache under the real data dir would turn fixture dates into holidays."""
    from app.taiwan.realtime import holidays

    def _no_fetch(year: int) -> object:
        raise RuntimeError(f"tests must not fetch the TWSE holiday schedule ({year})")

    store = holidays.TaiwanHolidayStore(root=_empty_holiday_root, fetcher=_no_fetch)
    monkeypatch.setattr(holidays, "_default_store", store)


@pytest.fixture(autouse=True)
def _api_tests_are_unauthenticated_local(request, monkeypatch):
    if request.node.get_closest_marker("real_auth"):
        return

    from app.api import auth as auth_api
    from app.services import auth as auth_service

    monkeypatch.setattr(auth_service, "is_configured", lambda: False)
    monkeypatch.setattr(auth_api, "_is_local_request", lambda _request: True)
