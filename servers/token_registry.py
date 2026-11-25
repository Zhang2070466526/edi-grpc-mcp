"""文件访问 token — token 就是 urlencode 的绝对路径（无状态、不过期、不签名）。

图片（image_display.py）与文档（document.py）复用这个 TokenStore，通过
/images/{token} 或 /documents/{token} 返回本地文件。token 由路径确定性生成，
路径不变就永久有效，文件移动/改名后自然失效。不做鉴权（内网，怎么简单怎么来）。
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from servers.utils import get_server_base_url


class TokenStore:
    """路径 → 永久访问 token 的确定性映射。"""

    def __init__(self, ttl: int = 600, route: str = "/files") -> None:
        """ttl 参数保留仅为兼容旧调用方，实际不参与（永久有效）。"""
        self._route = route

    def register(self, path: str, ttl: int | None = None, **extra: Any) -> tuple[str, str]:
        """注册文件，返回 (token, url)。ttl / extra 忽略（永久、无状态）。"""
        token = urllib.parse.quote(str(path), safe="")
        return token, f"{get_server_base_url()}{self._route}/{token}"

    def lookup(self, token: str) -> dict[str, Any] | None:
        """按 token 解回 {"path": ...}；解不出返回 None。"""
        try:
            path = urllib.parse.unquote(token)
        except Exception:
            return None
        return {"path": path} if path else None
