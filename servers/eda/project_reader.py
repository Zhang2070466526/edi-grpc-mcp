"""EDA 工程文件解析 — ProjectReader + S-expression 解析器。

核心组件：
  parse_sexp() — 递归下降 S-expression 解析器（处理 EDI 的 Lisp 风格原理图文件）
  ProjectReader — 读取 .epp 工程目录（metadata、原理图列表、网表）
  parse_paramsinfo() — 统一解析元件参数 JSON（兼容 Var 变量和普通参数）
  parse_components() — 从原理图 S-expression 提取所有元件
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ProjectReader:
    """读取 EDA 工作空间中的工程文件。"""

    def __init__(self, project_path: str) -> None:
        """校验并记录工程文件路径，workspace 为工程所在目录。"""
        self.epp_path = Path(project_path).expanduser()
        if not self.epp_path.is_file():
            raise FileNotFoundError(f"工程文件不存在: {self.epp_path}")
        if self.epp_path.suffix.lower() != ".epp":
            raise ValueError("project_path 必须指向 .epp 工程文件")
        self.workspace = self.epp_path.parent

    def read_text(self, relative_path: str) -> str | None:
        """读取工程目录下的文件内容，不存在返回 None。"""
        p = self.workspace / relative_path
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")

    def read_metadata(self) -> dict[str, Any]:
        """读取工程元数据（project_id、name、author、version、created）。"""
        raw = self.read_text("project/metadata.ep") or ""
        items = list(parse_sexp(raw))
        if not items:
            return {}
        meta = items[0] if isinstance(items[0], list) else []
        return {
            "project_id": meta[1] if len(meta) > 1 else "",
            "name": _kv(meta, "name"),
            "author": _kv(meta, "author"),
            "version": _kv(meta, "version"),
            "created": _kv(meta, "created"),
        }

    def list_schematics(self) -> list[str]:
        """列出原理图名称（如 'main'），去掉了路径前缀。"""
        raw = self.read_text("schematics/schematics.ep") or ""
        items = list(parse_sexp(raw))
        names: list[str] = []
        schematic_entries = _walk_find(items, "schematic")
        for entry in schematic_entries:
            if len(entry) >= 2:
                path = entry[1].strip('"')
                # "schematics/main/schematic.ep" -> "main"
                name = Path(path).parent.name if "/" in path else path
                names.append(name)
        return names

    def read_schematic(self, name: str = "main") -> str | None:
        """读取指定原理图内容（安全检查：拒绝 .. 和路径分隔符）。"""
        if ".." in name or "/" in name or "\\" in name:
            return None
        return self.read_text(f"schematics/{name}/schematic.ep")


# ---------------------------------------------------------------------------
# S-expression parser
# ---------------------------------------------------------------------------

def parse_sexp(text: str) -> Any:
    """Parse EDA S-expression format, yielding top-level expressions.

    Handles nested parens, quoted strings with escaped quotes,
    and barewords.
    """
    text = text.strip()
    pos = 0

    def peek():
        """返回当前位置的字符，越界返回空串。"""
        return text[pos] if pos < len(text) else ""

    def skip_ws():
        """跳过空白字符。"""
        nonlocal pos
        while pos < len(text) and text[pos] in " \t\n\r":
            pos += 1

    _escape_map = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}

    def read_string():
        """读取引号字符串，处理转义字符。"""
        nonlocal pos
        result = []
        pos += 1  # skip opening "
        while pos < len(text):
            ch = text[pos]
            if ch == "\\" and pos + 1 < len(text):
                next_ch = text[pos + 1]
                result.append(_escape_map.get(next_ch, next_ch))
                pos += 2
            elif ch == '"':
                pos += 1
                return "".join(result)
            else:
                result.append(ch)
                pos += 1
        return "".join(result)

    def read_bare():
        """读取裸词（直到空白或括号）。"""
        nonlocal pos
        start = pos
        while pos < len(text) and text[pos] not in " \t\n\r()":
            pos += 1
        return text[start:pos]

    def parse_one():
        """解析一个 S-expression（列表 / 字符串 / 裸词）。"""
        nonlocal pos
        skip_ws()
        if pos >= len(text):
            return None
        ch = peek()
        if ch == ")":
            pos += 1
            return None  # unexpected close paren, skip
        if ch == "(":
            pos += 1
            items = []
            while True:
                skip_ws()
                if pos >= len(text):
                    break
                if peek() == ")":
                    pos += 1
                    break
                item = parse_one()
                if item is not None:
                    items.append(item)
            return items
        elif ch == '"':
            return read_string()
        else:
            return read_bare()

    while True:
        skip_ws()
        if pos >= len(text):
            break
        result = parse_one()
        if result is not None:
            yield result


def _walk_find(items: list[Any], tag: str) -> list[list[Any]]:
    """Recursively find all sublists starting with tag."""
    results: list[list[Any]] = []
    for item in items:
        if isinstance(item, list) and len(item) >= 1 and item[0] == tag:
            results.append(item)
        elif isinstance(item, list):
            results.extend(_walk_find(item, tag))
    return results


def _kv(items, key):
    """Extract (key "value") from S-expression list, recursive into sublists."""
    for i, item in enumerate(items):
        if isinstance(item, str) and item == key and i + 1 < len(items):
            val = items[i + 1]
            if isinstance(val, str):
                return val.strip('"')
    for item in items:
        if isinstance(item, list):
            result = _kv(item, key)
            if result:
                return result
    return ""


def parse_paramsinfo(raw):
    """解析 component paramsinfo JSON。

    兼容两种结构：
    - 普通参数: {"Value": "...", "CurrentUnit": "...", "Unit": "...", "Tunable": "false"}
    - Var 变量: {"Initial": "29", "Max": "", "Min": "", "Status": "Disable", "Tunable": "false"}
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}

    result = {}
    for key, meta in data.items():
        if key == "BasicParameters" or not isinstance(meta, dict):
            continue
        value = meta.get("Value", "")
        if value == "" and "Initial" in meta:
            value = meta.get("Initial", "")
        result[key] = {
            "value": value,
            "unit": meta.get("CurrentUnit", meta.get("DefaultUnit", "")),
            "default_unit": meta.get("DefaultUnit", ""),
            "tunable": str(meta.get("Tunable", "false")).lower() == "true",
            "visible": str(meta.get("Visible", "true")).lower() != "false",
            "initial": meta.get("Initial", ""),
            "max": meta.get("Max", ""),
            "min": meta.get("Min", ""),
            "status": meta.get("Status", ""),
        }
    return result


def parse_components(schematic_text):
    """Extract all components from a schematic.ep S-expression."""
    items = list(parse_sexp(schematic_text))
    comps = []
    for comp in _walk_find(items, "component"):
        if len(comp) < 3:
            continue

        comp_id = comp[1] if isinstance(comp[1], str) else ""
        comp_type = _kv(comp, "type")
        name = _kv(comp, "name")
        model_id = _kv(comp, "component_uuid")

        pin_count = len(_walk_find(comp, "pin"))

        params_raw = ""
        psi_entries = _walk_find(comp, "paramsinfo")
        if psi_entries and len(psi_entries[0]) >= 2:
            params_raw = psi_entries[0][1]

        params = parse_paramsinfo(params_raw) if params_raw else {}
        param_count = len(params)

        comps.append({
            "component_id": comp_id,
            "name": name,
            "type": comp_type,
            "model_id": model_id,
            "pin_count": pin_count,
            "parameter_count": max(0, param_count),
            "paramsinfo": params,
        })

    return comps
