"""测试夹具：临时 DuckDB 文件（脱敏虚构数据），隐私红线——测试永不触碰真实明细。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 保证从仓库根以包形式导入 ledger
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def tmp_ledger_db(tmp_path, monkeypatch):
    """每个用例独立临时库文件（LEDGER_DB_PATH 显式配置优先口径）。

    测试基线锚定单用户模式语义（LEDGER_CREDENTIAL 在位）+HOME 重定向双保险：
    - 既有用例按单用户口径断言（现行为逐字节不变）；总管模式用例在用例内
      自行 delenv 切换（见 test_manager_mode）。
    - HOME 重定向保证总管发现机制（users_root=Path.home()）永不触及真实用户库
      ——即使某用例漏设凭证进入总管模式，聚合目标也只可能是空临时根（隐私红线）。
    """
    import ledger.db as db

    monkeypatch.setenv("LEDGER_CREDENTIAL", "TESTCREDENTIAL")
    monkeypatch.setenv("HOME", str(tmp_path))
    db_path = tmp_path / "ledger_test.duckdb"
    monkeypatch.setenv("LEDGER_DB_PATH", str(db_path))
    yield db_path
