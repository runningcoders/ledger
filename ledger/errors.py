"""ledger 错误契约（骨架无此文件，本单新增落位）。

口径来源：架构设计方案 v1.0 §4「错误契约统一」——业务错误返回结构化消息，
REST 侧经 HTTP 状态码、MCP 侧经 isError 语义，同一 message 文案源（本模块）。
业务规则（R1-R7）的校验失败统一抛本层异常，由两个入口分别翻译。
"""

from __future__ import annotations


class LedgerError(Exception):
    """业务错误基类：code 稳定、message 面向人话（Agent 可转述、UI 可直显）。"""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class NotFoundError(LedgerError):
    """资源不存在（404）。"""

    def __init__(self, code: str, message: str):
        super().__init__(code, message, http_status=404)


class ConflictError(LedgerError):
    """冲突：状态不允许、重名、重复操作等（409）。"""

    def __init__(self, code: str, message: str):
        super().__init__(code, message, http_status=409)
