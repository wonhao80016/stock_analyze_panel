"""Backtest package: strategy backtests, optimization, walk-forward and strategy mining.

Architecture: shared BacktestEngine -> StrategyBacktestService; mining reuses the factor
catalog and factor computation in factor.py.
"""
