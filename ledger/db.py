"""数据层：短连接（打开→执行→关闭）+ open_with_retry 撞锁重试 + 首启建库。

口径来源：架构设计方案 v1.0 §2.2（关键设计）+ 总管只读模式评估报告 01a14009（评估面 a/b/e，
AMan 拍板①~⑤按推荐，落码单 01a14409）：
- 短连接原则：两个入口都不持有常驻 DB 连接；写锁窗口=单次操作毫秒级。
- 撞锁重试：open_with_retry 重试 5 次、退避 0.2s 递增（总窗口约 3s），
  远大于对端写锁窗口；重试耗尽返回明确业务错误（db_busy）。
- 数据文件：每用户独立库 ~/.ai_orz/data/users/{uid}/personal/ledger.duckdb
  （env LEDGER_DB_PATH 显式配置优先——仅 uid=None 缓存路径语义下生效，见 db_path；
  uid 取自结构化 base64 凭证 env LEDGER_CREDENTIAL，MCP 入口 fail-fast 校验；
  无旧路径回退分支——多用户拍板①，AMan 01a13e2c）；单文件 + WAL；
  隐私红线：原始明细只落本地文件，不入平台库不入蜂巢图谱。
- 首启建库：_ensure_schema 执行仓库根 schema.sql（IF NOT EXISTS 幂等）。
  方案 B 存在性检查口径（评估面 e 落码裁定）：每次连接以 duckdb_tables() 检查
  expected 表集，缺失才执行 schema.sql——无进程级 Once 状态，同进程多库天然适配，
  并发由 threading.Lock 串行化（FastAPI sync 路由跑 threadpool）。
- 总管只读模式（评估面 a/e，AMan 拍板①按推荐）：serve 入口 LEDGER_CREDENTIAL 未设=
  总管模式——读路径经 read_only_scope() 走 read_only 连接（不 mkdir、不建库、
  不触发 schema，零副作用；撞锁复用同一重试链→db_busy 503），写路径经请求级凭证
  解析出显式 uid 走读写连接；MCP 入口 fail-fast 零豁免不变。
"""

from __future__ import annotations

import base64
import binascii
import contextvars
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from .errors import LedgerError

DB_LOCK_ATTEMPTS = 5
DB_LOCK_BACKOFF_SECONDS = 0.2

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

_schema_lock = threading.Lock()

# ── 多用户凭证（结构化 base64，AMan 拍板②，01a13e2c）────────────────────────
# 编码链：JSON（UTF-8 紧凑）→ 标准 base64（RFC 4648）→ env LEDGER_CREDENTIAL 单行字符串。
# 不做旧格式嗅探兼容（拍板②-2）：旧格式由下方校验链结构性拒绝。
CREDENTIAL_VERSION = 1
USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class CredentialError(Exception):
    """凭证解析失败（fail-fast 语义：入口应立即退出，不降级、不回落默认用户）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_credential(raw: str | None) -> str:
    """解析 LEDGER_CREDENTIAL 原始值，返回 user_id。

    六步校验链（任一步失败抛 CredentialError，不猜不降级，拍板②-3）：
    env 非空 → base64 严格解码（validate=True，字符集外即拒）→ UTF-8 → JSON 对象
    → v==1（缺失/非 int/未知版本拒绝，为演进预留单调递增语义）
    → user_id 必填非空且过白名单 ^[A-Za-z0-9_-]+$
      （uid 用作路径组件，白名单结构性排除 ../、/、. 等路径注入与穿越面）。
    旧格式裸 uid 串：或字符集/padding 不过②，或解码产物非合法 UTF-8 JSON③④——
    校验链结构性拒绝，无需专门嗅探分支（拍板②-2）。
    username 可选本期零消费；未知字段一律忽略（预留拓展位，向前兼容）。
    总管模式复用：REST 写凭证头 X-Ledger-Credential 走同一函数同一错误码族
    （评估面 a：不做双嗅探不受影响，确认维持）。
    """
    if not raw:
        raise CredentialError("env_missing", "环境变量 LEDGER_CREDENTIAL 未设置或为空")
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CredentialError(
            "base64_invalid", f"LEDGER_CREDENTIAL 不是合法 base64：{exc}"
        ) from exc
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialError("json_invalid", f"凭证解码产物非合法 UTF-8：{exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CredentialError("json_invalid", f"凭证解码产物非合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise CredentialError("json_invalid", "凭证 JSON 顶层须为对象")
    version = payload.get("v")
    # bool 是 int 子类：true/false 语义非版本号，显式排除
    if isinstance(version, bool) or not isinstance(version, int) or version != CREDENTIAL_VERSION:
        raise CredentialError(
            "version_unknown",
            f"凭证版本不支持（要求 v={CREDENTIAL_VERSION}，实际 {version!r}）",
        )
    user_id = payload.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise CredentialError("user_id_missing", "凭证缺少 user_id（必填非空字符串）")
    if not USER_ID_PATTERN.fullmatch(user_id):
        raise CredentialError(
            "user_id_invalid", "user_id 含非法字符（白名单 ^[A-Za-z0-9_-]+$）"
        )
    return user_id


_resolved_user_id: str | None = None


def bootstrap_credential() -> str:
    """启动装配：读 env LEDGER_CREDENTIAL → parse_credential → 存模块级缓存供 db_path() 消费。

    MCP stdio 入口在 main() 最前调用，零豁免（拍板②-3）；serve 入口仅在
    LEDGER_CREDENTIAL 已设（单用户模式）时调用——未设=总管只读模式放行（评估面 a）。
    """
    global _resolved_user_id
    user_id = parse_credential(os.environ.get("LEDGER_CREDENTIAL"))
    _resolved_user_id = user_id
    return user_id


def single_user_mode() -> bool:
    """模式判定单点（评估面 a，AMan 拍板①按推荐）：LEDGER_CREDENTIAL 已设=单用户模式。

    仅表达 env 存在性：serve 入口已在启动时对已设凭证 fail-fast，故运行中
    本函数为 True 即单用户模式、False 即总管只读模式。MCP 入口恒为 True。
    """
    return bool(os.environ.get("LEDGER_CREDENTIAL"))


def current_user_id() -> str | None:
    """当前装配用户（单用户模式=启动凭证 uid；总管模式=None，读聚合/写走请求级凭证）。"""
    return _resolved_user_id


def db_path(uid: str | None = None) -> Path:
    """数据文件路径（两级规则，无旧路径回退分支——多用户拍板①）。

    uid=None（缓存路径语义，现行为逐字节不变）：
      1. env LEDGER_DB_PATH 显式配置优先（维持既有行为）；
      2. 无显式配置 → 按启动装配缓存 uid 拼 ~/.ai_orz/data/users/{uid}/personal/ledger.duckdb；
      缓存未装配（未经入口 bootstrap_credential() 直接调 db 层）→ RuntimeError
      （编程错误口径，非运行时降级）。

    uid=显式（总管模式读/写路径，评估面 e）：按 uid 拼同规则库文件；
      LEDGER_DB_PATH 不参与（防多用户场景被 env 引到同一文件破坏隔离）；
      uid 过白名单纵深校验（与凭证链同一 pattern 单点复用）。
    """
    env = os.environ.get("LEDGER_DB_PATH")
    if uid is None:
        if env:
            return Path(env)
        if _resolved_user_id is None:
            raise RuntimeError(
                "用户凭证未装配：请经入口 main() 调用 bootstrap_credential()，"
                "或显式配置 LEDGER_DB_PATH"
            )
        return Path.home() / ".ai_orz" / "data" / "users" / _resolved_user_id / "personal" / "ledger.duckdb"
    if not USER_ID_PATTERN.fullmatch(uid):
        raise LedgerError(
            "invalid_uid", f"uid 含非法字符（白名单 ^[A-Za-z0-9_-]+$）：{uid!r}"
        )
    return Path.home() / ".ai_orz" / "data" / "users" / uid / "personal" / "ledger.duckdb"


# 总管读路径作用域（评估面 e）：REST 读编排专用；服务层零感知（connect 内部判定），
# MCP 面与写路径不进入本作用域（读写语义由调用面单一决定，杜绝隐式读写漂移）。
_read_only_scope: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ledger_read_only", default=False
)


@contextmanager
def read_only_scope() -> Iterator[None]:
    """作用域内 connect() 走 read_only=True（不 mkdir、不建库、不触发 schema）。

    FastAPI sync 端点跑线程池，编排（置位→调服务）发生在同一线程栈内，
    ContextVar 读写无跨线程传播问题。
    """
    token = _read_only_scope.set(True)
    try:
        yield
    finally:
        _read_only_scope.reset(token)


def _connect_raw(uid: str | None = None, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """原始连接（含撞锁重试）。DuckDB 单文件读写模式打开即持 OS 级排他锁。

    read_only=True（总管读路径）：只读共享锁打开，与写侧共存靠重试链兜底
    （读写锁冲突→IOException 含 lock→退避重试→耗尽 db_busy 503）；不 mkdir、
    文件不存在原样抛出（发现机制已保证存在性，TOCTOU 窗口由调用方容错）。
    """
    path = db_path(uid)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(1, DB_LOCK_ATTEMPTS + 1):
        try:
            if read_only:
                return duckdb.connect(str(path), read_only=True)
            return duckdb.connect(str(path))
        except duckdb.IOException as exc:  # 撞锁：明确报错而非数据损坏（方案 §1 硬约束）
            msg = str(exc)
            if "lock" not in msg.lower():
                raise
            last_err = exc
            time.sleep(DB_LOCK_BACKOFF_SECONDS * attempt)
    raise LedgerError(
        "db_busy",
        "数据文件忙，请稍候重试（另一入口正在写入）",
        http_status=503,
    ) from last_err


_EXPECTED_TABLES = {"categories", "accounts", "transactions", "tags", "transaction_tags", "budgets"}


def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """首启建库（方案 B 存在性检查口径，评估面 e 落码裁定）：每次连接检查
    expected-tables，缺失才执行仓库根 schema.sql（全部 IF NOT EXISTS / ON CONFLICT 幂等）。

    无进程级 Once 状态（原 _schema_ready 全局标志废止）：同进程多库天然适配
    （每库各自判定），并发重复执行由 _schema_lock 串行化且结果幂等；
    duckdb_tables() 查询微秒级，代价可忽略。
    """
    with _schema_lock:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name='main'"
            ).fetchall()
        }
        if _EXPECTED_TABLES.issubset(existing):
            return
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        # 逐语句执行：剥除 -- 注释行后按 ';' 切分（seed 的 INSERT 含分号安全——无存储过程）
        statements = [
            s.strip()
            for s in "\n".join(
                line for line in sql.splitlines() if not line.strip().startswith("--")
            ).split(";")
            if s.strip()
        ]
        for stmt in statements:
            conn.execute(stmt)


@contextmanager
def connect(
    uid: str | None = None, *, read_only: bool | None = None
) -> Iterator[duckdb.DuckDBPyConnection]:
    """短连接 contextmanager：打开→（首启建库，仅读写态）→交业务执行→关闭。

    用法：`with connect() as conn: conn.execute(...)`——业务层统一入口，
    禁止任何模块绕开本函数自持常驻连接（方案 §2.2 否决项①）。
    uid：None=缓存路径语义（现行为）；显式=总管模式按库定位（评估面 e）。
    read_only：None=由 read_only_scope() 作用域判定（REST 读编排专用）；
    显式 True/False=调用方直接裁定（MCP/写路径不进入作用域，恒 False）。
    """
    if read_only is None:
        read_only = _read_only_scope.get()
    conn = _connect_raw(uid, read_only=read_only)
    try:
        if not read_only:
            _ensure_schema(conn)
        yield conn
    finally:
        conn.close()
