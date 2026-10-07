"""Unit tests for Taiwan Security Master Service & Universes.

Covers:
  - TWSE + TPEx merging and duplicate handling
  - Parquet cache persistence and loading
  - Universe generation (TAIWAN_ALL, TWSE_ALL, TPEX_ALL, STOCKS, ETFS)
  - Multi-attribute search (code, canonical symbol, Traditional Chinese name)
  - Market Profile Metadata Bridge
  - Removal of Phase 2 hardcoded bootstrap list
  - Golden symbol availability (2330.TWSE, 0050.TWSE, 8069.TPEX)
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import polars as pl
import pytest

from app.backtest.engine import MatcherConfig
from app.backtest.strategy import _configure_taiwan_matcher
from app.data_providers.registry import get_provider
from app.taiwan.market_rules import PriceLimitClass, TaxClass, TickSizeClass
from app.taiwan.universe import (
    MarketProfileBridge,
    TaiwanSecurityMaster,
    UniverseType,
)
from app.taiwan.universe.adapters import (
    OfficialEtfProductMeta,
    TpexInstrumentAdapter,
    TwseInstrumentAdapter,
)
from tests.test_taiwan_instruments import TPEX_SAMPLE_HTML, TWSE_SAMPLE_HTML

_TWSE_SAMPLE_PRODUCT_TYPES = {
    "0050": OfficialEtfProductMeta("國內成分證券指數股票型基金", "臺灣50指數"),
    "006208": OfficialEtfProductMeta("國內成分證券指數股票型基金", "臺灣50指數"),
}


class _MockTwseAdapter(TwseInstrumentAdapter):
    def get_instruments(self, html_content: str | None = None):
        return super().get_instruments(
            html_content or TWSE_SAMPLE_HTML,
            official_product_types=_TWSE_SAMPLE_PRODUCT_TYPES,
        )


class _MockTpexAdapter(TpexInstrumentAdapter):
    def get_instruments(self, html_content: str | None = None):
        return super().get_instruments(html_content or TPEX_SAMPLE_HTML)


@pytest.fixture
def offline_master(tmp_path: Path) -> TaiwanSecurityMaster:
    cache_file = tmp_path / "test_sec_master.parquet"
    master = TaiwanSecurityMaster(
        cache_path=cache_file,
        twse_adapter=_MockTwseAdapter(),
        tpex_adapter=_MockTpexAdapter(),
    )
    master.load_from_adapters()
    return master


class TestTaiwanSecurityMasterOffline:
    """Offline unit tests for TaiwanSecurityMaster orchestrator."""

    def test_merge_and_deduplication(self, offline_master: TaiwanSecurityMaster):
        df = offline_master.to_dataframe()
        symbols = df["symbol"].to_list()
        assert len(symbols) == len(set(symbols)), "Canonical symbols must be strictly unique"
        assert "2330.TWSE" in symbols
        assert "8069.TPEX" in symbols
        assert "0050.TWSE" in symbols

    def test_persistence_cache_roundtrip(self, offline_master: TaiwanSecurityMaster, tmp_path: Path):
        save_file = tmp_path / "saved_master.parquet"
        etf = offline_master.get_instrument("0050.TWSE")
        offline_master._instruments["0050.TWSE"] = replace(
            etf,
            etf_management_style="passive",
        )
        offline_master.save_cache(save_file)
        assert save_file.exists()

        new_master = TaiwanSecurityMaster(cache_path=save_file)
        success = new_master.load_cache()
        assert success is True
        assert new_master.get_instrument("2330.TWSE") is not None
        assert new_master.get_instrument("2330.TWSE").name == "台積電"
        assert new_master.get_instrument("0050.TWSE").etf_management_style == "passive"

    def test_legacy_cache_without_management_style_reads_as_unknown(self, tmp_path: Path):
        cache_file = tmp_path / "legacy_master.parquet"
        pl.DataFrame({
            "symbol": ["00720B.TPEX"],
            "code": ["00720B"],
            "exchange": ["TPEX"],
            "name": ["元大投資級公司債"],
            "instrument_type": ["etf"],
            "listing_status": ["active"],
            "industry": [None],
            "is_supported": [True],
            "etf_category": ["bond"],
            "classification_source": ["cfi_code"],
            "underlying_scope": ["foreign"],
            "leverage_multiplier": [1.0],
            "lot_size": [1000],
        }).write_parquet(cache_file)

        master = TaiwanSecurityMaster(cache_path=cache_file)

        assert master.load_cache() is True
        assert master.get_instrument("00720B.TPEX").etf_management_style is None

    @pytest.mark.parametrize(
        ("include_lot_size", "lot_size", "expected_lot_size", "invalid"),
        [
            pytest.param(False, None, 1000, False, id="missing-column"),
            pytest.param(True, None, 1000, False, id="null"),
            pytest.param(True, 0, 0, True, id="zero"),
            pytest.param(True, -100, -100, True, id="negative"),
        ],
    )
    def test_legacy_cache_lot_size_compatibility_and_validation(
        self,
        tmp_path: Path,
        include_lot_size: bool,
        lot_size: int | None,
        expected_lot_size: int,
        invalid: bool,
    ):
        cache_file = tmp_path / "legacy_lot_master.parquet"
        frame = pl.DataFrame({
            "symbol": ["2330.TWSE"],
            "code": ["2330"],
            "exchange": ["TWSE"],
            "name": ["台積電"],
            "instrument_type": ["stock"],
            "listing_status": ["active"],
            "industry": [None],
            "is_supported": [True],
        })
        if include_lot_size:
            frame = frame.with_columns(
                pl.lit(lot_size, dtype=pl.Int64).alias("lot_size")
            )
        frame.write_parquet(cache_file)
        master = TaiwanSecurityMaster(cache_path=cache_file)

        assert master.load_cache() is True
        assert master.get_instrument("2330.TWSE").lot_size == expected_lot_size
        if invalid:
            with pytest.raises(ValueError, match="invalid trading unit"):
                _configure_taiwan_matcher(
                    MatcherConfig(),
                    ["2330.TWSE"],
                    security_master=master,
                )
        else:
            config = MatcherConfig()
            _configure_taiwan_matcher(
                config,
                ["2330.TWSE"],
                security_master=master,
            )
            assert config.lot_size("2330.TWSE") == 1000

    def test_universe_generation(self, offline_master: TaiwanSecurityMaster):
        all_univ = offline_master.get_universe(UniverseType.TAIWAN_ALL)
        twse_univ = offline_master.get_universe(UniverseType.TWSE_ALL)
        tpex_univ = offline_master.get_universe(UniverseType.TPEX_ALL)
        stock_univ = offline_master.get_universe(UniverseType.TAIWAN_STOCKS)
        etf_univ = offline_master.get_universe(UniverseType.TAIWAN_ETFS)

        # Unsupported securities (warrants, pref shares) must be excluded from universes
        assert "052330.TWSE" not in all_univ
        assert "1101B.TWSE" not in all_univ

        # Supported active securities included
        assert "2330.TWSE" in all_univ
        assert "8069.TPEX" in all_univ
        assert "0050.TWSE" in all_univ

        # Market filters
        assert "2330.TWSE" in twse_univ
        assert "8069.TPEX" not in twse_univ
        assert "8069.TPEX" in tpex_univ

        # Category filters
        assert "2330.TWSE" in stock_univ
        assert "0050.TWSE" not in stock_univ
        assert "0050.TWSE" in etf_univ

    def test_multi_attribute_search(self, offline_master: TaiwanSecurityMaster):
        # 1. Search by stock code
        res_2330 = offline_master.search("2330")
        assert len(res_2330) > 0
        assert res_2330[0]["symbol"] == "2330.TWSE"

        # 2. Search by canonical symbol
        res_sym = offline_master.search("8069.TPEX")
        assert len(res_sym) > 0
        assert res_sym[0]["symbol"] == "8069.TPEX"

        # 3. Search by Traditional Chinese name
        res_name = offline_master.search("元太")
        assert len(res_name) > 0
        assert res_name[0]["symbol"] == "8069.TPEX"

        res_etf = offline_master.search("元大台灣50")
        assert len(res_etf) > 0
        assert res_etf[0]["symbol"] == "0050.TWSE"

    def test_market_profile_metadata_bridge(self, offline_master: TaiwanSecurityMaster):
        tsmc = offline_master.get_instrument("2330.TWSE")
        assert tsmc is not None
        assert MarketProfileBridge.get_tax_class(tsmc) == TaxClass.ORDINARY_STOCK
        assert MarketProfileBridge.get_tick_size_class(tsmc) == TickSizeClass.ORDINARY_STOCK
        assert MarketProfileBridge.get_price_limit_class(tsmc) == PriceLimitClass.ORDINARY_TEN_PERCENT

        etf = offline_master.get_instrument("0050.TWSE")
        assert etf is not None
        assert MarketProfileBridge.get_tax_class(etf) == TaxClass.DOMESTIC_ETF
        assert MarketProfileBridge.get_tick_size_class(etf) == TickSizeClass.ETF

    def test_bootstrap_list_removed_from_hybrid_provider(self):
        """Verify TaiwanHybridProvider.get_instruments() queries Security Master."""
        provider = get_provider("taiwan")
        df = provider.get_instruments("stock")
        assert isinstance(df, pl.DataFrame)
        # Output columns must follow standard provider contract
        expected_cols = {"symbol", "name", "code", "exchange", "asset_type", "source", "list_date", "status", "currency", "lot_size"}
        assert set(df.columns) == expected_cols
        # Production master contains far more than the 6 hardcoded bootstrap items
        assert df.height > 6, f"Expected full universe, got {df.height} items"


class TestIndustryReadTimeNormalization:
    """Phase 8C-C Final Completion — existing `security_master.parquet` caches
    written before the industry-code fix may still hold raw numeric codes
    ("01"/"24"/"91"). `load_cache()` must normalize them to readable names at
    read time, without requiring the parquet file itself to be rebuilt.
    """

    def test_cached_numeric_industry_code_reads_back_as_readable_name(self, tmp_path: Path):
        cache_file = tmp_path / "legacy_numeric_codes.parquet"
        # Simulates a pre-fix cached row: industry holds the raw numeric code.
        df = pl.DataFrame({
            "symbol": ["2330.TWSE"],
            "code": ["2330"],
            "exchange": ["TWSE"],
            "name": ["台積電"],
            "instrument_type": ["stock"],
            "listing_status": ["active"],
            "industry": ["24"],
            "is_supported": [True],
        })
        df.write_parquet(cache_file)

        master = TaiwanSecurityMaster(cache_path=cache_file)
        assert master.load_cache() is True
        assert master.get_instrument("2330.TWSE").industry == "半導體業"

    def test_cached_already_readable_industry_is_unchanged(self, tmp_path: Path):
        cache_file = tmp_path / "already_readable.parquet"
        df = pl.DataFrame({
            "symbol": ["8069.TPEX"],
            "code": ["8069"],
            "exchange": ["TPEX"],
            "name": ["元太"],
            "instrument_type": ["stock"],
            "listing_status": ["active"],
            "industry": ["光電業"],
            "is_supported": [True],
        })
        df.write_parquet(cache_file)

        master = TaiwanSecurityMaster(cache_path=cache_file)
        assert master.load_cache() is True
        assert master.get_instrument("8069.TPEX").industry == "光電業"
