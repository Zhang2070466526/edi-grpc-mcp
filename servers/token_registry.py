"""临时文件访问 Token 存储 — 供图片 / 文档等本地文件的临时 HTTP 访问复用。

图片（image_display.py）与文档（document.py）原本各有一份「dict + RLock + 过期清理
+ 注册 + 查询」的近重复实现，这里提取为统一的 TokenStore。
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any

from servers.utils import get_server_base_url


class TokenStore:
    """临时文件访问 Token 存储：注册 → 查询（查询时顺带过期清理）。"""

    def __init__(self, ttl: int = 600, route: str = "/files") -> None:
        self._ttl = ttl
        self._route = route
        self._tokens: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def cleanup_expired(self) -> None:
        """清理已过期的 Token。"""
        now = time.time()
        with self._lock:
            for t in [t for t, v in self._tokens.items() if v["expires_at"] < now]:
                del self._tokens[t]

    def register(self, path: str, **extra: Any) -> tuple[str, str]:
        """注册一个 Token，返回 (token, url)。extra 存附加字段（如 disposition）。"""
        self.cleanup_expired()
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._tokens[token] = {
                "path": str(path),
                "expires_at": time.time() + self._ttl,
                **extra,
            }
        return token, f"{get_server_base_url()}{self._route}/{token}"

    def lookup(self, token: str) -> dict[str, Any] | None:
        """查询 Token（带过期清理），不存在/过期返回 None。"""
        self.cleanup_expired()
        with self._lock:
            return self._tokens.get(token)
