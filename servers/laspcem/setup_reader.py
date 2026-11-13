"""LASPCEM .sem 工程 setup 参数提取 — 从二进制工程文件读当前求解设置。

.sem 是 LASPCEM 的自定义二进制格式。setup 记录形态（实测）：
    key_len(4) + key + 类型(4) + 0x18(4) + 长度(4) + 值
值类型：0/1/2 = 字符串，3 = 布尔，4 = double。

⚠️ double 记录里**不含 8 字节数值**：key 之后是 `04 00 00 00 | 18 00 00 00 | ff ff ff ff |
ff ff ff 7f | <引用序号:4字节>`，数值本体存在文件别处、无法按记录偏移定位（实测
RangeStart/RangeEnd/RangeStep 的引用序号为 92/91/93）。所以这类键一律返回 None 并列入
unresolved_double_keys —— 早前按 string 布局硬解 double 会解出 1.9e-312 这类次正规垃圾。
需要真实扫频请以 GUI 里的 setup 为准。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: 求解设置参数名（与录制脚本 cem.EditSolution 的键对齐，含嵌套子键）
_SETUP_KEYS: tuple[str, ...] = (
    # General
    "Algorithm", "Enabled", "SolutionMode", "Composite", "SweepType", "SaveFields",
    "MaxSolutions", "ErrorTolerance", "FrequencySweeps", "RangeCount",
    "RangeEnd", "RangeStart", "RangeStep", "RangeType",
    # Mesh
    "MaxInitialMeshSize", "UseWavelengthInDielectric", "SolutionFrequency",
    "FrequencyList", "Frequency", "MaxDelta", "MaxNumberOfPass", "AdaptiveMesh",
    "ReturnToInitialMesh", "MeshGrowthPerPassByPercentage", "MinimumNumberOfPasses",
    "MinimumConvergedPasses", "Verbose", "AspectRatioValue", "AspectRatio",
    "AUXMeshOption", "AdaptEstimator", "FastAdaptionForLargeProblems", "MeshTollerance",
    "MeshAlgorithm", "OpenSurfaceMeshClean", "OpenSurfaceMeshCleanType",
    "SurfaceMeshCleanTolerance", "MinInitialMeshSize", "SurfaceGradation",
    "VolumeGradation", "ProximityFeature", "GeometryAdaptive", "MeshIntersection",
    "ParallelAdaption",
    # Advance
    "OrderOfBasisFunctions", "SolutionOptionsType", "NumbersOfDomains",
    "RelativeTolerance", "SolverPrecision", "SolverBLRCompression",
    "ApproximationAccuracy", "ThermalRelated", "TemperatureFile", "TemperatureUnit",
    "ThermalLengthUnit", "Deformation", "DeformFile", "DeformLengthUnit",
    "CopyDataFromOtherResults",
    # Parallel
    "NumberOfProcesses", "NumberOfThreadsPerProcess", "MaxMemoryPerProcess(MB)",
    "SolverPercentageMemory(%)", "SolverRHSBlockSize", "ApplyOutOfCore",
    "RequestNodeNumber", "ClusterSetup",
)

#: 值类型是 double 但记录内无数值的哨兵（read_setup_report 会把它转成 None + unresolved）
_UNRESOLVED = object()

#: 字符串值长度上限（容纳长路径；超过视为解析错位）
_MAX_STR = 4096

#: 变量名形态（允许 R_TSV / L1 / xsub.y 这类；长度上限 64，防止把二进制噪声当名字）
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")

#: 解名称形态：算法前缀白名单 + 序号，前后不许紧邻标识符字符（避免命中 XFEM_1、FEM_12a 这类）
_SOLUTION_RE = re.compile(rb"(?<![A-Za-z0-9_])(FEM|MoM|FDTD|PO)_([0-9]{1,4})(?![0-9A-Za-z_])")

#: setup 名形态：`Setup1` / `Setup1(FEM)` / `Setup_20261106_151752(MoM)`
#: 实测：从 .aedt 刚导入时叫 `Setup1`，求解后工程里存成 `Setup1(FEM)`（算法在括号里）。
#: 传错名字 ⇒ cem.FEMAnalyze 静默空转（3s 返回、无 mpiexec/phofem、无新解）。
_SETUP_RE = re.compile(rb"(?<![A-Za-z0-9_])(Setup[0-9_][A-Za-z0-9_]*(?:\([A-Za-z]+\))?)(?![A-Za-z0-9_])")


def _find_key_positions(data: bytes, key: str) -> list[int]:
    """找 key 带 4 字节长度前缀的所有出现位置。

    必须校验前缀，否则短键会命中长键的子串（如 "Frequency" 命中 "SolutionFrequency"、
    "FrequencyList"），把邻居的值当成本键的值。
    """
    kb = key.encode()
    prefix = len(kb).to_bytes(4, "little")
    out: list[int] = []
    idx = 0
    while True:
        pos = data.find(kb, idx)
        if pos < 0:
            return out
        if pos >= 4 and data[pos - 4:pos] == prefix:
            out.append(pos)
        idx = pos + 1


def _extract_value(data: bytes, key: str) -> Any:
    """在 data 里找 key，按「类型 + 0x18 + 长度 + 值」取值。

    Returns:
        字符串 / 布尔值；值类型是 double 时返回 _UNRESOLVED；键不存在返回 None。
    """
    for pos in _find_key_positions(data, key):
        p = pos + len(key)
        if p + 12 > len(data):
            continue
        vtype = int.from_bytes(data[p:p + 4], "little")
        vlen = int.from_bytes(data[p + 8:p + 12], "little")
        if vtype in (0, 1, 2):  # 字符串 / 数值存成字符串
            if not (0 < vlen < _MAX_STR) or p + 12 + vlen > len(data):
                continue
            return data[p + 12:p + 12 + vlen].decode("utf-8", "replace")
        if vtype == 3:  # 布尔
            if p + 16 > len(data):
                continue
            return data[p + 12:p + 16] == b"\x01\x00\x00\x00"
        if vtype == 4:  # double：记录内只有引用序号，无数值
            return _UNRESOLVED
    return None


def read_setup_report(sem_path: str) -> dict[str, Any]:
    """读取 .sem 的求解设置：可解析参数 + 未解出的 double 键清单。

    Returns:
        {"setup": {...}, "unresolved_double_keys": [...], "count": N, "resolved_count": M}
        setup 里 double 键的值固定为 None。
    """
    data = Path(sem_path).read_bytes()
    setup: dict[str, Any] = {}
    unresolved: list[str] = []
    for key in _SETUP_KEYS:
        v = _extract_value(data, key)
        if v is _UNRESOLVED:
            setup[key] = None
            unresolved.append(key)
        elif v is not None:
            setup[key] = v
    return {
        "setup": setup,
        "unresolved_double_keys": unresolved,
        "count": len(setup),
        "resolved_count": len(setup) - len(unresolved),
    }


def read_setup_params(sem_path: str) -> dict[str, Any]:
    """读取 .sem 工程里已配置的求解设置参数（已知键的子集；double 键为 None）。"""
    return read_setup_report(sem_path)["setup"]


def is_solved(sem_path: str) -> bool:
    """工程是否已求解过（.sem 里存在 SolutionId 标记）。

    实测：CPW_SL（已求解）含 SolutionId，CPWG-1（未求解）不含。用于「导出/建报告前
    先判断要不要自动求解」。
    """
    return b"SolutionId" in Path(sem_path).read_bytes()


def extract_design_variables(sem_path: str) -> list[str]:
    """提取 .sem 里的设计/扫参变量名（去重保序）。

    两种记录布局都认（实测）：
      * 新布局（TSV_SL 等）：`VariableName` +4B 类型 +4B 记录长 +4B 字符串长 + 名字
      * 旧布局（CPW_SL 等）：`Variable` +4B 类型 +4B ? +4B 字符串长 + 名字

    只覆盖名字以明文串存的记录；纯数值型（无名字串）取不到。读不到返回空列表。
    """
    data = Path(sem_path).read_bytes()

    def _scan(key: str, len_off: int) -> list[str]:
        found: list[str] = []
        for pos in _find_key_positions(data, key):
            q = pos + len(key)
            if q + len_off + 4 > len(data):
                continue
            vlen = int.from_bytes(data[q + len_off:q + len_off + 4], "little")
            start = q + len_off + 4
            if not (0 < vlen < _MAX_STR) or start + vlen > len(data):
                continue
            name = data[start:start + vlen].decode("utf-8", "replace")
            if name and _IDENT_RE.match(name) and name not in found:
                found.append(name)
        return found

    names = _scan("VariableName", 8)      # +4 类型 +4 记录长，再取 4B 长度
    if names:
        return names
    return _scan("Variable", 8)            # 旧布局兜底


def detect_port_count(sem_path: str) -> int:
    """推断工程端口数：优先看同目录已有的 Touchstone（`x.s2p` ⇒ 2 端口），否则默认 2。

    `.sem` 里没有可靠的端口计数标记（实测 BGA 工程里 `WavePort/LumpedPort` 都是 0 次），
    而导出产物后缀是最可靠的事实来源。建报告时用端口数生成 `Quantity` 列表：
    写死 2 端口会让四端口模型静默少画线。
    """
    p = Path(sem_path)
    best = 0
    try:
        for f in p.parent.glob(f"{p.stem}.s*p"):
            m = re.fullmatch(r"\.s(\d+)p", f.suffix, re.IGNORECASE)
            if m:
                best = max(best, int(m.group(1)))
    except OSError:
        pass
    return best or 2


def detect_solution_names(sem_path: str) -> list[str]:
    """返回 .sem 里的解名称候选（算法前缀白名单，按序号升序去重）。

    只认 `FEM_/MoM_/FDTD_/PO_<序号>`：不加白名单时，二进制里 `A_7`、`Q_0`、`x_7`、
    `ParametricSetup_20260713` 都会命中，按字母序取首个会拿到 `A_7` 这种噪声（实测）。
    ⚠️ 与录制脚本 analys.py 的 `'Solution Name': 'FEM_Setup1'` 冲突（.sem 里没有该字面量），
    谁才是 report/ExportSnpFile 认的解名尚未实测确认；调用方应把候选一并回传，别静默当真。
    读不到返回空列表。
    """
    data = Path(sem_path).read_bytes()
    found: set[tuple[int, str]] = set()
    for m in _SOLUTION_RE.finditer(data):
        found.add((int(m.group(2)), m.group(1).decode("ascii")))
    return [f"{algo}_{idx}" for idx, algo in sorted(found)]


def detect_solution_name(sem_path: str) -> str:
    """探测 .sem 里的解名称：取序号最大的候选（= 最近一次求解）。找不到返回空串。

    实测：同一 setup 每次 FEMAnalyze 都会新增一个序号（CPW_SL 工程求解两次后
    .sem 里同时有 FEM_1、FEM_2）。
    """
    names = detect_solution_names(sem_path)
    return names[-1] if names else ""


def detect_setup_names(sem_path: str) -> list[str]:
    """返回 .sem 里的 setup 名候选（可能带 `(算法)` 后缀），带括号的、出现次数多的排前面。

    实测：CPW_SL 工程求解后 .sem 里 `Setup1(FEM)` 出现 67 次，裸 `Setup1` 只有 3 次（且都是
    `Setup1(FEM)` 的子串）⇒ 直接传 `Setup1` 给 cem.FEMAnalyze 会找不到 setup、静默空转。
    """
    data = Path(sem_path).read_bytes()
    counts: dict[str, int] = {}
    for m in _SETUP_RE.finditer(data):
        name = m.group(1).decode("ascii", "ignore")
        counts[name] = counts.get(name, 0) + 1
    return [n for n, _ in sorted(counts.items(), key=lambda kv: ("(" in kv[0], kv[1]), reverse=True)]


def detect_setup_name(sem_path: str) -> str:
    """探测该传给 cem.FEMAnalyze 的 setup 名（带算法后缀的那个）。找不到返回空串。"""
    names = detect_setup_names(sem_path)
    return names[0] if names else ""


def split_setup_name(name: str) -> tuple[str, str]:
    """`Setup1(FEM)` → ("Setup1", "FEM")；没有括号时算法按 "FEM" 处理。

    依据实测：GUI 的结果树 / Build Rectangular Plot 对 `Setup1(FEM)` 显示的解名是
    `FEM_Setup1` ⇒ 解名 = 算法 + "_" + setup 基名。
    """
    m = re.fullmatch(r"(?P<base>.+?)\((?P<algo>[A-Za-z]+)\)", (name or "").strip())
    if m:
        return m.group("base"), m.group("algo")
    return (name or "").strip(), "FEM"
