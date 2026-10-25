"""Backtest package: strategy backtests and strategy mining.

Architecture: shared BacktestEngine -> StrategyBacktestService; mining reuses the factor
catalog and factor computation in factor.py.
"""
