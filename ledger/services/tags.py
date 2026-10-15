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
    """重命名标签。

    DuckDB 1.5.6 父表 UPDATE 的 FK 检查对尚存子行敏感（同 update()/void() 先例：
    子表清理必须先于父表 UPDATE；且父行 FK 检查不读同事务内未提交的删除，
    故采逐语句自动提交形态，与既有写路径同构）。先存联接集→清 transaction_tags
    →UPDATE tags→按原联接集重插——被引用标签重命名后关联零丢失
    （transaction_tags 复合 PK 自证零重复）。重名/存在性检查先于任何写路径，
    冲突时原状态零扰动。
    """
    with connect() as conn:
        if conn.execute("SELECT 1 FROM tags WHERE id = ?", [tag_id]).fetchone() is None:
            raise NotFoundError("tag_not_found", f"标签不存在：{tag_id}")
        if conn.execute(
            "SELECT 1 FROM tags WHERE name = ? AND id <> ?", [name, tag_id]
        ).fetchone():
            raise ConflictError("tag_name_taken", f"标签名已存在：{name}")
        saved = [r[0] for r in conn.execute(
            "SELECT transaction_id FROM transaction_tags WHERE tag_id = ?"
            " ORDER BY transaction_id", [tag_id]).fetchall()]
        conn.execute("DELETE FROM transaction_tags WHERE tag_id = ?", [tag_id])
        conn.execute("UPDATE tags SET name = ? WHERE id = ?", [name, tag_id])
        for txn_id in saved:
            conn.execute(
                "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                [txn_id, tag_id],
            )
        row = conn.execute("SELECT id, name FROM tags WHERE id = ?", [tag_id]).fetchone()
    return TagOut(id=row[0], name=row[1])
