"""services.discovery — 已注册用户发现机制（总管只读模式评估面 b，AMan 拍板②按推荐）。

口径来源：TL 评估报告 01a14009 评估面 b（落码单 01a14409）：
- 判定锚=personal/ledger.duckdb 文件存在性——严禁枚举 users/ 一级目录当用户清单
  （该目录混杂平台工作区内容：agents/.lark-cli/shared 等，评估期实锚在案）。
- uid 反解=目录名 + 白名单 ^[A-Za-z0-9_-]+$（与 parse_credential 同一 pattern
  单点复用 USER_ID_PATTERN）。
- 只读零副作用：不 mkdir、不建库；空集=返回空列表（非报错，调用方空聚合）。
- 展示口径=uid 直显（库内无 username 表，隐私面 uid 本就构成库路径对外可见，
  直显无增量泄漏）。
"""

from __future__ import annotations

from pathlib import Path

from ..db import USER_ID_PATTERN


def users_root() -> Path:
    """用户库根目录（调用时计算，跟随 HOME env——测试重定向同构 db_path 口径）。"""
    return Path.home() / ".ai_orz" / "data" / "users"


def discover_user_ids(root: Path | None = None) -> list[str]:
    """发现全部已注册用户（判定锚=personal/ledger.duckdb 文件存在性）。

    返回按 uid 字典序稳定排序的列表；空集=空列表非报错（评估面 b 容错口径）。
    root 参数仅供测试注入临时根；生产路径恒经 users_root()。
    """
    base = root if root is not None else users_root()
    found: set[str] = set()
    for marker in base.glob("*/personal/ledger.duckdb"):
        uid = marker.parent.parent.name
        if USER_ID_PATTERN.fullmatch(uid):
            found.add(uid)
    return sorted(found)
