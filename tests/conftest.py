"""测试夹具：临时 DuckDB 文件（脱敏虚构数据），隐私红线——测试永不触碰真实明细。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 保证从仓库根以包形式导入 ledger
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def tmp_ledger_db(tmp_path, monkeypatch):
    """每个用例独立临时库文件；重置建库标记使首启建库按库文件重新触发。"""
    import ledger.db as db

    db._schema_ready = False
    db_path = tmp_path / "ledger_test.duckdb"
    monkeypatch.setenv("LEDGER_DB_PATH", str(db_path))
    yield db_path
    db._schema_ready = False
