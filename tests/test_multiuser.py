"""多用户支持测试：凭证解析 / 每用户路径规则 / 入口 fail-fast（脱敏虚构）。

拍板口径：AMan 01a13e2c（9 项全定案）；实现要点：TL 任务书 01a13e47。
隐私红线：全部脱敏虚构 uid（TESTUSER*/LEGACYRAW* 形态），零真实平台 UID。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ledger.db as db
from ledger.db import CredentialError

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _cred(user_id: str = "TESTUSER0000000001A", v: int = 1, **extra) -> str:
    """构造结构化 base64 凭证（与平台注入形态同构：紧凑 JSON → 标准 base64）。"""
    payload: dict = {"v": v, "user_id": user_id, **extra}
    return base64.b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


@pytest.fixture(autouse=True)
def _isolated_credential_state(monkeypatch):
    """每用例隔离：重置凭证缓存、清 LEDGER_CREDENTIAL，杜绝用例间污染与真实 env 泄入。"""
    monkeypatch.setattr(db, "_resolved_user_id", None)
    monkeypatch.delenv("LEDGER_CREDENTIAL", raising=False)
    yield


# ── 凭证六步校验链 ──────────────────────────────────────────────────


def test_parse_credential_valid_returns_uid():
    raw = _cred(user_id="TESTUSER0000000001A", username="tester")
    assert db.parse_credential(raw) == "TESTUSER0000000001A"


def test_parse_credential_missing_env():
    with pytest.raises(CredentialError) as ei:
        db.parse_credential(None)
    assert ei.value.code == "env_missing"
    with pytest.raises(CredentialError) as ei:
        db.parse_credential("")
    assert ei.value.code == "env_missing"


def test_parse_credential_non_base64_charset_rejected():
    with pytest.raises(CredentialError) as ei:
        db.parse_credential("abc*def==")  # `*` 不在 base64 字母表
    assert ei.value.code == "base64_invalid"


def test_parse_credential_valid_base64_but_not_json_rejected():
    raw = base64.b64encode(b"not-json").decode("ascii")
    with pytest.raises(CredentialError) as ei:
        db.parse_credential(raw)
    assert ei.value.code == "json_invalid"


def test_parse_credential_missing_user_id_rejected():
    raw = base64.b64encode(json.dumps({"v": 1}).encode("utf-8")).decode("ascii")
    with pytest.raises(CredentialError) as ei:
        db.parse_credential(raw)
    assert ei.value.code == "user_id_missing"


def test_parse_credential_unknown_version_rejected():
    with pytest.raises(CredentialError) as ei:
        db.parse_credential(_cred(v=2))  # 未知版本：不猜不降级
    assert ei.value.code == "version_unknown"


def test_parse_credential_uid_whitelist_blocks_path_injection():
    for uid in ("../etc/passwd", "a/b", "a b", "."):
        with pytest.raises(CredentialError) as ei:
            db.parse_credential(_cred(user_id=uid))
        assert ei.value.code == "user_id_invalid"


def test_parse_credential_legacy_raw_uid_structurally_rejected():
    """旧格式裸 uid 串 fail-fast（拍板②-2 不识别旧格式，无嗅探分支）。

    旧形态串为 base64 字母表内 16 字符（恰为合法 base64 长度），但解码产物为
    乱码字节 → 非合法 UTF-8 JSON → 校验链③④结构性拦截（与 TL 任务书机理一致）。
    """
    with pytest.raises(CredentialError) as ei:
        db.parse_credential("LEGACYRAWUID0001")
    assert ei.value.code in {"base64_invalid", "json_invalid"}


# ── 路径两级规则 ────────────────────────────────────────────────────


def test_db_path_derived_per_user_with_home_redirect(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DB_PATH", raising=False)
    monkeypatch.setattr(db, "_resolved_user_id", "TESTUSER0000000001A")
    monkeypatch.setenv("HOME", str(tmp_path))  # Path.home() 跟随 HOME env
    expected = (
        tmp_path / ".ai_orz" / "data" / "users"
        / "TESTUSER0000000001A" / "personal" / "ledger.duckdb"
    )
    assert db.db_path() == expected


def test_db_path_explicit_env_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "_resolved_user_id", "TESTUSER0000000001A")
    monkeypatch.setenv("LEDGER_DB_PATH", str(tmp_path / "explicit.duckdb"))
    assert db.db_path() == tmp_path / "explicit.duckdb"


def test_db_path_isolated_between_users(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DB_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    users_root = tmp_path / ".ai_orz" / "data" / "users"
    paths = set()
    for uid in ("TESTUSER0000000001A", "TESTUSER0000000002B"):
        monkeypatch.setattr(db, "_resolved_user_id", uid)
        p = db.db_path()
        assert p.parent.parent.parent == users_root
        paths.add(p)
    assert len(paths) == 2  # 不同 uid → 不同库文件（隔离性）


def test_db_path_without_credential_or_env_is_programming_error(monkeypatch):
    monkeypatch.delenv("LEDGER_DB_PATH", raising=False)
    monkeypatch.setattr(db, "_resolved_user_id", None)
    with pytest.raises(RuntimeError):
        db.db_path()


# ── 入口 fail-fast 集成（subprocess 实证 exit code 与 stderr）────────


def _run_entry(env_extra: dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        # 纵深防御：即使校验意外通过也落临时库，真实库零风险
        "LEDGER_DB_PATH": str(tmp_path / "unused.duckdb"),
        **env_extra,
    }
    return subprocess.run(
        [sys.executable, "-m", "ledger"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=_REPO_ROOT,
    )


def test_main_fail_fast_exit_2_missing_env(tmp_path):
    proc = _run_entry({}, tmp_path)
    assert proc.returncode == 2
    assert "env_missing" in proc.stderr
    assert "base64" in proc.stderr  # stderr 含修复指引构造示例


def test_main_fail_fast_exit_2_legacy_uid(tmp_path):
    proc = _run_entry({"LEDGER_CREDENTIAL": "LEGACYRAWUID0001"}, tmp_path)
    assert proc.returncode == 2
    assert "credential_error" in proc.stderr
    assert "json_invalid" in proc.stderr


def test_main_fail_fast_exit_2_invalid_json(tmp_path):
    bad = base64.b64encode(b"not-json").decode("ascii")
    proc = _run_entry({"LEDGER_CREDENTIAL": bad}, tmp_path)
    assert proc.returncode == 2
    assert "json_invalid" in proc.stderr
