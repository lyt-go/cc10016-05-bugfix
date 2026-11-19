"""pytest 公共夹具：保证每个测试会话都完成解析规则事件记录的预置补录。"""
from __future__ import annotations

import pytest

from app.store import store


@pytest.fixture(scope="session", autouse=True)
def _seed_parse_rule_events() -> None:
    """仓库初始化后补录预置解析规则事件，事件状态机从启动时即完整。"""
    store.seed_parse_rule_events()
