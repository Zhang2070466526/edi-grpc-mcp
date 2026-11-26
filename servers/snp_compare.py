"""Touchstone 读写 + 参考阻抗重归一化。

给 sNp 对比工具（compare_snp）用：HFSS 导出的 .s2p 默认归一化到 50 Ω，
LASPCEM 导出的 `.sNp` 则归一化到工程自己的端口阻抗（实测 CPW_SL：Z0(1:1)=52.66 Ω、
Z0(2:1)=45.05 Ω）。两边直接比 S11 会得到 4–7 dB 的「系统性偏差」，其实是参考口径不同。

`ExportSnpFile` 只接受 File Path / Solution Name / Sweeping Parameters /
Overriding Parameters 四个键（`D:\\LASPCEM_2026\\plugin\\report\\__init__.py` 里没有
任何 impedance/renormal 接口），所以重归一化只能在这里用 Z 参数变换做。

数学（对角参考阻抗，取实部；虚部 < 1e-3 Ω 时忽略）：
    Z  = R0^{1/2} (I + S) (I - S)^{-1} R0^{1/2}
    S' = (Z - R1) (Z + R1)^{-1}
往返可逆，`tests/test_snp_compare.py` 用 round-trip 验证。
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

_FREQ_SCALE = {"HZ": 1e-9, "KHZ": 1e-6, "MHZ": 1e-3, "GHZ": 1.0}


def parse_touchstone(path: str | Path) -> dict:
    """解析 Touchstone 文件。

    Returns:
        {"freqs": ndarray(GHz), "s": complex ndarray (F, N, N), "fmt": "MA"|"RI"|"DB",
         "z0": float, "nports": int, "path": str}
    """
    fmt = "MA"
    scale = 1.0
    z0 = 50.0
    data_tokens: list[float] = []
    first_row_len = 0

    with open(path, "r", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("!"):
                continue
            if s.startswith("#"):
                tok = s[1:].upper().replace(",", " ").split()
                if tok:
                    scale = _FREQ_SCALE.get(tok[0], 1.0)
                if "RI" in tok:
                    fmt = "RI"
                elif "DB" in tok:
                    fmt = "DB"
                elif "MA" in tok:
                    fmt = "MA"
                if "R" in tok:
                    i = tok.index("R")
                    if i + 1 < len(tok):
                        try:
                            z0 = float(tok[i + 1])
                        except ValueError:
                            pass
                continue
            vals = [float(x) for x in s.replace(",", " ").split()]
            if not vals:
                continue
            if first_row_len == 0:
                first_row_len = len(vals)
            data_tokens.extend(vals)

    if not data_tokens:
        raise ValueError(f"没有数据行: {path}")

    # 端口数：首个数据行 = 1 + 2N² 个值（2 端口 → 9）
    nports = 2
    if first_row_len > 1 and (first_row_len - 1) % 2 == 0:
        n2 = (first_row_len - 1) // 2
        n = int(round(n2 ** 0.5))
        if n * n == n2 and n >= 1:
            nports = n
    block = 1 + 2 * nports * nports
    if len(data_tokens) % block != 0:
        raise ValueError(f"数据点不完整（{len(data_tokens)} 个值不能被 {block} 整除）: {path}")

    arr = np.array(data_tokens, dtype=float).reshape(-1, block)
    freqs = arr[:, 0] * scale
    n = nports
    # 自洽校验：文件名后缀写了端口数（.s2p/.s3p…）就必须与列数推出来的一致；
    # `.sNp` 这种通配写法（N 非数字）跳过校验。
    m = re.search(r"\.s(\d+)p$", Path(path).name, re.IGNORECASE)
    if m and int(m.group(1)) != n:
        raise ValueError(
            f"端口数与文件名不符：后缀 .s{m.group(1)}p，但数据列数推出 {n} 端口（首行 "
            f"{first_row_len} 个值）: {path}")
    pairs = arr[:, 1:].reshape(len(arr), n, n, 2)
    a, b = pairs[..., 0], pairs[..., 1]
    if fmt == "MA":
        s = a * np.exp(1j * np.deg2rad(b))
    elif fmt == "DB":
        s = 10 ** (a / 20.0) * np.exp(1j * np.deg2rad(b))
    else:  # RI
        s = a + 1j * b
    return {"freqs": freqs, "s": s, "fmt": fmt, "z0": z0, "nports": nports, "path": str(path)}


def _diag_per_freq(vec: np.ndarray, nfreq: int, n: int) -> np.ndarray:
    """标量或长度 N 的参考阻抗 → (nfreq, N, N) 对角矩阵（只取实部）。"""
    v = np.real(np.asarray(vec, dtype=complex)).ravel()
    if v.size == 1:
        v = np.repeat(v, n)
    if v.size != n:
        raise ValueError(f"参考阻抗维度不匹配：给了 {v.size} 个，端口中 {n} 个")
    mat = np.zeros((nfreq, n, n), dtype=float)
    idx = np.arange(n)
    mat[:, idx, idx] = v
    return mat


def renormalize(s: np.ndarray, z_from, z_to=50.0) -> np.ndarray:
    """把归一化到 z_from 的 S 参数矩阵重归一化到 z_to。

    Args:
        s: (F, N, N) 或 (N, N) 复矩阵。
        z_from: 标量或长度 N（可为复数，取实部；虚部 < 1e-3 时误差可忽略）。
        z_to: 标量或长度 N。
    Returns:
        与输入同形状的重归一化 S。
    """
    s = np.asarray(s, dtype=complex)
    squeeze = s.ndim == 2
    if squeeze:
        s = s[None, ...]
    nfreq, n = s.shape[0], s.shape[1]
    r0 = _diag_per_freq(z_from, nfreq, n)
    r1 = _diag_per_freq(z_to, nfreq, n)
    eye = np.eye(n, dtype=complex)

    sqrt0 = np.sqrt(r0).astype(complex)
    z = sqrt0 @ (eye + s) @ np.linalg.inv(eye - s) @ sqrt0
    # ⚠️ 多端口参考阻抗不同（如 52.66/45.05）时 A^{-1}(·)A 夹层不能省：
    # (z-R1)(z+R1)^{-1} 单独算出来是 A S A^{-1}，不是 S。单端口/等阻抗时两者相同，
    # 正是这个坑会让 round-trip 测试失败。
    sqrt1 = np.sqrt(r1).astype(complex)
    inv_sqrt1 = np.linalg.inv(sqrt1)
    s_new = inv_sqrt1 @ (z - r1) @ np.linalg.inv(z + r1) @ sqrt1
    return s_new[0] if squeeze else s_new


def align_freqs(ref_freq: np.ndarray, curves: list[tuple], tol: float = 1e-9):
    """把多条 (频率轴, 复数值) 曲线对齐到 ref_freq 的公共子网格。

    Args:
        ref_freq: 参考频率轴（GHz）。
        curves: [(freqs, values), ...]；实部/虚部分别线性插值（复数安全）。
    Returns:
        (grid, aligned_values...) —— 频段不重合时返回 None（调用方应报错，不要静默截断）。
    """
    ref = np.asarray(ref_freq, dtype=float).ravel()
    lo, hi = float(ref.min()), float(ref.max())
    for fx, _ in curves:
        fx = np.asarray(fx, dtype=float).ravel()
        lo = max(lo, float(fx.min()))
        hi = min(hi, float(fx.max()))
    if hi <= lo:
        return None
    grid = ref[(ref >= lo - tol) & (ref <= hi + tol)]
    out: list[np.ndarray] = []
    for fx, vals in curves:
        fx = np.asarray(fx, dtype=float).ravel()
        v = np.asarray(vals, dtype=complex)
        if v.shape[0] != fx.size:
            raise ValueError(f"频率轴 {fx.size} 个点，数据却有 {v.shape[0]} 行")
        flat = v.reshape(v.shape[0], -1)
        res = np.empty((grid.size, flat.shape[1]), dtype=complex)
        for k in range(flat.shape[1]):
            col = flat[:, k]
            res[:, k] = np.interp(grid, fx, col.real) + 1j * np.interp(grid, fx, col.imag)
        out.append(res.reshape((grid.size,) + v.shape[1:]))
    return (grid, *out)


def s_to_db(s: np.ndarray, i: int, j: int) -> np.ndarray:
    """S(i,j) → dB（20log10|S|），下限 -300 dB。"""
    mag = np.abs(np.asarray(s, dtype=complex)[:, i, j])
    return 20 * np.log10(np.maximum(mag, 1e-15))


def read_laspcem_z0(csv_path: str | Path) -> list[complex]:
    """从 LASPCEM 的 Design_POST/S_result.csv 读各端口归一化阻抗。

    实测格式：`Freq(GHz),"Func=none(Z0(1:1)) ","Func=none(Z0(2:1)) "`，
    单元格形如 `52.66047195\\0.001172162967`（实部\\虚部，反斜杠分隔）。
    取中频点（最稳）作为端口参考阻抗。
    """
    rows: list[list[complex]] = []
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        next(f, None)  # 表头
        for line in f:
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) < 2:
                continue
            vals = []
            for cell in parts[1:]:
                if not cell:
                    continue
                m = re.match(r"^\s*([-+0-9.eE]+)\s*[\\/]\s*([-+0-9.eE]+)\s*$", cell)
                if m:
                    vals.append(complex(float(m.group(1)), float(m.group(2))))
            if vals:
                rows.append(vals)
    if not rows:
        raise ValueError(f"读不到端口阻抗: {csv_path}")
    mid = rows[len(rows) // 2]
    return mid
