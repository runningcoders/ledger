"""services.tags — 标签查询与重命名（创建走交易打标的 get-or-create，输入即建）。"""

from __future__ import annotations

from ..db import connect
from ..errors import ConflictError, NotFoundError
from ..models import TagOut


def list_tags() -> list[TagOut]:
    with connect() as conn:
        rows = conn.execute("SELECT id, name FROM tags ORDER BY name").fetchall()
    return [TagOut(id=r[0], name=r[1]) for r in rows]


def rename_tag(tag_id: str, name: str) -> TagOut:
    with connect() as conn:
        if conn.execute("SELECT 1 FROM tags WHERE id = ?", [tag_id]).fetchone() is None:
            raise NotFoundError("tag_not_found", f"标签不存在：{tag_id}")
        if conn.execute(
            "SELECT 1 FROM tags WHERE name = ? AND id <> ?", [name, tag_id]
        ).fetchone():
            raise ConflictError("tag_name_taken", f"标签名已存在：{name}")
        conn.execute("UPDATE tags SET name = ? WHERE id = ?", [name, tag_id])
        row = conn.execute("SELECT id, name FROM tags WHERE id = ?", [tag_id]).fetchone()
    return TagOut(id=row[0], name=row[1])
