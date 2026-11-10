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
