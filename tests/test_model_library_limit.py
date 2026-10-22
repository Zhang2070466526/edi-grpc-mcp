"""模型库检索的 `limit` / `truncated` / `count` 契约（UX-3 的锁）。

不依赖 EDI：monkeypatch `call_grpc` 返回固定形状。
覆盖 4 种情况：
  ① 命中数 > limit → 截断 + `returned`/`truncated`/`count` 三者一致
  ② limit=0 → 不截断、不带任何截断标记
  ③ 命中数 <= limit → 不截断
  ④ data 是纯 list（无 count 字段）→ 不报错、不硬塞 count
"""

from __future__ import annotations

import pytest

from servers.eda import model_library as M


def _fake_grpc(count: int, shape: str = "dict"):
    """构造 call_grpc 的返回值：dict 形状带 count，list 形状只有结果。"""
    rows = [{"id": i, "name": f"model{i}"} for i in range(count)]

    def fake_call(*_args, **_kwargs):
        data: object = {"count": count, "results": list(rows)} if shape == "dict" else list(rows)
        return {"success": True, "details": {"code": 200, "message": "ok", "data": data}}

    return fake_call


@pytest.fixture
def patch_grpc(monkeypatch):
    def _apply(count: int, shape: str = "dict"):
        monkeypatch.setattr(M, "call_grpc", _fake_grpc(count, shape))
    return _apply


@pytest.mark.parametrize("limit", [3, 50, 100])
def test_truncates_and_exposes_count(patch_grpc, limit):
    patch_grpc(449)
    det = M.search_public_models(sub_type="62", limit=limit)["details"]

    assert det["truncated"] is True
    assert det["returned"] == limit
    assert det["count"] == 449, "命中总数必须与 returned/truncated 平级暴露"
    assert len(det["data"]["results"]) == limit


def test_limit_zero_returns_everything_without_flags(patch_grpc):
    patch_grpc(449)
    det = M.search_public_models(sub_type="62", limit=0)["details"]

    assert len(det["data"]["results"]) == 449
    assert "truncated" not in det and "returned" not in det and "count" not in det


def test_no_truncation_when_count_below_limit(patch_grpc):
    patch_grpc(7)
    det = M.search_public_models(sub_type="62", limit=50)["details"]

    assert len(det["data"]["results"]) == 7
    assert "truncated" not in det


def test_list_shaped_data_has_no_count_key(patch_grpc):
    """兼容分支：data 为纯 list 时没有 count，不应报错也不应凭空造 count。"""
    patch_grpc(10, shape="list")
    det = M.search_public_models(sub_type="62", limit=4)["details"]

    data = det["data"]
    assert isinstance(data, list) and len(data) == 4
    assert det["truncated"] is True and det["returned"] == 4
    assert "count" not in det


def test_personal_library_shares_the_same_limit_logic(patch_grpc):
    """个人库走同一 `_search_models`，契约必须一致。"""
    patch_grpc(120)
    det = M.search_personal_models(sub_type="61", limit=20)["details"]

    assert det["returned"] == 20 and det["count"] == 120 and det["truncated"] is True
