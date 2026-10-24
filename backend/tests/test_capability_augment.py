"""切换数据源后的能力快照刷新回归测试。

历史背景: 本文件原本覆盖 app.tickflow.policy._augment_custom_sources 的
「数据集 → 能力」增广映射。台股官方模式改版后 (见 policy.detect_capabilities)
所有台股能力无条件开放, 该增广机制连同函式一并移除, 对应的 7 个用例已失去
被测对象; 仅保留下面这条仍然成立的行为。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.tickflow.capabilities import CapabilitySet


def test_update_data_providers_refreshes_capability_snapshot(monkeypatch):
    """切换数据源后 app.state.capabilities 快照应刷新 (读缓存, 无网络)。"""
    from app.api import settings as settings_api

    monkeypatch.setattr("app.services.preferences.save", lambda upd: None)
    sentinel = CapabilitySet()
    monkeypatch.setattr(settings_api, "detect_capabilities", lambda: sentinel)

    mock_request = MagicMock()
    settings_api.update_data_providers(
        MagicMock(model_dump=lambda exclude_none: {"realtime_data_provider": "mock_src"}),
        mock_request,
    )
    assert mock_request.app.state.capabilities is sentinel
