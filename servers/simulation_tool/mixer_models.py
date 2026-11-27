"""`run_mixer_simulation` 的 project 参数结构化模型 —— 只为把结构写进 JSON Schema。

**为什么需要**：`project: dict` 在 `tools/list` 里只能生成
`{"type": "object", "additionalProperties": true}` —— 调用方（agent 或人）拿不到
`defaults` / `lo` / `indicators` 的字段名、必填与嵌套，只能靠报错反推。
2026-11-26 实测：另一个会话为一张 5 字段的参数表试了 3 轮、38.5 万 tokens，
还被客户端"3 次失败熔断 44s"卡住。

**字段为什么全是 Optional**：真正的必填校验仍由 `mixer_simulation._check_project`
负责 —— 它给的是带 `location` / `missing_field` 的中文报错，比 pydantic 的 422 好用。
这里只负责"把结构写进 schema"，不抢校验的活。

注意：`model_dump(exclude_none=True)` 会丢掉未提供的字段，因此下游校验仍能看出"缺字段"。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoStage(BaseModel):
    """一级本振（按寻路得到的混频器顺序，一个混频器一项）。"""

    model_config = ConfigDict(extra="allow")

    freq_ghz: list[float] | None = Field(
        default=None, description="本振频率范围 [min, max] GHz；固定本振写 [f, f]")
    power_dbm: float | None = Field(default=None, description="本振功率 dBm")
    output_bw_ghz: float | None = Field(
        default=None, description="该级混频器输出中频带宽 GHz；省略或 0 表示单点")


class LinkDefaults(BaseModel):
    """该链路公共工况（5 项必填，由下游校验器强制）。"""

    model_config = ConfigDict(extra="allow")

    in_port: int | None = Field(default=None, description="RF 输入端口号（≥1，且不能与 out_port 相同）")
    out_port: int | None = Field(default=None, description="IF 输出端口号")
    input_freq_ghz: list[float] | None = Field(
        default=None,
        description="输入频率范围 [min, max] GHz —— **必须 min < max**；固定频率也要写成递增小区间（如 [2, 2.1]）")
    input_power_dbm: float | None = Field(default=None, description="默认输入功率 dBm")
    lo: list[LoStage] | None = Field(
        default=None, description="本振数组（必须非空），按寻路得到的混频器顺序一一配对")


class SwitchBit(BaseModel):
    """链路中的开关状态（bits 原样透传给网表）。"""

    model_config = ConfigDict(extra="allow")

    instance: str | None = Field(default=None, description="开关器件实例名")
    bits: str | None = Field(default=None, description='开关位，如 "1"')


class IndicatorSpec(BaseModel):
    """一个指标：`name` 为 15 个标准名之一，其余字段随指标类型而变。"""

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(
        default=None, description="15 个标准指标名之一（如 变频增益 / 噪声 / 镜像抑制 / 输入 P1dB）")
    orders: dict[str, int] | None = Field(
        default=None, description='交调阶数：单级写 {"FREQ":5,"LO":5}；多级混频逐本振写 {"FREQ":5,"LO1":5,"LO2":5}')
    input_freq_ghz: float | list[float] | dict[str, float] | None = Field(
        default=None,
        description="**指标级只认标量 GHz**（如 35）；写成区间会被拒 —— 区间请放该链路的 defaults")
    input_power_sweep_dbm: dict[str, float] | None = Field(
        default=None, description='功率扫描 {"start", "stop", "step"} dBm（输入/输出 P1dB、Psat 必填）')
    frequency_sweep_ghz: dict[str, float] | None = Field(
        default=None, description='扫频 {"start", "stop", "step"} GHz（镜像抑制/带外杂散等）')
    peer_link: dict[str, int] | None = Field(
        default=None, description='受害链路 {"link_index": <0 基下标>}（通道间隔离度必填，且只能写在指标里）')
    snr_min_db: float | None = Field(default=None, description="最小信噪比（单通道线性动态范围必填）")
    tone_frequencies_ghz: list[float] | None = Field(
        default=None, description="双音频率 GHz（交调抑制必填）")


class MixerLink(BaseModel):
    """一条链路：开关状态 + 公共工况 + 指标列表。**链路不带 name**（name 只在 project 顶层，是工程名）。"""

    model_config = ConfigDict(extra="allow")

    switch_bits: list[SwitchBit] | None = Field(
        default=None, description="该链路上开关的目标状态；省略则用网表里的默认值")
    defaults: LinkDefaults | None = Field(default=None, description="该链路公共工况（5 项必填项见 LinkDefaults）")
    indicators: list[IndicatorSpec] | None = Field(
        default=None, description="指标列表（≥1），name 取 15 个标准名之一")


class MixerProject(BaseModel):
    """`run_mixer_simulation` 的 project 参数。"""

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None, description="工程名（用于结果目录命名）")
    epp: str | None = Field(default=None, description=".epp 工程绝对路径（同目录需有 netlist.log 与 component.json）")
    links: list[MixerLink] | None = Field(default=None, description="链路数组（≥1）")
