"""工具入参的 pydantic 结构化模型 —— 只为把字段结构写进 `tools/list` 的 JSON Schema。

**为什么需要**：`position: dict` / `parameters: dict` / `series: list[dict]` 这类裸注解在
`tools/list` 里只能生成 `{"type": "object", "additionalProperties": true}`，调用方
（agent 或人）看不到字段名、类型与必填关系，只能靠报错反推。2026-11-26 实测
`run_mixer_simulation(project: dict)` 因此让一个会话烧掉 38.5 万 tokens、3 轮黑盒试错
（见 `simulation_tool/mixer_models.py` 的同一段说明）。

**字段为什么全是 Optional + `extra="allow"`**：真正的必填 / 取值范围校验仍由各工具自己的
`require_*` / `_check_*` 负责 —— 那些报错带 `location`、`missing_field` 和中文 hint，比
pydantic 的 422 好用得多。这里只负责「把结构写进 schema」，不抢校验的活。

**工具里怎么用**：入口统一写

    if hasattr(position, "model_dump"):
        position = position.model_dump(exclude_none=True)

`exclude_none` 保证「未提供的字段不会凭空出现」，下游的 `"position" in item` 这类
「省略即保持原值」语义照旧成立。

对应关系（工具 → 参数 → 模型）见文件末尾 `MODEL_PARAMS`，由
`servers/schema_descriptions.py::_model_param_map()` 消费。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Loose(BaseModel):
    """所有参数模型的基类：允许额外字段，避免本地模型比 EDI 枚举更严格而误拒。"""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------- 几何


class Position(_Loose):
    """原理图场景坐标（不是毫米、不是截图像素）。"""

    x: float | None = Field(default=None, description="X 坐标（数值，场景值）")
    y: float | None = Field(default=None, description="Y 坐标（数值，场景值）")


class ComponentTransform(_Loose):
    """一个器件的目标几何状态；省略的字段保持原值。"""

    uuid: str | None = Field(default=None, description="器件 uuid（与 instance_name 至少给一个）")
    instance_name: str | None = Field(default=None, description='器件实例名（如 "R1"）；与 uuid 至少给一个')
    position: Position | None = Field(default=None, description="目标绝对坐标 {x, y}；按 12.5 网格吸附")
    rotation_deg: int | float | None = Field(
        default=None, description="目标绝对旋转角（度），归一化到 [0, 360)；450 等效 90，-90 等效 270")
    mirrored_horizontal: bool | None = Field(default=None, description="目标绝对状态：水平镜像")
    mirrored_vertical: bool | None = Field(default=None, description="目标绝对状态：垂直镜像")


# ---------------------------------------------------------------- 器件参数


class ParamValue(_Loose):
    """单个器件参数的取值。`value` 必填，`unit` 视参数而定。"""

    value: Any = Field(
        default=None,
        description='参数值（字符串或数值都行）；引用工程变量时直接写变量名，如 "freqin"')
    unit: str | None = Field(
        default=None, description='单位，如 "GHz" / "dB" / "mm" / "dBm"；无单位参数可省略')


# ---------------------------------------------------------------- 模型库过滤


class ModelFilter(_Loose):
    """模型库检索的一个过滤条件（search_public_models / search_personal_models 的 filters 元素）。"""

    key: str | None = Field(default=None, description="参数 id（get_model_category_params 返回，如 gain / min_freq）")
    min: float | None = Field(default=None, description="下限（闭区间，含边界；与 max 二选一或都用）")
    max: float | None = Field(default=None, description="上限（闭区间，含边界）")


# ---------------------------------------------------------------- 混频杂散


class MixerSpurSpec(_Loose):
    """一路混频器（杂散分析的输入）。"""

    name: str | None = Field(default=None, description="这路混频器的名字（结果里按它回显）")
    enabled: bool | None = Field(default=None, description="是否参与分析（至少一路 true）")
    relation: str | None = Field(
        default=None,
        description=("接收：low_side / high_side（缺省 low_side）；"
                     "发射：sum / diff_lo / diff_if（缺省 sum）"))
    rf_center_mhz: float | None = Field(default=None, description="RF 中心频率 MHz（>0，缺省 1000）")
    rf_bandwidth_mhz: float | None = Field(default=None, description="RF 带宽 MHz（缺省 0）")
    if_bandwidth_mhz: float | None = Field(default=None, description="IF 带宽 MHz（缺省 1）")
    rf_power_dbm: float | None = Field(default=None, description="RF 功率 dBm（缺省 -10）")
    lo_power_dbm: float | None = Field(default=None, description="LO 功率 dBm（缺省 7）")


# ---------------------------------------------------------------- 寻路


class PortPair(_Loose):
    """一个有向端口对。"""

    input: int | None = Field(default=None, description="输入端口号（正整数）")
    output: int | None = Field(default=None, description="输出端口号（正整数，不能与 input 相同）")


# ---------------------------------------------------------------- 绘图


class ChartSeries(_Loose):
    """一条曲线。"""

    name: str | None = Field(default=None, description="图例名；空=不显示图例，非空时全图必须唯一")
    points: list[list[float]] | None = Field(
        default=None, description="数据点数组 [[x, y], ...]（非空、有限数值）")


class ChartMark(_Loose):
    """一个标注点。"""

    series: str | None = Field(default=None, description="所属曲线的 name（必须精确匹配已有 series）")
    point: list[float] | None = Field(default=None, description="标注坐标 [x, y]（必须精确匹配该曲线上的点）")
    label: str | None = Field(default=None, description="标注文字")


# ---------------------------------------------------------------- 报告导出


class ReportElectricalParameter(_Loose):
    """一条电性能指标。"""

    indicator: str | None = Field(default=None, description="指标名（如 增益 / 噪声系数 / 输出 P1dB）")
    minimum: float | str | None = Field(default=None, description="下限（数值，或 \"—\" 之类的占位文字）")
    typical: float | str | None = Field(default=None, description="典型值")
    maximum: float | str | None = Field(default=None, description="上限")
    unit: str | None = Field(default=None, description='单位，如 "dB" / "dBm"')
    requirement: str | None = Field(default=None, description="指标要求（自由文本）")
    result: float | str | None = Field(default=None, description="仿真/实测结果")


class ReportLink(_Loose):
    """一条链路的结果表。"""

    name: str | None = Field(default=None, description="链路名")
    electrical_parameters: list[ReportElectricalParameter] | None = Field(
        default=None, description="该链路的电性能指标数组（与 run_tr_simulation 的 results 同构）")


class ReportComponent(_Loose):
    """报告里的一个器件条目。"""

    type: str | None = Field(default=None, description="器件类型/功能，如 LNA / 混频器")
    model: str | None = Field(default=None, description="型号")
    manufacturer: str | None = Field(default=None, description="厂商")
    specs: Any = Field(default=None, description="关键规格（自由结构：dict 或字符串）")


class ReportChart(_Loose):
    """报告里插入的一张图。"""

    link: str | None = Field(default=None, description="归属的链路名")
    indicator: str | None = Field(default=None, description="图对应的指标名")
    image_path: str | None = Field(default=None, description="图片**绝对**路径（相对路径会解析错）")


class SimulationReport(_Loose):
    """`export_simulation_report(report=...)` 的报告数据（透传 Report_Exporter 原生 JSON）。"""

    model_name: str | None = Field(default=None, description="封面型号")
    description: str | None = Field(default=None, description="简介")
    conclusion: str | None = Field(default=None, description="结论")
    components: list[ReportComponent] | None = Field(default=None, description="器件清单")
    links: list[ReportLink] | None = Field(default=None, description="各链路的指标表")
    charts: list[ReportChart] | None = Field(default=None, description="插入报告的图")
    schematic_path: str | None = Field(default=None, description="拓扑图**绝对**路径（可被 schematic 参数覆盖）")


# ---------------------------------------------------------------- 工具 → 参数 → 模型


#: 参数本身就是一个对象
OBJECT_PARAMS: dict[str, dict[str, type[BaseModel]]] = {
    "add_ideal_component": {"position": Position},
    "add_performance_component": {"position": Position},
    "export_simulation_report": {"report": SimulationReport},
}

#: 参数是 `list[Model]`（元素的字段才是调用方要看的东西）
ARRAY_PARAMS: dict[str, dict[str, type[BaseModel]]] = {
    "update_schematic_components_transform": {"components": ComponentTransform},
    "analyze_mixer_spur": {"mixers": MixerSpurSpec},
    "find_netlist_paths": {"pairs": PortPair},
    "draw_chart": {"series": ChartSeries, "marks": ChartMark},
    "search_public_models": {"filters": ModelFilter},
    "search_personal_models": {"filters": ModelFilter},
}

#: 参数是 `dict[str, Model]`（键是参数名 / 变量名，运行时才知道，故只能约束值）
DICT_PARAMS: dict[str, dict[str, type[BaseModel]]] = {
    "update_components_from_schematic": {"parameters": ParamValue},
    "replace_port_component": {"parameters": ParamValue},
}

#: 无法用模型表达、但仍想给出结构的参数（手工 schema）
RAW_SCHEMAS: dict[tuple[str, str], dict] = {
    ("export_laspcem_snp", "sweeping_parameters"): {
        "type": "object",
        "description": ('扫参变量，形如 {"L1": ["nominal", 140, 150]}；不传则按工程变量取标称值'),
        "additionalProperties": {"type": "array", "items": {}},
    },
    # float | list 的 union：scalar 是「整个阻带抑制 ≥A」，列表是 [[f, A]] / [[f_lo, f_hi, A]]
    ("filter_order_bandstop", "stopband_specs"): {
        "anyOf": [
            {"type": "number", "description": "整个阻带抑制 ≥A dB 时直接给浮点数 A"},
            {"type": "array", "description": "抑制点/子带列表",
             "items": {"type": "array", "items": {"type": "number"},
                       "minItems": 2, "maxItems": 3,
                       "description": "每项 [f, A]（抑制点）或 [f_lo, f_hi, A]（子带），频率 GHz、抑制 dB"}},
        ],
    },
}

#: 「只能给列表」的三个阶数估算工具共用同一份元素结构。
# 它们的签名是裸 `stopband_specs: list`，FastMCP 只能生成 `items: {}` —— 元素里一个约束都没有，
# agent 从 tools/list 看不出每项该给 [f, A] 还是 {f, A}，只能靠运行时 INVALID_PARAMETERS 试错。
# bandstop 的签名是 `float | list` 且上面已单独补过，故不在这个列表里。
_STOPBAND_ITEMS_DOC = "每项 [f, A]（抑制点）或 [f_lo, f_hi, A]（抑制频段），频率 GHz、抑制 dB"
RAW_SCHEMAS.update({
    (tool, "stopband_specs"): {
        "type": "array",
        "description": "阻带规范列表，" + _STOPBAND_ITEMS_DOC,
        # 每次给一份新 dict：schema 注入时会对节点做原地处理（去 title / 展平 $ref）
        "items": {"type": "array", "items": {"type": "number"},
                  "minItems": 2, "maxItems": 3, "description": _STOPBAND_ITEMS_DOC},
    }
    for tool in ("filter_order_lowpass", "filter_order_highpass", "filter_order_bandpass")
})


def _strip_titles(node):
    """递归去掉 pydantic 自动生成的 `title`（对 agent 无用，且会把内联 schema 撑得很长）。"""
    if isinstance(node, dict):
        node.pop("title", None)
        for v in node.values():
            _strip_titles(v)
    elif isinstance(node, list):
        for v in node:
            _strip_titles(v)
    return node


def _model_schema(model: type[BaseModel]) -> dict:
    return _strip_titles(model.model_json_schema())


def param_schema(tool: str, param: str) -> dict | None:
    """返回该参数的 JSON Schema 片段；不在三张表里就返回 None（保持 FastMCP 原样）。"""
    model = OBJECT_PARAMS.get(tool, {}).get(param)
    if model is not None:
        return _model_schema(model)

    model = ARRAY_PARAMS.get(tool, {}).get(param)
    if model is not None:
        return {"type": "array", "items": _model_schema(model)}

    model = DICT_PARAMS.get(tool, {}).get(param)
    if model is not None:
        return {"type": "object", "additionalProperties": _model_schema(model)}

    return RAW_SCHEMAS.get((tool, param))

