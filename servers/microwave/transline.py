#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transline.py -- 传输线计算核心（KiCad transline_calculations 的 Python 移植）
==============================================================================

纯代码、零依赖（只用标准库 math / cmath）、无界面。
当前已移植：

    * Microstrip         微带线   (Hammerstad-Jensen + Getsinger 色散 + 覆盖层修正)
    * Stripline          带状线   (Cohn 双子线并联模型 + 导体厚度修正)
    * GCPW               接地共面波导 (保角变换 + 有限厚度修正 + Gevorgian 色散)
    * Coax               同轴线   (TEM 精确解 + Pozar 损耗 + TE/TM 高阶模截止)
    * CoupledMicrostrip  耦合微带线/差分对 (Kirschning-Jansen 1984 全频段闭式)
    * CoupledStripline   耦合带状线/差分对 (Cohn 1955 + Wadell 偏置修正)

来源与许可
----------
本文件是 KiCad 中 ``common/transline_calculations/`` 的逐行移植：

    microstrip.cpp / microstrip.h
    stripline.cpp / stripline.h
    coplanar.cpp / coplanar.h        (仅取 CPW_BACKMETAL = 1 的 GCPW 分支)
    coax.cpp / coax.h
    coupled_microstrip.cpp / .h
    coupled_stripline.cpp / .h
    transline_calculation_base.cpp / .h
    dielectric_djordjevic_sarkar.cpp / .h
    units.h / units_scales.h

原始算法来自 GPL 的 ``transline`` 项目：
    Copyright (C) 2001 Gopal Narayanan <gopal@astro.umass.edu>
    Copyright (C) 2002 Claudio Girardi <claudio.girardi@ieee.org>
    Copyright (C) 2005, 2006 Stefan Jahn <stefan@lkcc.org>
    Modified for KiCad: 2018 Jean-Pierre Charras
    Copyright The KiCad Developers, see AUTHORS.txt for contributors.

KiCad 源码为 GPL-2.0-or-later，故本移植文件同样以 **GPL-2.0-or-later** 发布。
（注意：这与先前手写版 rf_transline.py 的 MIT 声明不同——移植版是衍生作品。）

与 KiCad 的已知差异（均已在对应位置写明理由）
--------------------------------------------
1. **椭圆积分取模数语义。** KiCad 的 EllipticIntegral() 内部把入参当作参数
   m = k²，但 coplanar.cpp 的所有调用点传的都是模数 k 及其补模数 √(1-k²)。
   本移植按模数语义实现，使 CPW 的 q = K(k)/K(k') 符合 Ghione-Naldi 原式
   （对典型 GCPW，Z0 因此比 KiCad 低约 2~4%）。
2. **GCPW 阻焊层修正只在 W/H ≥ 1 生效。** Wan-Hoorfar 的窄导体拟合在基线相减后
   给出非正增量，修正退化为空操作——与 KiCad 行为一致，但已显式记录。
3. **退化几何不再产生 NaN。** 带状线 T = 0、非法几何 (A + T ≥ H)、微带 er = 1 的
   介质损耗等 KiCad 会得到 0/0 或 0·ln(0) 的位置，本移植取物理正确的极限值，
   并通过结果状态（STATUS.ERROR / WARNING）标记非法输入。

单位约定
--------
内部一律使用 **国际单位制**，与 KiCad 一致：

    长度      m       （可用 MM / MICRON / MIL / INCH 常量换算）
    频率      Hz
    电导率    S/m
    角度      rad
    阻抗      ohm
    衰减      dB（ATTEN_* / LOSS_* 为整段长度的 dB）

用法
----
    from transline import Microstrip, Stripline, P, MM, MICRON

    ms = Microstrip()
    ms.set(P.EPSILONR, 4.3)
    ms.set(P.TAND, 0.02)
    ms.set(P.H, 0.2 * MM)
    ms.set(P.H_T, 1e20)          # 无覆盖层
    ms.set(P.T, 35 * MICRON)
    ms.set(P.SIGMA, 5.8e7)
    ms.set(P.MUR, 1.0)
    ms.set(P.MURC, 1.0)
    ms.set(P.ROUGH, 0.0)
    ms.set(P.PHYS_WIDTH, 0.4 * MM)
    ms.set(P.PHYS_LEN, 100 * MM)
    ms.set(P.FREQUENCY, 1e9)
    ms.analyse()
    print(ms.z0, ms.eps_eff)

综合（已知 Z0 求线宽）：

    ms.set(P.Z0, 50.0)
    ms.set(P.ANG_L, math.pi / 2)
    ms.synthesize()
    print(ms.width)
"""

from __future__ import annotations

import cmath
import math

__all__ = [
    "P", "STATUS", "SYNTH", "TranslineBase",
    "Microstrip", "Stripline", "GCPW", "Coax",
    "CoupledMicrostrip", "CoupledStripline",
    "MM", "CM", "M", "MICRON", "MIL", "INCH", "KM", "FEET",
    "C0", "ZF0", "MU0", "E0", "LOG2DB",
]

# ---------------------------------------------------------------------------
# 物理常数（与 KiCad transline_calculations/units.h 完全一致）
# ---------------------------------------------------------------------------

MU0 = 12.566370614e-7     # 真空磁导率
E0 = 8.854e-12            # 真空介电常数
C0 = 299792458.0          # 真空光速
ZF0 = 376.730313412       # 真空波阻抗（CODATA 2022）
LOG2DB = 20.0 / math.log(10.0)

# 长度换算到米
KM = 1e3
M = 1.0
CM = 1e-2
MM = 1e-3
MICRON = 1e-6
INCH = 2.54e-2
MIL = 2.54e-5
FEET = 0.3048


# ---------------------------------------------------------------------------
# C 浮点语义辅助（Python 的 math 会抛异常的地方，C 只产生 inf / NaN）
# ---------------------------------------------------------------------------
# 耦合线公式（Kirschning-Jansen / Cohn）里有多处 0/0、0·ln0、负底数幂，
# KiCad 在 C++ 下得到 inf/NaN 并继续算，Python 会抛 ZeroDivisionError /
# ValueError / OverflowError。下面四个包装函数把这些位置还原成 IEEE 行为，
# 让"退化几何 → NaN → 状态层报 ERROR"这条链路与 KiCad 一致。

def _div(x, y):
    """0/0 → NaN，非零/0 → ±inf，其余同普通除法。"""
    try:
        return x / y
    except ZeroDivisionError:
        if x == 0.0 or not math.isfinite(x):
            return math.nan
        return math.inf if (x > 0.0) == (math.copysign(1.0, y) > 0.0) else -math.inf


def _pow(x, y):
    """负底数 + 非整数指数 → NaN（Python 的 ** 会返回复数）；0 的负次幂 → inf。"""
    if x < 0.0:
        return math.nan
    if x == 0.0:
        if y == 0.0:
            return 1.0
        return math.inf if y < 0.0 else 0.0
    try:
        return x ** y
    except OverflowError:
        return math.inf


def _acosh(x):
    """x < 1 → NaN（C 的 acosh 语义）。"""
    if x < 1.0:
        return math.nan
    return math.acosh(x)


def _atanh(x):
    """|x| > 1 → NaN，|x| == 1 → ±inf（C 的 atanh 语义）。"""
    ax = abs(x)
    if ax > 1.0:
        return math.nan
    if ax == 1.0:
        return math.inf if x > 0.0 else -math.inf
    return math.atanh(x)


def _cosh(x):
    try:
        return math.cosh(x)
    except OverflowError:
        return math.inf


def _coth(x):
    return _div(math.cosh(x), math.sinh(x))


def _sech(x):
    return _div(1.0, math.cosh(x))


# ---------------------------------------------------------------------------
# 参数键与状态（对应 TRANSLINE_PARAMETERS / TRANSLINE_STATUS）
# ---------------------------------------------------------------------------

class P:
    """参数键。取值即参数名，便于打印与调试。"""
    EPSILONR = "EPSILONR"                      # 相对介电常数
    TAND = "TAND"                              # 介质损耗角正切
    H = "H"                                    # 基板厚度
    H_T = "H_T"                                # 覆盖层上表面高度（极大值=无覆盖）
    T = "T"                                    # 导体厚度
    PHYS_WIDTH = "PHYS_WIDTH"                  # 线宽
    PHYS_LEN = "PHYS_LEN"                      # 线长
    ROUGH = "ROUGH"                            # 表面粗糙度
    MUR = "MUR"                                # 基板相对磁导率
    MURC = "MURC"                              # 导体相对磁导率
    FREQUENCY = "FREQUENCY"                    # 工作频率
    STRIPLINE_A = "STRIPLINE_A"                # 带状线：导体上表面到上地平面距离
    Z0 = "Z0"                                  # 特性阻抗
    ANG_L = "ANG_L"                            # 电长度（rad）
    SIGMA = "SIGMA"                            # 金属电导率
    SKIN_DEPTH = "SKIN_DEPTH"                  # 趋肤深度
    LOSS_DIELECTRIC = "LOSS_DIELECTRIC"        # 介质损耗（dB）
    LOSS_CONDUCTOR = "LOSS_CONDUCTOR"          # 导体损耗（dB）
    EPSILON_EFF = "EPSILON_EFF"                # 有效介电常数
    UNIT_PROP_DELAY = "UNIT_PROP_DELAY"        # 单位传播延时（ps/cm）
    ATTEN_COND = "ATTEN_COND"                  # 导体衰减（dB）
    ATTEN_DILECTRIC = "ATTEN_DILECTRIC"        # 介质衰减（dB）
    DIELECTRIC_MODEL_SEL = "DIELECTRIC_MODEL_SEL"    # 0=常数, 1=Djordjevic-Sarkar
    EPSILONR_SPEC_FREQ = "EPSILONR_SPEC_FREQ"        # er/tand 的标定频率
    SOLDERMASK_PRESENT = "SOLDERMASK_PRESENT"
    SOLDERMASK_THICKNESS = "SOLDERMASK_THICKNESS"
    SOLDERMASK_EPSILONR = "SOLDERMASK_EPSILONR"
    SOLDERMASK_TAND = "SOLDERMASK_TAND"
    SOLDERMASK_FILLS_GAPS = "SOLDERMASK_FILLS_GAPS"  # GCPW：阻焊是否填满槽
    PHYS_S = "PHYS_S"                          # 共面波导：中心导体到地平面的槽宽
    PHYS_DIAM_IN = "PHYS_DIAM_IN"              # 同轴线：内导体直径
    PHYS_DIAM_OUT = "PHYS_DIAM_OUT"            # 同轴线：外导体内径
    CUTOFF_FREQUENCY = "CUTOFF_FREQUENCY"      # 同轴线：TE11 截止频率

    # --- 差分对（耦合线）------------------------------------------------
    Z0_E = "Z0_E"                              # 偶模特性阻抗
    Z0_O = "Z0_O"                              # 奇模特性阻抗
    Z_DIFF = "Z_DIFF"                          # 差分阻抗 = 2*Z0_O
    Z_COMM = "Z_COMM"                          # 共模阻抗 = Z0_E/2
    COUPLING_K = "COUPLING_K"                  # 耦合系数 (Z0e-Z0o)/(Z0e+Z0o)
    EPSILON_EFF_EVEN = "EPSILON_EFF_EVEN"      # 偶模有效介电常数
    EPSILON_EFF_ODD = "EPSILON_EFF_ODD"        # 奇模有效介电常数
    UNIT_PROP_DELAY_EVEN = "UNIT_PROP_DELAY_EVEN"
    UNIT_PROP_DELAY_ODD = "UNIT_PROP_DELAY_ODD"
    ATTEN_COND_EVEN = "ATTEN_COND_EVEN"
    ATTEN_COND_ODD = "ATTEN_COND_ODD"
    ATTEN_DILECTRIC_EVEN = "ATTEN_DILECTRIC_EVEN"
    ATTEN_DILECTRIC_ODD = "ATTEN_DILECTRIC_ODD"


class STATUS:
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


class SYNTH:
    """
    综合模式（对应 SYNTHESIZE_OPTS）。

    只有差分对用得上：KiCad 允许固定线宽反解间距、固定间距反解线宽，
    或直接以 (Z_DIFF, Z_COMM) 为目标做二维联立求解。
    """
    DEFAULT = "DEFAULT"                  # 默认：二维联立求解 (W, S)
    FIX_WIDTH = "FIX_WIDTH"              # 固定线宽，反解间距
    FIX_SPACING = "FIX_SPACING"          # 固定间距，反解线宽
    FROM_ZDIFF_ZCOMM = "FROM_ZDIFF_ZCOMM"  # 以 Z_DIFF / Z_COMM 为目标


# ---------------------------------------------------------------------------
# Djordjevic-Sarkar 宽频因果介质模型（可选，默认关闭）
# ---------------------------------------------------------------------------

class _DjordjevicSarkar:
    """
    Djordjevic et al., IEEE Trans. EMC 43(4):662-667, Nov. 2001, 式 (8)-(11)。
    由单点 (er, tand, f_spec) 拟合出 (eps_inf, m)，之后每次查询只做一次复对数。
    """

    def __init__(self, eps_r_spec, tan_d_spec, f_spec, f1=1.0e3, f2=1.0e12):
        if f1 <= 0.0:
            raise ValueError("DIELECTRIC_DJORDJEVIC_SARKAR: f1 must be positive")
        if f2 <= f1:
            raise ValueError("DIELECTRIC_DJORDJEVIC_SARKAR: f2 must exceed f1")
        if f_spec < f1 or f_spec > f2:
            raise ValueError("DIELECTRIC_DJORDJEVIC_SARKAR: f_spec outside [f1, f2]")

        self.f1 = f1
        self.f2 = f2

        if tan_d_spec == 0.0:          # 无耗短路，跳过复对数
            self.lossless = True
            self.eps_inf = eps_r_spec
            self.m = 0.0
            return

        k = cmath.log((f2 + 1j * f_spec) / (f1 + 1j * f_spec))
        self.m = -tan_d_spec * eps_r_spec / k.imag
        self.eps_inf = eps_r_spec * (1.0 + tan_d_spec * k.real / k.imag)
        self.lossless = False

    def complex_eps_at(self, f):
        if self.lossless:
            return complex(self.eps_inf, 0.0)
        return self.eps_inf + self.m * cmath.log((self.f2 + 1j * f) / (self.f1 + 1j * f))

    def eps_real_at(self, f):
        return self.complex_eps_at(f).real

    def tan_delta_at(self, f):
        if self.lossless:
            return 0.0
        e = self.complex_eps_at(f)
        return -e.imag / e.real


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------

class TranslineBase:
    """对应 TRANSLINE_CALCULATION_BASE：参数表 + 牛顿迭代 + 结果状态。"""

    _PARAMS = ()
    _MAX_ERROR = 1e-6       # Z0 优化的最大误差（绝对，ohm）

    def __init__(self):
        self._p = {k: 0.0 for k in self._PARAMS}
        self._analysis = {}
        self._synthesis = {}
        self._ds = None

    # -- 参数读写 ---------------------------------------------------------
    def set(self, param, value):
        """设置参数（对应 SetParameter）。"""
        self._p[param] = float(value)
        return self

    def get(self, param):
        """读取参数（对应 GetParameter）。"""
        return self._p[param]

    def set_params(self, **kwargs):
        """批量设置，键名用小写参数名，如 set_params(epsilon_r=4.3, h=0.2e-3)。"""
        for key, value in kwargs.items():
            self.set(key.upper(), value)
        return self

    # -- 子类实现 ---------------------------------------------------------
    def analyse(self):
        """由几何参数算出 Z0 / 电长度 / 损耗等。"""
        raise NotImplementedError

    def synthesize(self, recalculate_length=False):
        """由目标 Z0 反解几何参数。返回 True 表示收敛。"""
        raise NotImplementedError

    def _set_analysis_results(self):
        raise NotImplementedError

    def _set_synthesis_results(self):
        raise NotImplementedError

    # -- 结果 -------------------------------------------------------------
    @property
    def analysis_results(self):
        """{参数: (值, 状态)}，对应 GetAnalysisResults()。"""
        self._set_analysis_results()
        return dict(self._analysis)

    @property
    def synthesis_results(self):
        """{参数: (值, 状态)}，对应 GetSynthesisResults()。"""
        self._set_synthesis_results()
        return dict(self._synthesis)

    def _set_analysis_result(self, param, value, status=STATUS.OK):
        self._analysis[param] = (value, status)

    def _set_synthesis_result(self, param, value, status=STATUS.OK):
        self._synthesis[param] = (value, status)

    # -- 通用物理量 -------------------------------------------------------
    def _skin_depth(self):
        """趋肤深度 1/sqrt(pi*f*murc*mu0*sigma)。"""
        return 1.0 / math.sqrt(math.pi * self._p[P.FREQUENCY] * self._p[P.MURC]
                               * MU0 * self._p[P.SIGMA])

    @staticmethod
    def _unit_propagation_delay(eps_eff):
        """单位传播延时（ps/cm）。"""
        return math.sqrt(eps_eff) * (1.0e10 / 2.99e8)

    @staticmethod
    def _elliptic_integral(k):
        """
        第一/第二类完全椭圆积分 K(k)、E(k)，AGM（算术-几何平均）法，返回 (K, E)。

        参数是**模数 k**（不是参数 m = k²）：
            K(k) = ∫₀^{π/2} dθ/√(1-k²sin²θ)，   k == 1 时发散（返回 inf）。

        与 KiCad 的差异（有意为之）
        --------------------------
        KiCad 的 TRANSLINE_CALCULATION_BASE::EllipticIntegral() 内部取
        ``b = √(1-arg)``、``c = √arg``，即把入参当作**参数 m = k²**；但所有调用点
        （coplanar.cpp 的 k1 = W/(W+2S)、k3、ke 及其补模数 √(1-k²)）传的都是**模数 k**。
        这个约定不一致会让 CPW 的 q = K(k)/K(k') 偏差数个百分点（例如
        W=0.3/S=0.2/H=0.5/er=4.3 的 GCPW，Z0 会从 71.3 Ω 抬到 73.0 Ω）。

        本移植按**模数**语义实现（内部换算 m = k²），以符合 Ghione-Naldi 原式与
        教科书 CPW 公式 Z0 = (ZF0/4)/√ε_eff · K(k')/K(k)。KiCad 的负数分支是 m<0 的
        Landen 变换，模数语义下 m = k² ≥ 0 恒成立，故该分支不可达，已省略。
        """
        NR_EPSI = 2.2204460492503131e-16
        i_max = 16

        m = k * k

        if m == 1.0:
            return math.inf, 0.0
        if m > 1.0:                 # K 在 m > 1 时解析延拓为复数，此处不接受
            return math.nan, math.nan

        a = 1.0
        b = math.sqrt(1.0 - m)
        c = math.sqrt(m)
        fr = 0.5
        s = fr * c * c             # 2^{-1}·c₀²，c₀ = k

        i = 0
        while i < i_max:
            t = (a + b) / 2.0
            c = (a - b) / 2.0
            b = math.sqrt(a * b)
            a = t
            fr *= 2.0
            s += fr * c * c

            if c / a < NR_EPSI:
                break
            i += 1

        if i >= i_max:
            return 0.0, 0.0

        half_pi = math.pi / 2.0
        return half_pi / a, half_pi * (1.0 - s) / a

    @classmethod
    def _elliptic_ratio(cls, k):
        """K(k) / K(√(1-k²))，共面波导保角变换里的标准比值。"""
        return cls._elliptic_integral(k)[0] / cls._elliptic_integral(math.sqrt(1.0 - k * k))[0]

    # -- 介质模型 ---------------------------------------------------------
    def _update_dielectric_model(self):
        if int(self._p[P.DIELECTRIC_MODEL_SEL]) != 1:
            self._ds = None
            return

        f_spec = self._p[P.EPSILONR_SPEC_FREQ]
        if not math.isfinite(f_spec) or f_spec <= 0.0:
            self._ds = None
            return

        try:
            self._ds = _DjordjevicSarkar(self._p[P.EPSILONR], self._p[P.TAND], f_spec)
        except ValueError:
            self._ds = None

    def _dispersed_epsilon_r(self, f):
        return self._ds.eps_real_at(f) if self._ds else self._p[P.EPSILONR]

    def _dispersed_tan_delta(self, f):
        return self._ds.tan_delta_at(f) if self._ds else self._p[P.TAND]

    # -- 阻焊层（仅微带类需要，默认关闭） ---------------------------------
    def _get_soldermask_delta_q(self, w_over_h, c_over_h):
        """子类覆盖；默认 0 表示该传输线不支持阻焊层修正。"""
        return 0.0

    @staticmethod
    def _wan_hoorfar_q2(u, h_bar_top):
        """Wan-Hoorfar 2000 式 (12)/(13)：多层微带第二层介质的填充因子 q_2。"""
        w_bar_eff = u + (2.0 / math.pi) * math.log(17.08 * (0.5 * u + 0.92))
        denom = w_bar_eff * math.pi - 4.0

        if denom <= 0.0 or not math.isfinite(denom):
            return 0.0

        v_bar = (2.0 / math.pi) * math.atan((2.0 * math.pi / denom) * (h_bar_top - 1.0))
        half_pi = 0.5 * math.pi * v_bar

        if u >= 1.0:
            # 宽导体
            q1 = 1.0 - math.log(w_bar_eff * math.pi - 1.0) / (2.0 * w_bar_eff)
            inner = (2.0 * w_bar_eff * math.cos(half_pi) / (2.0 * h_bar_top - 1.0 + v_bar)
                     + math.sin(half_pi))
            if inner <= 0.0 or not math.isfinite(inner):
                return 0.0
            correction = (1.0 - v_bar) / (2.0 * w_bar_eff) * math.log(inner)
            return max(0.0, 1.0 - q1 - correction)

        # 窄导体
        log_eighth = math.log(0.125 * u)
        if not math.isfinite(log_eighth) or log_eighth == 0.0:
            return 0.0

        q1 = 0.5 + 0.9 / (math.pi * log_eighth)
        bj = (h_bar_top + 1.0) / (h_bar_top + 0.25 * u - 1.0)
        if bj <= 0.0 or not math.isfinite(bj):
            return 0.0

        acos_arg = math.sqrt(bj / h_bar_top) * (h_bar_top - 1.0 + 0.125 * u)
        if acos_arg < -1.0 or acos_arg > 1.0:
            return 0.0

        correction = (math.log(bj) * math.acos(acos_arg)) / (4.0 * log_eighth)
        return max(0.0, 1.0 - q1 - correction)

    def _apply_soldermask_correction(self, eps_eff_uncoated, tan_d_substrate,
                                     eps_r_substrate, w_over_h):
        """
        三层（基板/阻焊/空气）修正，Bahl-Stuchly 1980 空气置换分解 +
        Svacina 1992 / Wan-Hoorfar 2000 填充因子。任一条件不满足则原值返回。
        """
        if self._p[P.SOLDERMASK_PRESENT] < 0.5:
            return eps_eff_uncoated, tan_d_substrate

        c_mask = self._p[P.SOLDERMASK_THICKNESS]
        h = self._p[P.H]

        if c_mask <= 0.0 or h <= 0.0 or not math.isfinite(c_mask) or not math.isfinite(h):
            return eps_eff_uncoated, tan_d_substrate

        eps_mask = self._p[P.SOLDERMASK_EPSILONR]
        tan_d_mask = self._p[P.SOLDERMASK_TAND]

        if not math.isfinite(eps_mask) or eps_mask <= 1.0:
            return eps_eff_uncoated, tan_d_substrate
        if not math.isfinite(tan_d_mask) or tan_d_mask < 0.0:
            return eps_eff_uncoated, tan_d_substrate
        if eps_r_substrate <= 1.0 or not math.isfinite(eps_r_substrate):
            return eps_eff_uncoated, tan_d_substrate

        delta_q = self._get_soldermask_delta_q(w_over_h, c_mask / h)
        if delta_q <= 0.0:
            return eps_eff_uncoated, tan_d_substrate

        eps_eff_coated = eps_eff_uncoated + delta_q * (eps_mask - 1.0)

        q_sub = min(1.0, max(0.0, (eps_eff_uncoated - 1.0) / (eps_r_substrate - 1.0)))
        delta_q_capped = min(delta_q, max(0.0, 1.0 - q_sub))

        tan_d_coated = tan_d_substrate
        if eps_eff_coated > 0.0:
            tan_d_coated = (q_sub * eps_r_substrate * tan_d_substrate
                            + delta_q_capped * eps_mask * tan_d_mask) / eps_eff_coated

        return eps_eff_coated, tan_d_coated

    # -- 一维牛顿迭代（对应 MinimiseZ0Error1D） ---------------------------
    def _minimise_z0_error_1d(self, optimise, measure, recalculate_length=False):
        p = self._p

        if not math.isfinite(p[measure]):
            p[optimise] = math.nan
            return False

        var = p[optimise]
        if (not math.isfinite(var)) or var == 0.0:
            var = 0.001
            p[optimise] = var

        z0_dest = p[measure]
        angl_dest = p[P.ANG_L]

        iteration = 0
        self.analyse()
        z0_current = p[measure]
        error = abs(z0_dest - z0_current)

        while error > self._MAX_ERROR:
            iteration += 1
            increment = var / 100.0
            var += increment
            p[optimise] = var

            self.analyse()
            z0_result = p[measure]

            # f(w) = Z0 - Z0(w);  w(n+1) = w(n) - f(w)/f'(w)
            slope = (z0_result - z0_current) / increment
            slope = (z0_dest - z0_current) / slope - increment
            var += slope

            if var <= 0.0:
                var = increment
            p[optimise] = var

            self.analyse()
            z0_current = p[measure]
            error = abs(z0_dest - z0_current)

            if iteration > 250:
                break

        if recalculate_length:
            p[measure] = z0_dest
            p[P.ANG_L] = angl_dest
            p[P.PHYS_LEN] = (C0 / p[P.FREQUENCY] / math.sqrt(p[P.EPSILON_EFF])
                             * p[P.ANG_L] / (2.0 * math.pi))
            self.analyse()
            p[measure] = z0_dest
            p[P.ANG_L] = angl_dest
            p[P.PHYS_LEN] = (C0 / p[P.FREQUENCY] / math.sqrt(p[P.EPSILON_EFF])
                             * p[P.ANG_L] / (2.0 * math.pi))

        return error <= self._MAX_ERROR


# ---------------------------------------------------------------------------
# 微带线
# ---------------------------------------------------------------------------

class Microstrip(TranslineBase):
    """
    微带线（对应 MICROSTRIP）。

    模型：Hammerstad-Jensen 准静态 + 导体厚度/覆盖层修正 + Getsinger 色散
          + Pozar 介质损耗 + 趋肤效应导体损耗。
    参考：Hammerstad & Jensen, IEEE MTT-S Digest 1980；
          March, 11th European Microwave Conf. 1981（覆盖层修正）；
          Garg-Bahl-Bozzi, "Microstrip Lines and Slotlines" 4th ed. 2024。
    """

    _PARAMS = (
        P.EPSILONR, P.H_T, P.H, P.PHYS_WIDTH, P.T, P.Z0,
        P.FREQUENCY, P.EPSILON_EFF, P.SKIN_DEPTH, P.SIGMA, P.ROUGH,
        P.TAND, P.PHYS_LEN, P.MUR, P.MURC, P.ANG_L,
        P.UNIT_PROP_DELAY, P.ATTEN_COND, P.ATTEN_DILECTRIC,
        P.DIELECTRIC_MODEL_SEL, P.EPSILONR_SPEC_FREQ,
        P.SOLDERMASK_PRESENT, P.SOLDERMASK_THICKNESS,
        P.SOLDERMASK_EPSILONR, P.SOLDERMASK_TAND,
    )

    def __init__(self):
        super().__init__()
        # 准静态状态量
        self._z0_0 = 0.0        # 静态特性阻抗
        self._er_eff_0 = 0.0    # 静态有效介电常数
        self._mur_eff = 0.0     # 有效磁导率
        self._w_eff = 0.0       # 有效线宽
        self._z0_h_1 = 0.0      # 均匀介质（er=1）等效阻抗

    # -- 输出便捷属性 -----------------------------------------------------
    @property
    def z0(self):
        return self._p[P.Z0]

    @property
    def eps_eff(self):
        return self._p[P.EPSILON_EFF]

    @property
    def angle(self):
        return self._p[P.ANG_L]

    @property
    def width(self):
        return self._p[P.PHYS_WIDTH]

    @property
    def length(self):
        return self._p[P.PHYS_LEN]

    @property
    def attenuation_conductor_db(self):
        return self._p[P.ATTEN_COND]

    @property
    def attenuation_dielectric_db(self):
        return self._p[P.ATTEN_DILECTRIC]

    # -- 主流程 -----------------------------------------------------------
    def analyse(self):
        self._update_dielectric_model()

        f = self._p[P.FREQUENCY]
        raw_eps_r = self._p[P.EPSILONR]
        raw_tan_d = self._p[P.TAND]

        # 覆盖色散后的介质参数，供各子函数通过参数表读取
        self._p[P.EPSILONR] = self._dispersed_epsilon_r(f)
        self._p[P.TAND] = self._dispersed_tan_delta(f)

        self._mur_eff_ms()
        self._microstrip_z0()

        # 阻焊层修正（默认关闭时为逐位不变的空操作）
        er_eff_uncoated = self._er_eff_0
        dispersed_eps_r = self._p[P.EPSILONR]
        dispersed_tan_d = self._p[P.TAND]
        u_over_h = self._p[P.PHYS_WIDTH] / self._p[P.H]

        er_eff_coated, tan_d_coated = self._apply_soldermask_correction(
            er_eff_uncoated, dispersed_tan_d, dispersed_eps_r, u_over_h)

        if er_eff_coated != er_eff_uncoated:
            self._er_eff_0 = er_eff_coated
            self._z0_0 *= math.sqrt(er_eff_uncoated / er_eff_coated)
            self._p[P.Z0] = self._z0_0

        self._dispersion()
        self._line_angle()

        self._p[P.TAND] = tan_d_coated
        self._attenuation()

        self._p[P.EPSILONR] = raw_eps_r
        self._p[P.TAND] = raw_tan_d

    def synthesize(self, recalculate_length=False):
        p = self._p
        z0_dest = p[P.Z0]
        angl_dest = p[P.ANG_L]

        p[P.PHYS_WIDTH] = self._synthesize_width()

        if not self._minimise_z0_error_1d(P.PHYS_WIDTH, P.Z0):
            return False

        p[P.Z0] = z0_dest
        p[P.ANG_L] = angl_dest
        er_eff = p[P.EPSILON_EFF]
        p[P.PHYS_LEN] = (C0 / p[P.FREQUENCY] / math.sqrt(er_eff * self._mur_eff)
                         * p[P.ANG_L] / (2.0 * math.pi))
        self.analyse()

        p[P.Z0] = z0_dest
        p[P.ANG_L] = angl_dest
        p[P.PHYS_LEN] = (C0 / p[P.FREQUENCY] / math.sqrt(er_eff * self._mur_eff)
                         * p[P.ANG_L] / (2.0 * math.pi))
        return True

    def _set_analysis_results(self):
        p = self._p
        self._set_analysis_result(P.EPSILON_EFF, p[P.EPSILON_EFF])
        self._set_analysis_result(P.UNIT_PROP_DELAY, p[P.UNIT_PROP_DELAY])
        self._set_analysis_result(P.ATTEN_COND, p[P.ATTEN_COND])
        self._set_analysis_result(P.ATTEN_DILECTRIC, p[P.ATTEN_DILECTRIC])
        self._set_analysis_result(P.SKIN_DEPTH, p[P.SKIN_DEPTH])

        z0, ang_l = p[P.Z0], p[P.ANG_L]
        length, width = p[P.PHYS_LEN], p[P.PHYS_WIDTH]

        self._set_analysis_result(P.Z0, z0,
                                  STATUS.ERROR if (not math.isfinite(z0) or z0 < 0) else STATUS.OK)
        self._set_analysis_result(P.ANG_L, ang_l,
                                  STATUS.ERROR if (not math.isfinite(ang_l) or ang_l < 0) else STATUS.OK)
        self._set_analysis_result(P.PHYS_LEN, length,
                                  STATUS.WARNING if (not math.isfinite(length) or length < 0) else STATUS.OK)
        self._set_analysis_result(P.PHYS_WIDTH, width,
                                  STATUS.WARNING if (not math.isfinite(width) or width <= 0) else STATUS.OK)

    def _set_synthesis_results(self):
        p = self._p
        self._set_synthesis_result(P.EPSILON_EFF, p[P.EPSILON_EFF])
        self._set_synthesis_result(P.UNIT_PROP_DELAY, p[P.UNIT_PROP_DELAY])
        self._set_synthesis_result(P.ATTEN_COND, p[P.ATTEN_COND])
        self._set_synthesis_result(P.ATTEN_DILECTRIC, p[P.ATTEN_DILECTRIC])
        self._set_synthesis_result(P.SKIN_DEPTH, p[P.SKIN_DEPTH])

        z0, ang_l = p[P.Z0], p[P.ANG_L]
        length, width = p[P.PHYS_LEN], p[P.PHYS_WIDTH]

        self._set_synthesis_result(P.Z0, z0,
                                   STATUS.WARNING if (not math.isfinite(z0) or z0 < 0) else STATUS.OK)
        self._set_synthesis_result(P.ANG_L, ang_l,
                                   STATUS.WARNING if (not math.isfinite(ang_l) or ang_l < 0) else STATUS.OK)
        self._set_synthesis_result(P.PHYS_LEN, length,
                                   STATUS.ERROR if (not math.isfinite(length) or length < 0) else STATUS.OK)
        self._set_synthesis_result(P.PHYS_WIDTH, width,
                                   STATUS.ERROR if (not math.isfinite(width) or width <= 0) else STATUS.OK)

    def _get_soldermask_delta_q(self, w_over_h, c_over_h):
        if w_over_h <= 0.0 or c_over_h <= 0.0:
            return 0.0
        q2_coated = self._wan_hoorfar_q2(w_over_h, 1.0 + c_over_h)
        q2_base = self._wan_hoorfar_q2(w_over_h, 1.0)
        return max(0.0, q2_coated - q2_base)

    # -- 综合初值 ---------------------------------------------------------
    def _synthesize_width(self):
        """Hammerstad-Jensen 综合公式给出线宽初值，供牛顿迭代使用。"""
        e_r = self._p[P.EPSILONR]
        z0 = self._p[P.Z0]
        a = ((z0 / ZF0 / 2.0 / math.pi) * math.sqrt((e_r + 1.0) / 2.0)
             + ((e_r - 1.0) / (e_r + 1.0) * (0.23 + (0.11 / e_r))))
        b = ZF0 / 2.0 * math.pi / (z0 * math.sqrt(e_r))

        if a > 1.52:
            w_h = 8.0 * math.exp(a) / (math.exp(2.0 * a) - 2.0)
        else:
            w_h = ((2.0 / math.pi)
                   * (b - 1.0 - math.log((2.0 * b) - 1.0)
                      + ((e_r - 1.0) / (2.0 * e_r))
                      * (math.log(b - 1.0) + 0.39 - 0.61 / e_r)))

        h = self._p[P.H]
        return w_h * h if h > 0.0 else 0.0

    # -- 静态阻抗 ---------------------------------------------------------
    def _microstrip_z0(self):
        e_r = self._p[P.EPSILONR]
        h = self._p[P.H]
        h2h = self._p[P.H_T] / h
        u = self._p[P.PHYS_WIDTH] / h
        t_h = self._p[P.T] / h

        # er=1 的线宽修正 → 均匀介质等效阻抗
        delta_u_1 = self._delta_u_thickness(u, t_h, 1.0)
        self._z0_h_1 = self._z0_homogeneous(u + delta_u_1)

        # 实际 er 的线宽修正 → 均匀介质等效阻抗
        u += self._delta_u_thickness(u, t_h, e_r)
        z0_h_r = self._z0_homogeneous(u)

        q_inf = self._filling_factor(u, e_r)      # 填充因子（已含厚度修正后的宽度）
        q_c = self._delta_q_cover(h2h)            # 覆盖层影响
        q_t = self._delta_q_thickness(u, t_h)     # 厚度影响
        q = (q_inf - q_t) * q_c

        e_r_eff_t = self._e_r_effective(e_r, q)
        e_r_eff = e_r_eff_t * (self._z0_h_1 / z0_h_r) ** 2.0

        self._p[P.Z0] = (z0_h_r - self._delta_z0_cover(u, h2h)) / math.sqrt(e_r_eff_t)

        self._w_eff = u * h
        self._er_eff_0 = e_r_eff
        self._z0_0 = self._p[P.Z0]

    @staticmethod
    def _z0_homogeneous(u):
        """均匀介质（无覆盖、零厚度）微带线阻抗。"""
        freq = 6.0 + (2.0 * math.pi - 6.0) * math.exp(-((30.666 / u) ** 0.7528))
        return (ZF0 / (2.0 * math.pi)) * math.log(freq / u + math.sqrt(1.0 + 4.0 / (u * u)))

    @staticmethod
    def _delta_z0_cover(u, h2h):
        """
        March 1981 式 (1)：覆盖层对阻抗的修正。
        Bahl 1978 原文的 Q 有印刷错误（应为 1 - atanh(x)），此处用 March 修正形式。
        """
        h2hp1 = 1.0 + h2h
        p_coef = 270.0 * (1.0 - math.tanh(0.28 + 1.2 * math.sqrt(h2h)))

        q_arg = (0.48 * math.sqrt(max(0.0, u - 1.0))) / (h2hp1 * h2hp1)
        if q_arg >= 1.0:
            return p_coef

        return p_coef * (1.0 - math.atanh(q_arg))

    @staticmethod
    def _filling_factor(u, e_r):
        """无覆盖、零厚度微带的填充因子（Hammerstad-Jensen）。"""
        u2 = u * u
        u3 = u2 * u
        u4 = u3 * u
        a = (1.0 + math.log((u4 + u2 / 2704.0) / (u4 + 0.432)) / 49.0
             + math.log(1.0 + u3 / 5929.741) / 18.7)
        b = 0.564 * ((e_r - 0.9) / (e_r + 3.0)) ** 0.053
        return (1.0 + 10.0 / u) ** (-a * b)

    @staticmethod
    def _delta_q_cover(h2h):
        """March 1981 式 (7)：覆盖层对填充因子的修正。"""
        return math.tanh(1.043 + 0.121 * h2h - 1.164 / h2h)

    @staticmethod
    def _delta_q_thickness(u, t_h):
        return (2.0 * math.log(2.0) / math.pi) * (t_h / math.sqrt(u))

    @staticmethod
    def _e_r_effective(e_r, q):
        return 0.5 * (e_r + 1.0) + 0.5 * q * (e_r - 1.0)

    @staticmethod
    def _delta_u_thickness(u, t_h, e_r):
        """导体厚度对归一化线宽的修正（Hammerstad-Jensen）。"""
        if t_h <= 0.0:
            return 0.0
        delta_u = ((t_h / math.pi)
                   * math.log(1.0 + (4.0 * math.e) * math.tanh(math.sqrt(6.517 * u)) ** 2.0 / t_h))
        return 0.5 * delta_u * (1.0 + 1.0 / math.cosh(math.sqrt(e_r - 1.0)))

    # -- 色散 -------------------------------------------------------------
    def _dispersion(self):
        e_r = self._p[P.EPSILONR]
        e_r_eff_0 = self._er_eff_0
        u = self._p[P.PHYS_WIDTH] / self._p[P.H]

        f_n = self._p[P.FREQUENCY] * self._p[P.H] / 1e06    # 归一化频率 [GHz*mm]

        p_coef = self._e_r_dispersion(u, e_r, f_n)
        e_r_eff_f = e_r - (e_r - e_r_eff_0) / (1.0 + p_coef)

        d = self._z0_dispersion(u, e_r, e_r_eff_0, e_r_eff_f, f_n)
        z0_f = self._z0_0 * d

        self._p[P.UNIT_PROP_DELAY] = self._unit_propagation_delay(e_r_eff_f)
        self._p[P.EPSILON_EFF] = e_r_eff_f
        self._p[P.Z0] = z0_f

    @staticmethod
    def _e_r_dispersion(u, e_r, f_n):
        """Getsinger/Kirschning 色散修正因子（有效介电常数）。"""
        p_1 = (0.27488 + u * (0.6315 + 0.525 / (1.0 + 0.0157 * f_n) ** 20.0)
               - 0.065683 * math.exp(-8.7513 * u))
        p_2 = 0.33622 * (1.0 - math.exp(-0.03442 * e_r))
        p_3 = 0.0363 * math.exp(-4.6 * u) * (1.0 - math.exp(-((f_n / 38.7) ** 4.97)))
        p_4 = 1.0 + 2.751 * (1.0 - math.exp(-((e_r / 15.916) ** 8.0)))
        return p_1 * p_2 * ((p_3 * p_4 + 0.1844) * f_n) ** 1.5763

    @staticmethod
    def _z0_dispersion(u, e_r, e_r_eff_0, e_r_eff_f, f_n):
        """Getsinger 色散修正因子（特性阻抗）。"""
        r_1 = 0.03891 * e_r ** 1.4
        r_2 = 0.267 * u ** 7.0
        r_3 = 4.766 * math.exp(-3.228 * u ** 0.641)
        r_4 = 0.016 + (0.0514 * e_r) ** 4.524
        r_5 = (f_n / 28.843) ** 12.0
        r_6 = 22.2 * u ** 1.92
        r_7 = 1.206 - 0.3144 * math.exp(-r_1) * (1.0 - math.exp(-r_2))
        r_8 = 1.0 + 1.275 * (1.0 - math.exp(
            -0.004625 * r_3 * e_r ** 1.674 * (f_n / 18.365) ** 2.745))
        tmpf = (e_r - 1.0) ** 6.0
        r_9 = (5.086 * r_4 * (r_5 / (0.3838 + 0.386 * r_4))
               * (math.exp(-r_6) / (1.0 + 1.2992 * r_5))
               * (tmpf / (1.0 + 10.0 * tmpf)))
        r_10 = 0.00044 * e_r ** 2.136 + 0.0184
        tmpf = (f_n / 19.47) ** 6.0
        r_11 = tmpf / (1.0 + 0.0962 * tmpf)
        r_12 = 1.0 / (1.0 + 0.00245 * u * u)
        r_13 = 0.9408 * e_r_eff_f ** r_8 - 0.9603
        r_14 = (0.9408 - r_9) * e_r_eff_0 ** r_8 - 0.9603
        r_15 = 0.707 * r_10 * (f_n / 12.3) ** 1.097
        r_16 = 1.0 + 0.0503 * e_r * e_r * r_11 * (1.0 - math.exp(-((u / 15.0) ** 6.0)))
        r_17 = r_7 * (1.0 - 1.1241 * (r_12 / r_16)
                      * math.exp(-0.026 * f_n ** 1.15656 - r_15))
        return (r_13 / r_14) ** r_17

    # -- 损耗 -------------------------------------------------------------
    def _conductor_losses(self):
        """导体损耗（dB/m）。"""
        if self._p[P.FREQUENCY] <= 0.0:
            return 0.0

        e_r_eff_0 = self._er_eff_0
        delta = self._p[P.SKIN_DEPTH]

        # 电流分布因子
        k = math.exp(-1.2 * (self._z0_h_1 / ZF0) ** 0.7)
        # 表面电阻
        r_s = 1.0 / (self._p[P.SIGMA] * delta)
        # 表面粗糙度修正
        r_s *= 1.0 + ((2.0 / math.pi)
                      * math.atan(1.40 * ((self._p[P.ROUGH] / delta) ** 2.0)))
        # 导体品质因数
        q_c = (math.pi * self._z0_h_1 * self._p[P.PHYS_WIDTH] * self._p[P.FREQUENCY]) / (r_s * C0 * k)

        return ((20.0 * math.pi / math.log(10.0)) * self._p[P.FREQUENCY]
                * math.sqrt(e_r_eff_0) / (C0 * q_c))

    def _dielectric_losses(self):
        """介质损耗（dB/m），Pozar 形式。"""
        e_r = self._p[P.EPSILONR]
        e_r_eff_0 = self._er_eff_0

        if e_r == 1.0:
            # 均匀空气介质不存在介质损耗。KiCad 原版此处为 0/0 -> NaN，
            # 移植版返回物理正确的 0。
            return 0.0

        return ((20.0 * math.pi / math.log(10.0)) * (self._p[P.FREQUENCY] / C0)
                * (e_r / math.sqrt(e_r_eff_0))
                * ((e_r_eff_0 - 1.0) / (e_r - 1.0)) * self._p[P.TAND])

    def _attenuation(self):
        self._p[P.SKIN_DEPTH] = self._skin_depth()
        self._p[P.ATTEN_COND] = self._conductor_losses() * self._p[P.PHYS_LEN]
        self._p[P.ATTEN_DILECTRIC] = self._dielectric_losses() * self._p[P.PHYS_LEN]

    def _mur_eff_ms(self):
        """有效磁导率。"""
        mur = self._p[P.MUR]
        h = self._p[P.H]
        w = self._p[P.PHYS_WIDTH]
        self._mur_eff = (2.0 * mur) / ((1.0 + mur)
                                       + ((1.0 - mur) * (1.0 + (10.0 * h / w)) ** -0.5))

    def _line_angle(self):
        """由线长换算电长度（rad）。"""
        e_r_eff = self._p[P.EPSILON_EFF]
        v = C0 / math.sqrt(e_r_eff * self._mur_eff)
        lambda_g = v / self._p[P.FREQUENCY]
        self._p[P.ANG_L] = 2.0 * math.pi * self._p[P.PHYS_LEN] / lambda_g


# ---------------------------------------------------------------------------
# 带状线
# ---------------------------------------------------------------------------

class Stripline(TranslineBase):
    """
    带状线（对应 STRIPLINE）。

    模型：Cohn 公式的双子线并联（导体居中时两半对称），含导体厚度修正。
    有效介电常数等于基板 er（TEM、无色散）。
    参考：Cohn, "Characteristic Impedance of the Shielded-Strip Transmission
          Line", IRE Trans. MTT, 1954；厚度修正见 IPC-2141。
    """

    _PARAMS = (
        P.SKIN_DEPTH, P.EPSILON_EFF, P.EPSILONR, P.T, P.STRIPLINE_A,
        P.H, P.Z0, P.LOSS_CONDUCTOR, P.PHYS_LEN, P.LOSS_DIELECTRIC,
        P.FREQUENCY, P.TAND, P.PHYS_WIDTH, P.UNIT_PROP_DELAY, P.ANG_L,
        P.SIGMA, P.MURC, P.DIELECTRIC_MODEL_SEL, P.EPSILONR_SPEC_FREQ,
    )

    def __init__(self):
        super().__init__()
        self._unit_prop_delay = 0.0

    # -- 输出便捷属性 -----------------------------------------------------
    @property
    def z0(self):
        return self._p[P.Z0]

    @property
    def eps_eff(self):
        return self._p[P.EPSILON_EFF]

    @property
    def angle(self):
        return self._p[P.ANG_L]

    @property
    def width(self):
        return self._p[P.PHYS_WIDTH]

    @property
    def length(self):
        return self._p[P.PHYS_LEN]

    @property
    def loss_conductor_db(self):
        return self._p[P.LOSS_CONDUCTOR]

    @property
    def loss_dielectric_db(self):
        return self._p[P.LOSS_DIELECTRIC]

    # -- 主流程 -----------------------------------------------------------
    def analyse(self):
        self._update_dielectric_model()

        f = self._p[P.FREQUENCY]
        raw_eps_r = self._p[P.EPSILONR]
        raw_tan_d = self._p[P.TAND]

        self._p[P.EPSILONR] = self._dispersed_epsilon_r(f)
        self._p[P.TAND] = self._dispersed_tan_delta(f)

        self._p[P.SKIN_DEPTH] = self._skin_depth()
        self._p[P.EPSILON_EFF] = self._p[P.EPSILONR]     # 无色散

        t = self._p[P.T]
        a = self._p[P.STRIPLINE_A]
        h = self._p[P.H]

        zl_upper, ac1 = self._line_impedance(2.0 * a + t)
        zl_lower, ac2 = self._line_impedance(2.0 * (h - a) - t)

        # 上下两半并联
        self._p[P.Z0] = 2.0 / (1.0 / zl_upper + 1.0 / zl_lower)
        self._p[P.LOSS_CONDUCTOR] = self._p[P.PHYS_LEN] * (ac1 + ac2)
        self._p[P.LOSS_DIELECTRIC] = (LOG2DB * self._p[P.PHYS_LEN] * (math.pi / C0)
                                      * self._p[P.FREQUENCY] * math.sqrt(self._p[P.EPSILONR])
                                      * self._p[P.TAND])
        self._p[P.ANG_L] = (2.0 * math.pi * self._p[P.PHYS_LEN]
                            * math.sqrt(self._p[P.EPSILONR])
                            * self._p[P.FREQUENCY] / C0)
        self._unit_prop_delay = self._unit_propagation_delay(self._p[P.EPSILON_EFF])

        self._p[P.EPSILONR] = raw_eps_r
        self._p[P.TAND] = raw_tan_d

    def synthesize(self, recalculate_length=False):
        return self._minimise_z0_error_1d(P.PHYS_WIDTH, P.Z0)

    def _set_analysis_results(self):
        p = self._p
        self._set_analysis_result(P.EPSILON_EFF, p[P.EPSILON_EFF])
        self._set_analysis_result(P.UNIT_PROP_DELAY, self._unit_prop_delay)
        self._set_analysis_result(P.LOSS_CONDUCTOR, p[P.LOSS_CONDUCTOR])
        self._set_analysis_result(P.LOSS_DIELECTRIC, p[P.LOSS_DIELECTRIC])
        self._set_analysis_result(P.SKIN_DEPTH, p[P.SKIN_DEPTH])

        z0, ang_l = p[P.Z0], p[P.ANG_L]
        length, width = p[P.PHYS_LEN], p[P.PHYS_WIDTH]

        invalid = (p[P.STRIPLINE_A] + p[P.T]) >= p[P.H]

        self._set_analysis_result(P.Z0, z0,
                                  STATUS.ERROR if (not math.isfinite(z0) or z0 < 0 or invalid) else STATUS.OK)
        self._set_analysis_result(P.ANG_L, ang_l,
                                  STATUS.ERROR if (not math.isfinite(ang_l) or ang_l < 0) else STATUS.OK)
        self._set_analysis_result(P.PHYS_LEN, length,
                                  STATUS.WARNING if (not math.isfinite(length) or length < 0) else STATUS.OK)
        self._set_analysis_result(P.PHYS_WIDTH, width,
                                  STATUS.WARNING if (not math.isfinite(width) or width <= 0) else STATUS.OK)
        self._set_analysis_result(P.STRIPLINE_A, p[P.STRIPLINE_A],
                                  STATUS.WARNING if invalid else STATUS.OK)
        self._set_analysis_result(P.T, p[P.T], STATUS.WARNING if invalid else STATUS.OK)
        self._set_analysis_result(P.H, p[P.H], STATUS.WARNING if invalid else STATUS.OK)

    def _set_synthesis_results(self):
        p = self._p
        self._set_synthesis_result(P.EPSILON_EFF, p[P.EPSILON_EFF])
        self._set_synthesis_result(P.UNIT_PROP_DELAY, self._unit_prop_delay)
        self._set_synthesis_result(P.LOSS_CONDUCTOR, p[P.LOSS_CONDUCTOR])
        self._set_synthesis_result(P.LOSS_DIELECTRIC, p[P.LOSS_DIELECTRIC])
        self._set_synthesis_result(P.SKIN_DEPTH, p[P.SKIN_DEPTH])

        z0, ang_l = p[P.Z0], p[P.ANG_L]
        length, width = p[P.PHYS_LEN], p[P.PHYS_WIDTH]

        invalid = (p[P.STRIPLINE_A] + p[P.T]) >= p[P.H]

        self._set_synthesis_result(P.Z0, z0,
                                   STATUS.WARNING if (not math.isfinite(z0) or z0 < 0) else STATUS.OK)
        self._set_synthesis_result(P.ANG_L, ang_l,
                                   STATUS.WARNING if (not math.isfinite(ang_l) or ang_l < 0) else STATUS.OK)
        self._set_synthesis_result(P.PHYS_LEN, length,
                                   STATUS.ERROR if (not math.isfinite(length) or length < 0) else STATUS.OK)
        self._set_synthesis_result(P.PHYS_WIDTH, width,
                                   STATUS.ERROR if (not math.isfinite(width) or width <= 0 or invalid) else STATUS.OK)
        self._set_synthesis_result(P.STRIPLINE_A, p[P.STRIPLINE_A],
                                   STATUS.WARNING if invalid else STATUS.OK)
        self._set_synthesis_result(P.T, p[P.T], STATUS.WARNING if invalid else STATUS.OK)
        self._set_synthesis_result(P.H, p[P.H], STATUS.WARNING if invalid else STATUS.OK)

    def _line_impedance(self, a_height):
        """
        单侧（导体到一侧地平面）特性阻抗与导体损耗系数。
        返回 (ZL, a_ac)，其中 a_ac 为 dB/m 量纲的损耗因子。
        """
        p = self._p
        w = p[P.PHYS_WIDTH]
        t = p[P.T]
        eps_r = p[P.EPSILONR]
        hmt = a_height - t

        if hmt <= 0.0:
            # 非法几何：导体已越过该侧地平面（STRIPLINE_A + T >= H）。
            # KiCad 原式在此处由 log(负数/零) 得到 NaN；Python 的 math.log 会抛异常，
            # 故显式返回 NaN，交由 _set_analysis_results 标记为 ERROR。
            return math.nan, math.nan

        a_ac = math.sqrt(p[P.FREQUENCY] / p[P.SIGMA] / 17.2)

        if w / hmt >= 0.35:
            # 宽导体分支
            # t -> 0+ 时 a^2/hmt^2 - 1 = t(2a-t)/hmt^2，故 t*ln(...) = t*ln(t)+... -> 0。
            # KiCad 原式在此处为 0 * ln(0) = 0 * (-inf) = NaN，这里取极限值 0。
            edge_term = t * math.log(a_height * a_height / hmt / hmt - 1.0) if t > 0.0 else 0.0
            zl = w + (2.0 * a_height * math.log((2.0 * a_height - t) / hmt) - edge_term) / math.pi
            zl = ZF0 * hmt / math.sqrt(eps_r) / 4.0 / zl

            a_ac *= 2.02e-6 * eps_r * zl / hmt
            if t > 0.0:
                a_ac *= (1.0 + 2.0 * w / hmt
                         + (a_height + t) / hmt / math.pi * math.log(2.0 * a_height / t - 1.0))
            else:
                # t -> 0+ 时 ln(2a/t) 发散（边缘电流奇异）。退化为均匀电流极限，
                # 与窄导体分支对 t=0 的处理保持一致；KiCad 原式此处为 inf/NaN。
                a_ac *= 1.0 + 2.0 * w / hmt
        else:
            # 窄导体分支（厚度修正见 IPC-2141）
            if t > 0.0:
                tdw = t / w
                if t / w > 1.0:
                    tdw = w / t

                de = 1.0 + tdw / math.pi * (1.0 + math.log(4.0 * math.pi / tdw)) + 0.236 * tdw ** 1.65
                if t / w > 1.0:
                    de *= t / 2.0
                else:
                    de *= w / 2.0
            else:
                # t -> 0 的极限：tdw*ln(4*pi/tdw) -> 0，故 de = 1，即 de = w/2
                tdw = 0.0
                de = w / 2.0

            zl = ZF0 / 2.0 / math.pi / math.sqrt(eps_r) * math.log(4.0 * a_height / math.pi / de)

            a_ac *= 0.01141 / zl / de
            if t > 0.0:
                a_ac *= (de / a_height + 0.5 + tdw / 2.0 / math.pi
                         + 0.5 / math.pi * math.log(4.0 * math.pi / tdw)
                         + 0.1947 * tdw ** 0.65 - 0.0767 * tdw ** 1.65)
            else:
                a_ac *= de / a_height + 0.5

        return zl, a_ac


# ---------------------------------------------------------------------------
# 接地共面波导（GCPW / CBCPW）
# ---------------------------------------------------------------------------

class GCPW(TranslineBase):
    """
    接地共面波导（Grounded Coplanar Waveguide，即导体背衬共面波导 CBCPW）。
    对应 KiCad ``COPLANAR`` 中 ``CPW_BACKMETAL = 1`` 的分支。

    几何：中心导体宽 W、两侧槽宽 S、基板厚 H、导体厚 T，基板背面为连续地平面。

    模型：
      * 准静态保角变换，空气半平面比值 q1 = K(k1)/K(k1')，k1 = W/(W+2S)
      * 背面地平面比值 q3 = K(k3)/K(k3')，
        k3 = tanh(πW/4H) / tanh(π(W+2S)/4H)      (Ghione-Naldi 1983, eq. 8)
      * 有限厚度修正 d = (1.25T/π)(1 + ln(4πW/T))  (Gupta et al. 1996, eq. 7.98-7.100)
      * 有效介电常数色散（Gevorgian et al. 1997）
      * 导体损耗（Wheeler 增量电感律 → Ghione-Goano-Naldi 1996, eq. 13-14）

    与 KiCad 的差异：KiCad 的 COPLANAR 同时支持无背地（空气背衬）CPW，本移植按
    需求只保留 GCPW，故 k2 = sinh(πW/4H)/sinh(π(W+2S)/4H) 的空气背衬分支已省略。

    阻焊层修正的适用区间：修正量取自 Wan-Hoorfar 2000 的填充因子，其**宽导体分支
    （W/H ≥ 1）** 才给出随覆盖厚度单调递增的增量；窄导体分支（W/H < 1）基线相减后
    增量 ≤ 0，修正退化为空操作。这与 KiCad 行为一致（KiCad 官方阻焊测试也只覆盖
    W/H = 1.75 的宽导体）。典型 50 Ω GCPW 的 W/H 常小于 1，此时阻焊层影响不建模。
    """

    _PARAMS = (
        P.EPSILONR, P.TAND, P.SIGMA, P.MURC, P.PHYS_WIDTH, P.PHYS_S,
        P.H, P.T, P.PHYS_LEN, P.FREQUENCY, P.Z0, P.ANG_L,
        P.LOSS_CONDUCTOR, P.LOSS_DIELECTRIC, P.SKIN_DEPTH, P.EPSILON_EFF,
        P.UNIT_PROP_DELAY, P.DIELECTRIC_MODEL_SEL, P.EPSILONR_SPEC_FREQ,
        P.SOLDERMASK_PRESENT, P.SOLDERMASK_THICKNESS,
        P.SOLDERMASK_EPSILONR, P.SOLDERMASK_TAND, P.SOLDERMASK_FILLS_GAPS,
    )

    def __init__(self):
        super().__init__()
        self._synthesize_target = P.PHYS_WIDTH

    # -- 输出便捷属性 -----------------------------------------------------
    @property
    def z0(self):
        return self._p[P.Z0]

    @property
    def eps_eff(self):
        return self._p[P.EPSILON_EFF]

    @property
    def angle(self):
        return self._p[P.ANG_L]

    @property
    def width(self):
        return self._p[P.PHYS_WIDTH]

    @property
    def gap(self):
        return self._p[P.PHYS_S]

    @property
    def length(self):
        return self._p[P.PHYS_LEN]

    @property
    def loss_conductor_db(self):
        return self._p[P.LOSS_CONDUCTOR]

    @property
    def loss_dielectric_db(self):
        return self._p[P.LOSS_DIELECTRIC]

    def set_synthesize_target(self, target):
        """综合时反解哪个几何量：P.PHYS_WIDTH 或 P.PHYS_S（其它值忽略）。"""
        if target in (P.PHYS_WIDTH, P.PHYS_S):
            self._synthesize_target = target
        return self

    # -- 阻焊层填充因子（对应 COPLANAR::GetSoldermaskDeltaQ） --------------
    def _get_soldermask_delta_q(self, w_over_h, c_over_h):
        """
        增量填充因子。基准是微带（Wan-Hoorfar）的 q2 增量，再乘上半空间占比与槽覆盖因子。

        注意 Wan-Hoorfar 的 q2 只有**宽导体分支（w_over_h ≥ 1）** 随覆盖厚度单调递增；
        窄导体分支（w_over_h < 1）相减后为负，被 max(0, ·) 截断为 0，即修正不生效。
        详见类文档"阻焊层修正的适用区间"。
        """
        if w_over_h <= 0.0 or c_over_h <= 0.0:
            return 0.0

        # 以微带（Wan-Hoorfar）增量填充因子为基准
        microstrip_delta = max(0.0, self._wan_hoorfar_q2(w_over_h, 1.0 + c_over_h)
                               - self._wan_hoorfar_q2(w_over_h, 1.0))

        fills_gaps = self._p[P.SOLDERMASK_FILLS_GAPS] >= 0.5
        # GCPW 背面有地平面，电容更多落在基板侧，上半空间占比取 0.25
        # （无背地的对称 CPW 约为 0.5）
        half_space = 0.25
        # 槽内填满阻焊时整个上半空间边缘场都被覆盖；仅覆盖导体条时捕获比例更小
        slot_coverage = 1.0 if fills_gaps else 0.4
        return half_space * slot_coverage * microstrip_delta

    # -- 主流程 -----------------------------------------------------------
    def analyse(self):
        self._update_dielectric_model()

        p = self._p
        f = p[P.FREQUENCY]
        raw_eps_r = p[P.EPSILONR]
        raw_tan_d = p[P.TAND]

        p[P.SKIN_DEPTH] = self._skin_depth()

        W = p[P.PHYS_WIDTH]
        S = p[P.PHYS_S]
        H = p[P.H]
        T = p[P.T]
        epsr = self._dispersed_epsilon_r(f)
        length = p[P.PHYS_LEN]
        tand_substrate = self._dispersed_tan_delta(f)
        sigma = p[P.SIGMA]

        # --- 准静态保角变换 ---
        k1 = W / (W + S + S)
        kk1 = self._elliptic_integral(k1)[0]
        kpk1 = self._elliptic_integral(math.sqrt(1.0 - k1 * k1))[0]
        q1 = kk1 / kpk1

        k3 = (math.tanh((math.pi / 4.0) * (W / H))
              / math.tanh((math.pi / 4.0) * (W + S + S) / H))
        q3 = self._elliptic_ratio(k3)
        qz = 1.0 / (q1 + q3)
        er0 = 1.0 + q3 * qz * (epsr - 1.0)
        zl_factor = ZF0 / 2.0 * qz

        # --- 有限厚度修正 ---
        if T > 0.0:
            d = (T * 1.25 / math.pi) * (1.0 + math.log(4.0 * math.pi * W / T))
            se = S - d
            We = W + d
            ke = We / (We + se + se)
            qe = self._elliptic_ratio(ke)

            qz = 1.0 / (qe + q3)
            er0 = 1.0 + q3 * qz * (epsr - 1.0)
            zl_factor = ZF0 / 2.0 * qz

            er0 = er0 - (0.7 * (er0 - 1.0) * T / S) / (q1 + (0.7 * T / S))

        # --- 阻焊层（默认关闭时为逐位不变的空操作） ---
        tand_eff = tand_substrate
        u_over_h = (W / H) if H > 0.0 else 0.0
        er0_coated, tand_coated = self._apply_soldermask_correction(
            er0, tand_substrate, epsr, u_over_h)

        if er0_coated != er0:
            zl_factor *= math.sqrt(er0 / er0_coated)
            er0 = er0_coated
            tand_eff = tand_coated

        sr_er = math.sqrt(epsr)
        sr_er0 = math.sqrt(er0)

        # --- TE0 模截止频率与色散因子 G ---
        fte = (C0 / 4.0) / (H * math.sqrt(epsr - 1.0))

        log_w_h = math.log(W / H)
        uu = 0.54 - (0.64 - 0.015 * log_w_h) * log_w_h
        vv = 0.43 - (0.86 - 0.54 * log_w_h) * log_w_h
        gg = math.exp(uu * math.log(W / S) + vv)

        # --- 导体损耗（Wheeler 增量电感律，仅 T > 0 有效） ---
        ac = 0.0
        if T > 0.0:
            n = (1.0 - k1) * 8.0 * math.pi / (T * (1.0 + k1))
            a = W / 2.0
            b = a + S
            ac = (math.pi + math.log(n * a)) / a + (math.pi + math.log(n * b)) / b

        ac_factor = ac / (4.0 * ZF0 * kk1 * kpk1 * (1.0 - k1 * k1))
        ad_factor = (epsr / (epsr - 1.0)) * tand_eff * math.pi / C0

        # --- 有效介电常数色散 ---
        sr_er_f = sr_er0 + (sr_er - sr_er0) / (1.0 + gg * (f / fte) ** (-1.8))

        p[P.LOSS_CONDUCTOR] = (LOG2DB * length * ac_factor * sr_er0
                               * math.sqrt(math.pi * MU0 * f / sigma))
        p[P.LOSS_DIELECTRIC] = (LOG2DB * length * ad_factor * f
                                * (sr_er_f * sr_er_f - 1.0) / sr_er_f)
        p[P.ANG_L] = 2.0 * math.pi * length * sr_er_f * f / C0
        p[P.EPSILON_EFF] = sr_er_f * sr_er_f
        p[P.Z0] = zl_factor / sr_er_f
        p[P.UNIT_PROP_DELAY] = self._unit_propagation_delay(sr_er_f * sr_er_f)

        p[P.EPSILONR] = raw_eps_r
        p[P.TAND] = raw_tan_d

    def synthesize(self, recalculate_length=False):
        target = P.PHYS_S if self._synthesize_target == P.PHYS_S else P.PHYS_WIDTH
        return self._minimise_z0_error_1d(target, P.Z0, True)

    def _set_analysis_results(self):
        p = self._p
        self._set_analysis_result(P.EPSILON_EFF, p[P.EPSILON_EFF])
        self._set_analysis_result(P.UNIT_PROP_DELAY, p[P.UNIT_PROP_DELAY])
        self._set_analysis_result(P.LOSS_CONDUCTOR, p[P.LOSS_CONDUCTOR])
        self._set_analysis_result(P.LOSS_DIELECTRIC, p[P.LOSS_DIELECTRIC])
        self._set_analysis_result(P.SKIN_DEPTH, p[P.SKIN_DEPTH])

        z0, ang_l = p[P.Z0], p[P.ANG_L]
        length, w, s = p[P.PHYS_LEN], p[P.PHYS_WIDTH], p[P.PHYS_S]

        self._set_analysis_result(
            P.Z0, z0, STATUS.ERROR if (not math.isfinite(z0) or z0 < 0) else STATUS.OK)
        self._set_analysis_result(
            P.ANG_L, ang_l, STATUS.ERROR if (not math.isfinite(ang_l) or ang_l < 0) else STATUS.OK)
        self._set_analysis_result(
            P.PHYS_LEN, length, STATUS.WARNING if (not math.isfinite(length) or length < 0) else STATUS.OK)
        self._set_analysis_result(
            P.PHYS_WIDTH, w, STATUS.WARNING if (not math.isfinite(w) or w <= 0.0) else STATUS.OK)
        self._set_analysis_result(
            P.PHYS_S, s, STATUS.WARNING if (not math.isfinite(s) or s <= 0.0) else STATUS.OK)

    def _set_synthesis_results(self):
        p = self._p
        self._set_synthesis_result(P.EPSILON_EFF, p[P.EPSILON_EFF])
        self._set_synthesis_result(P.UNIT_PROP_DELAY, p[P.UNIT_PROP_DELAY])
        self._set_synthesis_result(P.LOSS_CONDUCTOR, p[P.LOSS_CONDUCTOR])
        self._set_synthesis_result(P.LOSS_DIELECTRIC, p[P.LOSS_DIELECTRIC])
        self._set_synthesis_result(P.SKIN_DEPTH, p[P.SKIN_DEPTH])

        z0, ang_l = p[P.Z0], p[P.ANG_L]
        length, w, s = p[P.PHYS_LEN], p[P.PHYS_WIDTH], p[P.PHYS_S]

        w_invalid = not math.isfinite(w) or w <= 0.0
        s_invalid = not math.isfinite(s) or s <= 0.0

        w_status = (STATUS.ERROR if w_invalid else STATUS.OK) \
            if self._synthesize_target == P.PHYS_WIDTH else \
            (STATUS.WARNING if w_invalid else STATUS.OK)
        s_status = (STATUS.ERROR if s_invalid else STATUS.OK) \
            if self._synthesize_target == P.PHYS_S else \
            (STATUS.WARNING if s_invalid else STATUS.OK)

        self._set_synthesis_result(
            P.Z0, z0, STATUS.WARNING if (not math.isfinite(z0) or z0 < 0) else STATUS.OK)
        self._set_synthesis_result(
            P.ANG_L, ang_l, STATUS.WARNING if (not math.isfinite(ang_l) or ang_l < 0) else STATUS.OK)
        self._set_synthesis_result(
            P.PHYS_LEN, length, STATUS.ERROR if (not math.isfinite(length) or length < 0) else STATUS.OK)
        self._set_synthesis_result(P.PHYS_WIDTH, w, w_status)
        self._set_synthesis_result(P.PHYS_S, s, s_status)


# ---------------------------------------------------------------------------
# 同轴线
# ---------------------------------------------------------------------------

class Coax(TranslineBase):
    """
    同轴线（TEM 模）。对应 KiCad ``COAX``。

    公式依据 Pozar, "Microwave Engineering", 4th ed., Wiley 2012：
    §2.2（TEM 同轴 Z0，Table 2.1）、§2.7（衰减）、§3.5（高阶 TE/TM 模截止）。

    注意：与 KiCad 一致，Z0 只含 √ε_r（TEM 波阻抗 η = η₀/√ε_r），而 λ_g 含
    √(ε_r·μ_r)。内导体 μ_c 只通过趋肤深度进入导体损耗。
    """

    _PARAMS = (
        P.SKIN_DEPTH, P.EPSILONR, P.TAND, P.PHYS_DIAM_IN, P.PHYS_DIAM_OUT,
        P.MUR, P.MURC, P.SIGMA, P.FREQUENCY, P.PHYS_LEN, P.Z0, P.ANG_L,
        P.LOSS_CONDUCTOR, P.LOSS_DIELECTRIC, P.CUTOFF_FREQUENCY,
        P.DIELECTRIC_MODEL_SEL, P.EPSILONR_SPEC_FREQ,
    )

    def __init__(self):
        super().__init__()
        self._synthesize_target = P.PHYS_DIAM_IN
        self._te_modes = []
        self._tm_modes = []

    # -- 输出便捷属性 -----------------------------------------------------
    @property
    def z0(self):
        return self._p[P.Z0]

    @property
    def angle(self):
        return self._p[P.ANG_L]

    @property
    def length(self):
        return self._p[P.PHYS_LEN]

    @property
    def d_in(self):
        return self._p[P.PHYS_DIAM_IN]

    @property
    def d_out(self):
        return self._p[P.PHYS_DIAM_OUT]

    @property
    def cutoff_frequency(self):
        return self._p[P.CUTOFF_FREQUENCY]

    @property
    def loss_conductor_db(self):
        return self._p[P.LOSS_CONDUCTOR]

    @property
    def loss_dielectric_db(self):
        return self._p[P.LOSS_DIELECTRIC]

    @property
    def te_modes(self):
        """当前频率下已传播的 TE_1m 模，如 ['H(1,1)', 'H(1,2)']。"""
        return list(self._te_modes)

    @property
    def tm_modes(self):
        """当前频率下已传播的 TM_0m 模，如 ['E(0,1)']。"""
        return list(self._tm_modes)

    def set_synthesize_target(self, target):
        """综合时反解哪个直径：P.PHYS_DIAM_IN 或 P.PHYS_DIAM_OUT（其它值忽略）。"""
        if target in (P.PHYS_DIAM_IN, P.PHYS_DIAM_OUT):
            self._synthesize_target = target
        return self

    # -- 单位长度损耗（dB/m） ---------------------------------------------
    def _alpha_d(self):
        """均匀 TEM 介质损耗 α_d = π f √ε_r tanδ / c（Pozar §2.7）。"""
        ad = ((math.pi / C0) * self._p[P.FREQUENCY]
              * math.sqrt(self._p[P.EPSILONR]) * self._p[P.TAND])
        return ad * LOG2DB

    def _alpha_c(self):
        """
        导体损耗 α_c = (Rs/(2η))·(1/a + 1/b)/ln(b/a)。
        由 R = (Rs/2π)(1/a + 1/b)（Pozar Table 2.1）除以 2Z0 得到，下面用直径表示。
        """
        rs = math.sqrt(math.pi * self._p[P.FREQUENCY] * self._p[P.MURC]
                       * MU0 / self._p[P.SIGMA])

        d_in = self._p[P.PHYS_DIAM_IN]
        d_out = self._p[P.PHYS_DIAM_OUT]

        ac = (math.sqrt(self._p[P.EPSILONR]) * ((1.0 / d_in) + (1.0 / d_out))
              / math.log(d_out / d_in) * (rs / ZF0))
        return ac * LOG2DB

    # -- 高阶模截止（对应 COAX::UpdateModeCutoffs） ------------------------
    def _update_mode_cutoffs(self):
        p = self._p
        epsr = p[P.EPSILONR]
        mur = p[P.MUR]
        d_in = p[P.PHYS_DIAM_IN]
        d_out = p[P.PHYS_DIAM_OUT]
        freq = p[P.FREQUENCY]
        sqrt_mu_er = math.sqrt(epsr * mur)

        fc_te11 = (2.0 * C0) / (math.pi * sqrt_mu_er * (d_in + d_out))
        fc_step = C0 / (sqrt_mu_er * (d_out - d_in))

        p[P.CUTOFF_FREQUENCY] = fc_te11

        self._te_modes = []
        self._tm_modes = []

        if fc_te11 <= freq:
            self._te_modes.append("H(1,1)")
            for m in range(2, 10):
                if fc_te11 + (m - 1) * fc_step > freq:
                    break
                self._te_modes.append(f"H(1,{m})")

        for m in range(1, 10):
            if m * fc_step > freq:
                break
            self._tm_modes.append(f"E(0,{m})")

    # -- 主流程 -----------------------------------------------------------
    def analyse(self):
        self._update_dielectric_model()

        p = self._p
        freq = p[P.FREQUENCY]
        raw_eps_r = p[P.EPSILONR]
        raw_tan_d = p[P.TAND]

        p[P.EPSILONR] = self._dispersed_epsilon_r(freq)
        p[P.TAND] = self._dispersed_tan_delta(freq)

        p[P.SKIN_DEPTH] = self._skin_depth()

        d_in = p[P.PHYS_DIAM_IN]
        d_out = p[P.PHYS_DIAM_OUT]
        epsr = p[P.EPSILONR]
        mur = p[P.MUR]
        length = p[P.PHYS_LEN]

        # Z0 = η·ln(b/a)/(2π)，η = η₀/√ε_r（Pozar eq. 2.32）
        p[P.Z0] = (ZF0 / (2.0 * math.pi * math.sqrt(epsr))) * math.log(d_out / d_in)

        lambda_g = (C0 / freq) / math.sqrt(epsr * mur)

        p[P.ANG_L] = (2.0 * math.pi * length) / lambda_g
        p[P.LOSS_DIELECTRIC] = self._alpha_d() * length
        p[P.LOSS_CONDUCTOR] = self._alpha_c() * length

        self._update_mode_cutoffs()

        p[P.EPSILONR] = raw_eps_r
        p[P.TAND] = raw_tan_d

    def synthesize(self, recalculate_length=False):
        self._update_dielectric_model()

        p = self._p
        mur = p[P.MUR]
        freq = p[P.FREQUENCY]
        z0_dest = p[P.Z0]
        angl_dest = p[P.ANG_L]
        epsr = self._dispersed_epsilon_r(freq)

        k = z0_dest * math.sqrt(epsr) / ZF0 * 2.0 * math.pi

        if self._synthesize_target == P.PHYS_DIAM_IN:
            p[P.PHYS_DIAM_IN] = p[P.PHYS_DIAM_OUT] / math.exp(k)
        elif self._synthesize_target == P.PHYS_DIAM_OUT:
            p[P.PHYS_DIAM_OUT] = p[P.PHYS_DIAM_IN] * math.exp(k)

        lambda_g = (C0 / freq) / math.sqrt(epsr * mur)
        p[P.PHYS_LEN] = (lambda_g * angl_dest) / (2.0 * math.pi)

        self.analyse()

        p[P.Z0] = z0_dest
        p[P.ANG_L] = angl_dest
        return True

    def _set_analysis_results(self):
        p = self._p
        self._set_analysis_result(P.LOSS_CONDUCTOR, p[P.LOSS_CONDUCTOR])
        self._set_analysis_result(P.LOSS_DIELECTRIC, p[P.LOSS_DIELECTRIC])
        self._set_analysis_result(P.EPSILONR, p[P.EPSILONR])
        self._set_analysis_result(P.SKIN_DEPTH, p[P.SKIN_DEPTH])
        self._set_analysis_result(P.CUTOFF_FREQUENCY, p[P.CUTOFF_FREQUENCY])

        z0, ang_l = p[P.Z0], p[P.ANG_L]
        length = p[P.PHYS_LEN]
        d_in, d_out = p[P.PHYS_DIAM_IN], p[P.PHYS_DIAM_OUT]

        geometry_invalid = d_in >= d_out

        self._set_analysis_result(
            P.Z0, z0, STATUS.ERROR if (not math.isfinite(z0) or z0 < 0) else STATUS.OK)
        self._set_analysis_result(
            P.ANG_L, ang_l, STATUS.ERROR if (not math.isfinite(ang_l) or ang_l < 0) else STATUS.OK)
        self._set_analysis_result(
            P.PHYS_LEN, length, STATUS.WARNING if (not math.isfinite(length) or length < 0) else STATUS.OK)
        self._set_analysis_result(
            P.PHYS_DIAM_IN, d_in,
            STATUS.WARNING if (not math.isfinite(d_in) or d_in <= 0.0 or geometry_invalid) else STATUS.OK)
        self._set_analysis_result(
            P.PHYS_DIAM_OUT, d_out,
            STATUS.WARNING if (not math.isfinite(d_out) or d_out <= 0.0 or geometry_invalid) else STATUS.OK)

    def _set_synthesis_results(self):
        p = self._p
        self._set_synthesis_result(P.LOSS_CONDUCTOR, p[P.LOSS_CONDUCTOR])
        self._set_synthesis_result(P.LOSS_DIELECTRIC, p[P.LOSS_DIELECTRIC])
        self._set_synthesis_result(P.EPSILONR, p[P.EPSILONR])
        self._set_synthesis_result(P.SKIN_DEPTH, p[P.SKIN_DEPTH])
        self._set_synthesis_result(P.CUTOFF_FREQUENCY, p[P.CUTOFF_FREQUENCY])

        z0, ang_l = p[P.Z0], p[P.ANG_L]
        length = p[P.PHYS_LEN]
        d_in, d_out = p[P.PHYS_DIAM_IN], p[P.PHYS_DIAM_OUT]

        d_in_invalid = not math.isfinite(d_in) or d_in <= 0.0
        d_out_invalid = not math.isfinite(d_out) or d_out <= 0.0
        geometry_invalid = d_in >= d_out

        def _status(invalid, target):
            if geometry_invalid:
                return STATUS.ERROR if self._synthesize_target == target else STATUS.WARNING
            return STATUS.ERROR if invalid else STATUS.OK

        self._set_synthesis_result(
            P.Z0, z0, STATUS.WARNING if (not math.isfinite(z0) or z0 < 0) else STATUS.OK)
        self._set_synthesis_result(
            P.ANG_L, ang_l, STATUS.WARNING if (not math.isfinite(ang_l) or ang_l < 0) else STATUS.OK)
        self._set_synthesis_result(
            P.PHYS_LEN, length, STATUS.ERROR if (not math.isfinite(length) or length < 0) else STATUS.OK)
        self._set_synthesis_result(P.PHYS_DIAM_IN, d_in,
                                   _status(d_in_invalid, P.PHYS_DIAM_IN))
        self._set_synthesis_result(P.PHYS_DIAM_OUT, d_out,
                                   _status(d_out_invalid, P.PHYS_DIAM_OUT))


# ---------------------------------------------------------------------------
# 耦合微带线 / 差分对
# ---------------------------------------------------------------------------

class CoupledMicrostrip(TranslineBase):
    """
    耦合微带线（差分对，对应 COUPLED_MICROSTRIP）。

    模型：Kirschning-Jansen 1984 全频段闭式
          （IEEE Trans. MTT 32(1):83-90，勘误 MTT 33(3):288）；
          奇偶模静态阻抗用 Kirschning-Jansen 1984 式 (1)-(13)，
          色散用同文 Q_0..Q_29 修正链；
          导体厚度用 Jansen 1978 增量线宽修正；
          覆盖层用 March 1981 修正；损耗用 Wheeler 增量电感律 + Pozar 介质损耗。
    参考：Garg-Bahl-Bozzi, "Microstrip Lines and Slotlines" 4th ed. 2024, ch. 5。

    几何：两条等宽 W、间距 S 的导体，基板厚 H、导体厚 T。
          "间距" S 指两条导体的**边到边**净间距（与 KiCad 的 PHYS_S 一致）。

    输出：奇模阻抗 Z0_O、偶模阻抗 Z0_E、差分阻抗 Z_DIFF = 2·Z0_O、
          共模阻抗 Z_COMM = Z0_E/2、耦合系数 k = (Z0e-Z0o)/(Z0e+Z0o)。

    两模阻抗随间距 S 的走向（耦合带状线同理，是这一族结构的共性）：
    偶模时两导体同电位，间隙中点是磁壁（dV/dx = 0），S → 0 时半域问题退化为
    "宽 2W 的导体、磁壁紧贴其边缘"，即合并后 2W 导体的一半，故
    C_even(S→0) = C_single(2W)/2，即 Z0_E(S→0) = 2·Z0_single(2W)；
    S → ∞ 时两线解耦，Z0_E → Z0_single(W)。所以 **Z0_E 随间距单调下降**，
    从 2·Z0_single(2W) 降到 Z0_single(W)（"耦合越紧、偶模阻抗越高"正是这一侧）。
    奇模时间隙中点是电壁（V = 0），S → 0 时导体边缘紧贴地，Z0_O 对数趋零；
    S → ∞ 时同样趋于 Z0_single(W)，故 **Z0_O 随间距单调上升**。
    两个极限都可用独立单线模型核对，verify_transline.py 的 E2/E7 就是这么钉的。

    前置条件：H > 0、PHYS_WIDTH > 0、PHYS_S > 0、H_T > 0（覆盖层高度，
    取极大值表示无覆盖）、SIGMA > 0、MURC > 0。退化几何会得到 NaN 并由
    ``analysis_results`` 标记为 ERROR，而不是抛异常。

    覆盖层修正的适用域：Kirschning-Jansen 的静态式（含覆盖项）拟合于
    0.1 ≤ u ≤ 10、0.1 ≤ g ≤ 4；覆盖项本身沿用 March 1981，要求 h2h ≥ 1
    （KiCad 官方用例 H_T = h 即 h2h = 1 处 eps_eff 恰为 (er+1)/2，对应
    q_c = 0）。h2h < 1 时原式给出的 q_c 为负、Z0_E 会掉到零以下，属于
    域外输入；g > 4 时偶模支越过 x = 10^(0.103g-0.159) 的极点，奇模支的
    G = 2.178 - 0.796g 把修正项推到无界的 540·f_o。两处都已按下述方式收敛：
    偶模越过极点取原式的连续极限 0；奇模以单根线的覆盖修正为上界（g → ∞
    时两线解耦，奇模退化为孤立单线）。
    """

    _PARAMS = (
        P.Z0_E, P.Z0_O, P.Z_DIFF, P.Z_COMM, P.COUPLING_K, P.ANG_L,
        P.PHYS_WIDTH, P.PHYS_LEN, P.PHYS_S, P.H, P.T, P.H_T, P.FREQUENCY,
        P.EPSILONR, P.EPSILON_EFF_EVEN, P.EPSILON_EFF_ODD,
        P.UNIT_PROP_DELAY_EVEN, P.UNIT_PROP_DELAY_ODD,
        P.ATTEN_COND_EVEN, P.ATTEN_COND_ODD,
        P.ATTEN_DILECTRIC_EVEN, P.ATTEN_DILECTRIC_ODD,
        P.SKIN_DEPTH, P.MURC, P.SIGMA, P.ROUGH, P.TAND,
        P.DIELECTRIC_MODEL_SEL, P.EPSILONR_SPEC_FREQ,
        P.SOLDERMASK_PRESENT, P.SOLDERMASK_THICKNESS,
        P.SOLDERMASK_EPSILONR, P.SOLDERMASK_TAND,
    )

    def __init__(self):
        super().__init__()
        # 单线辅助计算器（对应 KiCad 的 m_aux_microstrip）
        self._aux = Microstrip()
        self._w_t_e = 0.0          # 厚度修正后的偶模等效线宽
        self._w_t_o = 0.0          # 厚度修正后的奇模等效线宽
        self._er_eff_e_0 = 0.0     # 静态偶模有效介电常数
        self._er_eff_o_0 = 0.0     # 静态奇模有效介电常数
        self._z0_e_0 = 0.0         # 静态偶模阻抗
        self._z0_o_0 = 0.0         # 静态奇模阻抗
        self._er_eff_e = 0.0       # 色散后偶模有效介电常数
        self._er_eff_o = 0.0       # 色散后奇模有效介电常数
        self._prop_delay_e = 0.0
        self._prop_delay_o = 0.0
        self._atten_cond_e = 0.0
        self._atten_cond_o = 0.0
        self._atten_dielectric_e = 0.0
        self._atten_dielectric_o = 0.0
        self._ang_l_e = 0.0
        self._ang_l_o = 0.0

    # -- 输出便捷属性 -----------------------------------------------------
    @property
    def z0_even(self):
        return self._p[P.Z0_E]

    @property
    def z0_odd(self):
        return self._p[P.Z0_O]

    @property
    def z_diff(self):
        return self._p[P.Z_DIFF]

    @property
    def z_comm(self):
        return self._p[P.Z_COMM]

    @property
    def coupling_k(self):
        return self._p[P.COUPLING_K]

    @property
    def eps_eff_even(self):
        return self._p[P.EPSILON_EFF_EVEN]

    @property
    def eps_eff_odd(self):
        return self._p[P.EPSILON_EFF_ODD]

    @property
    def width(self):
        return self._p[P.PHYS_WIDTH]

    @property
    def spacing(self):
        return self._p[P.PHYS_S]

    @property
    def length(self):
        return self._p[P.PHYS_LEN]

    @property
    def angle(self):
        return self._p[P.ANG_L]

    # -- 主流程 -----------------------------------------------------------
    def analyse(self):
        self._update_dielectric_model()

        p = self._p
        f = p[P.FREQUENCY]
        raw_eps_r = p[P.EPSILONR]
        raw_tan_d = p[P.TAND]

        # 覆盖色散后的介质参数，供各子函数通过参数表读取
        p[P.EPSILONR] = self._dispersed_epsilon_r(f)
        p[P.TAND] = self._dispersed_tan_delta(f)

        if not self._geometry_is_usable():
            self._mark_degenerate()
            p[P.EPSILONR] = raw_eps_r
            p[P.TAND] = raw_tan_d
            return

        self._delta_u_thickness()
        self._er_eff_static()
        self._z0_even_odd()

        # 阻焊层修正：奇偶模各自套用同一增量填充因子，阻抗按 sqrt(未涂/涂)
        # 缩放，保持均匀介质参考阻抗与新 eps_eff 自洽。
        dispersed_eps_r = p[P.EPSILONR]
        dispersed_tan_d = p[P.TAND]
        u_over_h = p[P.PHYS_WIDTH] / p[P.H]

        er_e_coated, tan_d_e_coated = self._apply_soldermask_correction(
            self._er_eff_e_0, dispersed_tan_d, dispersed_eps_r, u_over_h)
        er_o_coated, tan_d_o_coated = self._apply_soldermask_correction(
            self._er_eff_o_0, dispersed_tan_d, dispersed_eps_r, u_over_h)

        if er_e_coated != self._er_eff_e_0:
            self._z0_e_0 *= math.sqrt(self._er_eff_e_0 / er_e_coated)
            self._er_eff_e_0 = er_e_coated

        if er_o_coated != self._er_eff_o_0:
            self._z0_o_0 *= math.sqrt(self._er_eff_o_0 / er_o_coated)
            self._er_eff_o_0 = er_o_coated

        self._er_eff_freq()
        self._z0_dispersion()

        # 损耗用两模混合后的 tanδ（奇偶模只差 eps_eff，取平均避免偏向任一模式）
        p[P.TAND] = 0.5 * (tan_d_e_coated + tan_d_o_coated)

        self._attenuation()
        self._line_angle()
        self._diff_impedance()
        self._common_mode_impedance()

        p[P.EPSILON_EFF_EVEN] = self._er_eff_e
        p[P.EPSILON_EFF_ODD] = self._er_eff_o
        p[P.UNIT_PROP_DELAY_EVEN] = self._prop_delay_e
        p[P.UNIT_PROP_DELAY_ODD] = self._prop_delay_o
        p[P.ATTEN_COND_EVEN] = self._atten_cond_e
        p[P.ATTEN_COND_ODD] = self._atten_cond_o
        p[P.ATTEN_DILECTRIC_EVEN] = self._atten_dielectric_e
        p[P.ATTEN_DILECTRIC_ODD] = self._atten_dielectric_o
        p[P.ANG_L] = math.sqrt(max(0.0, self._ang_l_e) * max(0.0, self._ang_l_o))

        p[P.EPSILONR] = raw_eps_r
        p[P.TAND] = raw_tan_d

    def synthesize(self, opts=SYNTH.DEFAULT, recalculate_length=False):
        """
        综合几何。opts：

            SYNTH.DEFAULT          二维牛顿联立求解 (W, S) 以匹配 (Z0_E, Z0_O)
            SYNTH.FROM_ZDIFF_ZCOMM 以 (Z_DIFF, Z_COMM) 为目标（先换算成 Z0_O/Z0_E）
            SYNTH.FIX_WIDTH        固定线宽 W，反解间距 S 以匹配 Z0_O
            SYNTH.FIX_SPACING      固定间距 S，反解线宽 W 以匹配 Z0_O

        KiCad 的 COUPLED_MICROSTRIP::Synthesize 只实现后两种；FROM_ZDIFF_ZCOMM
        是本实现按 CoupledStripline 的同一语义补的，换算关系与那边逐字一致。
        """
        p = self._p

        if opts == SYNTH.FROM_ZDIFF_ZCOMM:
            z_diff_target = p[P.Z_DIFF]
            z_comm_target = p[P.Z_COMM]

            if not (z_diff_target > 0.0) or not (z_comm_target > 0.0):
                return False

            p[P.Z0_O] = 0.5 * z_diff_target
            p[P.Z0_E] = 2.0 * z_comm_target

        if opts == SYNTH.FIX_WIDTH:
            return self._minimise_z0_error_1d(P.PHYS_S, P.Z0_O)

        if opts == SYNTH.FIX_SPACING:
            return self._minimise_z0_error_1d(P.PHYS_WIDTH, P.Z0_O)

        z0_e_target = p[P.Z0_E]
        z0_o_target = p[P.Z0_O]
        ang_l_dest = p[P.ANG_L]

        # 初值（Akhtarzad / Hinton 的合成公式）
        self._synth_width()

        h = p[P.H]
        w_h = _div(p[P.PHYS_WIDTH], h)
        s_h = _div(p[P.PHYS_S], h)

        eps = 1e-4
        f1 = f2 = 0.0
        err = math.inf
        iters = 0

        while iters < 250:
            iters += 1

            # 数值雅可比
            ft1, ft2 = self._syn_fun(s_h + eps, w_h, z0_e_target, z0_o_target)
            j11 = (ft1 - f1) / eps
            j21 = (ft2 - f2) / eps
            ft1, ft2 = self._syn_fun(s_h, w_h + eps, z0_e_target, z0_o_target)
            j12 = (ft1 - f1) / eps
            j22 = (ft2 - f2) / eps

            det = j11 * j22 - j21 * j12
            if det == 0.0 or not math.isfinite(det):
                return False

            s_h += (-f1 * j22 + f2 * j12) / det
            w_h += (-f2 * j11 + f1 * j21) / det

            f1, f2 = self._syn_fun(s_h, w_h, z0_e_target, z0_o_target)
            err = math.sqrt(f1 * f1 + f2 * f2)

            if not math.isfinite(err) or err <= 1e-4:
                break

        if not math.isfinite(err) or err > 1e-4:
            return False

        p[P.PHYS_S] = s_h * h
        p[P.PHYS_WIDTH] = w_h * h

        # 线长取奇偶模几何平均（两模相速不同）
        le = _div(C0, p[P.FREQUENCY] * math.sqrt(self._er_eff_e)) * ang_l_dest / (2.0 * math.pi)
        lo = _div(C0, p[P.FREQUENCY] * math.sqrt(self._er_eff_o)) * ang_l_dest / (2.0 * math.pi)
        p[P.PHYS_LEN] = math.sqrt(le * lo)

        self.analyse()

        p[P.ANG_L] = ang_l_dest
        p[P.Z0_E] = z0_e_target
        p[P.Z0_O] = z0_o_target
        return True

    # -- 结果表 -----------------------------------------------------------
    def _publish_results(self, sink, impedance_failure, geometry_failure):
        p = self._p
        write = sink

        def checked(param, value, positive, failure):
            invalid = (not math.isfinite(value)) or (value <= 0.0 if positive else value < 0.0)
            write(param, value, failure if invalid else STATUS.OK)

        write(P.EPSILON_EFF_EVEN, self._er_eff_e)
        write(P.EPSILON_EFF_ODD, self._er_eff_o)
        write(P.UNIT_PROP_DELAY_EVEN, self._prop_delay_e)
        write(P.UNIT_PROP_DELAY_ODD, self._prop_delay_o)
        write(P.ATTEN_COND_EVEN, self._atten_cond_e)
        write(P.ATTEN_COND_ODD, self._atten_cond_o)
        write(P.ATTEN_DILECTRIC_EVEN, self._atten_dielectric_e)
        write(P.ATTEN_DILECTRIC_ODD, self._atten_dielectric_o)
        write(P.SKIN_DEPTH, p[P.SKIN_DEPTH])
        write(P.Z_DIFF, p[P.Z_DIFF])
        write(P.Z_COMM, p[P.Z_COMM])
        write(P.COUPLING_K, p[P.COUPLING_K])
        checked(P.Z0_E, p[P.Z0_E], True, impedance_failure)
        checked(P.Z0_O, p[P.Z0_O], True, impedance_failure)
        checked(P.ANG_L, p[P.ANG_L], False, impedance_failure)
        checked(P.PHYS_WIDTH, p[P.PHYS_WIDTH], True, geometry_failure)
        checked(P.PHYS_LEN, p[P.PHYS_LEN], False, geometry_failure)
        checked(P.PHYS_S, p[P.PHYS_S], True, geometry_failure)

    def _set_analysis_results(self):
        # 分析：模式阻抗非法是硬错误，几何非法只是警告（几何是用户给的）
        self._publish_results(self._set_analysis_result, STATUS.ERROR, STATUS.WARNING)

    def _set_synthesis_results(self):
        # 综合：几何是求解器输出，非法为硬错误；目标阻抗降级为警告
        self._publish_results(self._set_synthesis_result, STATUS.WARNING, STATUS.ERROR)

    def _get_soldermask_delta_q(self, w_over_h, c_over_h):
        """与微带线相同的 Wan-Hoorfar 增量填充因子（忽略槽间耦合的二阶修正）。"""
        if w_over_h <= 0.0 or c_over_h <= 0.0:
            return 0.0
        q2_coated = self._wan_hoorfar_q2(w_over_h, 1.0 + c_over_h)
        q2_base = self._wan_hoorfar_q2(w_over_h, 1.0)
        return max(0.0, q2_coated - q2_base)

    # -- 退化几何 ---------------------------------------------------------
    def _geometry_is_usable(self):
        p = self._p
        for key in (P.H, P.PHYS_WIDTH, P.PHYS_S, P.H_T, P.EPSILONR):
            value = p[key]
            if not math.isfinite(value) or value <= 0.0:
                return False
        return True

    def _mark_degenerate(self):
        p = self._p
        p[P.Z0_E] = p[P.Z0_O] = p[P.Z_DIFF] = math.nan
        p[P.Z_COMM] = 0.0
        p[P.COUPLING_K] = 0.0
        self._er_eff_e = self._er_eff_o = math.nan
        self._er_eff_e_0 = self._er_eff_o_0 = math.nan
        self._z0_e_0 = self._z0_o_0 = math.nan
        self._prop_delay_e = self._prop_delay_o = math.nan
        self._atten_cond_e = self._atten_cond_o = 0.0
        self._atten_dielectric_e = self._atten_dielectric_o = 0.0
        self._ang_l_e = self._ang_l_o = 0.0
        p[P.EPSILON_EFF_EVEN] = p[P.EPSILON_EFF_ODD] = math.nan
        p[P.UNIT_PROP_DELAY_EVEN] = p[P.UNIT_PROP_DELAY_ODD] = math.nan
        p[P.ATTEN_COND_EVEN] = p[P.ATTEN_COND_ODD] = 0.0
        p[P.ATTEN_DILECTRIC_EVEN] = p[P.ATTEN_DILECTRIC_ODD] = 0.0
        p[P.ANG_L] = 0.0

    # -- 厚度修正（Jansen 1978） -----------------------------------------
    @staticmethod
    def _delta_u_thickness_single(u, t_h):
        """单根微带的厚度增量线宽（Atwater 1989）。"""
        if t_h <= 0.0:
            return 0.0
        denom = 1.0 + math.exp(-100.0 * (u - 1.0 / (2.0 * math.pi)))
        return (1.25 * t_h / math.pi) * (
            1.0 + math.log((2.0 + (4.0 * math.pi * u - 2.0) / denom) / t_h))

    def _delta_u_thickness(self):
        p = self._p
        e_r = p[P.EPSILONR]
        h = p[P.H]
        u = p[P.PHYS_WIDTH] / h
        g = p[P.PHYS_S] / h
        t_h = p[P.T] / h

        if t_h > 0.0:
            delta_u = self._delta_u_thickness_single(u, t_h)
            delta_t = _div(t_h, g * e_r)
            delta_u_e = delta_u * (1.0 - 0.5 * math.exp(-_div(0.69 * delta_u, delta_t)))
            delta_u_o = delta_u_e + delta_t
        else:
            delta_u_e = delta_u_o = 0.0

        self._w_t_e = p[P.PHYS_WIDTH] + delta_u_e * h
        self._w_t_o = p[P.PHYS_WIDTH] + delta_u_o * h

    def _compute_single_line(self):
        """为单线计算准备参数并跑一次微带静态 + 色散（对应 compute_single_line）。"""
        aux = self._aux
        p = self._p
        aux.set(P.EPSILONR, p[P.EPSILONR])
        aux.set(P.PHYS_WIDTH, p[P.PHYS_WIDTH])
        aux.set(P.H, p[P.H])
        aux.set(P.T, 0.0)
        aux.set(P.H_T, 1e12)          # 任意大的覆盖高度
        aux.set(P.FREQUENCY, p[P.FREQUENCY])
        aux.set(P.MURC, p[P.MURC])
        aux._microstrip_z0()
        aux._dispersion()

    # -- 静态填充因子（Kirschning-Jansen 1984） ---------------------------
    @staticmethod
    def _filling_factor_even(u, g, e_r):
        v = u * (20.0 + g * g) / (10.0 + g * g) + g * math.exp(-g)
        if v <= 0.0:
            return math.nan
        v3 = v * v * v
        v4 = v3 * v
        a_e = (1.0 + math.log((v4 + v * v / 2704.0) / (v4 + 0.432)) / 49.0
               + math.log(1.0 + v3 / 5929.741) / 18.7)
        b_e = 0.564 * _pow((e_r - 0.9) / (e_r + 3.0), 0.053)
        return _pow(1.0 + 10.0 / v, -a_e * b_e)

    @staticmethod
    def _filling_factor_odd(u, g, e_r):
        b_odd = 0.747 * e_r / (0.15 + e_r)
        c_odd = b_odd - (b_odd - 0.207) * math.exp(-0.414 * u)
        d_odd = 0.593 + 0.694 * math.exp(-0.562 * u)
        return math.exp(-c_odd * _pow(g, d_odd))

    @staticmethod
    def _delta_q_cover_even(h2h):
        if h2h <= 0.0:
            return -1.0                 # h2h → 0⁺ 的 IEEE 极限：tanh(-∞)
        if h2h <= 39.0:
            return math.tanh(1.626 + 0.107 * h2h - 1.733 / math.sqrt(h2h))
        return 1.0

    @staticmethod
    def _delta_q_cover_odd(h2h):
        if 0.0 <= h2h < 7.0:
            return math.tanh(9.575 / (7.0 - h2h) - 2.965 + 1.68 * h2h - 0.311 * h2h * h2h)
        return 1.0                      # h2h = 7 处 9.575/(7-h2h) → +∞，tanh → 1

    def _er_eff_static(self):
        p = self._p
        er = p[P.EPSILONR]
        h = p[P.H]

        self._compute_single_line()
        er_eff_single = self._aux._er_eff_0

        u_t_e = self._w_t_e / h
        u_t_o = self._w_t_o / h
        g = p[P.PHYS_S] / h
        h2h = p[P.H_T] / h
        t_h = p[P.T] / h

        q_inf = self._filling_factor_even(u_t_e, g, er)
        q = (q_inf - Microstrip._delta_q_thickness(u_t_e, t_h)) * self._delta_q_cover_even(h2h)
        self._er_eff_e_0 = 0.5 * (er + 1.0) + 0.5 * (er - 1.0) * q

        q_inf = self._filling_factor_odd(u_t_o, g, er)
        q = (q_inf - Microstrip._delta_q_thickness(u_t_o, t_h)) * self._delta_q_cover_odd(h2h)

        a_o = 0.7287 * (er_eff_single - 0.5 * (er + 1.0)) * (1.0 - math.exp(-0.179 * u_t_o))
        self._er_eff_o_0 = (0.5 * (er + 1.0) + a_o - er_eff_single) * q + er_eff_single

    # -- 覆盖层对阻抗的修正（March 1981） --------------------------------
    #
    # 适配域（越界一律按"无覆盖"处理，返回 0）：
    #
    #   * h2h > 39：KiCad 自己在 _delta_q_cover_even 里以 h2h ≤ 39 为界，
    #     超过即认为覆盖层远到不起作用。此时 E·√(1+h2h) 与 K·√(1+h2h) 都已
    #     把 tanh 推到饱和，两支的极限本来就是 0，这道界只是省掉大指数运算。
    #   * x = 10^(0.103g-0.159) ≥ 2：0.747/sin(0.5πx) 在 x = 2, 4, 6… 处有极点，
    #     首个极点对应 g ≈ 4.47。极点前 sin(0.5πx) → 0⁺ 使 D → +∞、g_e → 0，
    #     所以"越过极点取 0"正是原式的连续极限，不是硬截断。
    #
    # 奇模支没有极点：G = 2.178 - 0.796g 随 g 增大把 tanh 压向 -1，修正项无界
    # 增长到 540·f_o，而 g → ∞ 时奇模退化为孤立单线，修正应趋于单根线的覆盖
    # 修正（约 20 Ω）。这一支的上界在 _z0_even_odd 中用单根线修正处理。
    #
    # 域内取值与 KiCad 逐位一致。
    _COVER_H2H_MAX = 39.0

    @staticmethod
    def _delta_Z0_even_cover(g, u, h2h):
        if h2h <= 0.0 or h2h > CoupledMicrostrip._COVER_H2H_MAX:
            return 0.0

        x = _pow(10.0, 0.103 * g - 0.159)
        if x >= 2.0:
            return 0.0

        a = -4.351 / _pow(1.0 + h2h, 1.842)
        b = 6.639 / _pow(1.0 + h2h, 1.861)
        c = -2.291 / _pow(1.0 + h2h, 1.90)
        f_e = 1.0 - _atanh(a + (b + c * u) * u)

        y = _pow(10.0, 0.0492 * g - 0.073)
        d = _div(0.747, math.sin(0.5 * math.pi * x))
        e = 0.725 * math.sin(0.5 * math.pi * y)
        f = _pow(10.0, 0.11 - 0.0947 * g)
        g_e = 270.0 * (1.0 - math.tanh(d + e * math.sqrt(1.0 + h2h) - f / (1.0 + h2h)))

        return f_e * g_e

    @staticmethod
    def _delta_Z0_odd_cover(g, u, h2h):
        # 原式（KiCad / QUCS 同源）在 g 大时无界增长，见类内注释；上界由调用方施加。
        if h2h <= 0.0 or h2h > CoupledMicrostrip._COVER_H2H_MAX:
            return 0.0

        j = math.tanh(_pow(1.0 + h2h, 1.585) / 6.0)
        f_o = _pow(u, j)

        g_coef = 2.178 - 0.796 * g
        k = math.log10(20.492 * _pow(g, 0.174)) if g > 0.858 else 1.30
        l = 2.51 * _pow(g, -0.462) if g > 0.873 else 2.674

        g_o = 270.0 * (1.0 - math.tanh(
            g_coef + k * math.sqrt(1.0 + h2h) - l / (1.0 + h2h)))
        return f_o * g_o

    # -- 静态奇偶模阻抗 ---------------------------------------------------
    def _z0_even_odd(self):
        p = self._p
        h = p[P.H]
        u_t_e = self._w_t_e / h
        u_t_o = self._w_t_o / h
        g = p[P.PHYS_S] / h
        h2h = p[P.H_T] / h

        if g <= 0.0 or u_t_e <= 0.0 or u_t_o <= 0.0:
            self._z0_e_0 = self._z0_o_0 = math.nan
            return

        z0_single = self._aux._z0_0
        er_eff_single = self._aux._er_eff_0

        # 偶模
        er_eff = self._er_eff_e_0
        q_1 = 0.8695 * _pow(u_t_e, 0.194)
        q_2 = 1.0 + 0.7519 * g + 0.189 * _pow(g, 2.31)
        q_3 = (0.1975 + _pow(16.6 + _pow(8.4 / g, 6.0), -0.387)
               + math.log(_pow(g, 10.0) / (1.0 + _pow(g / 3.4, 10.0))) / 241.0)
        q_4 = _div(2.0 * q_1,
                   q_2 * (math.exp(-g) * _pow(u_t_e, q_3)
                          + (2.0 - math.exp(-g)) * _pow(u_t_e, -q_3)))
        self._z0_e_0 = _div(
            z0_single * math.sqrt(_div(er_eff_single, er_eff)),
            1.0 - math.sqrt(er_eff_single) * q_4 * z0_single / ZF0)
        self._z0_e_0 -= _div(self._delta_Z0_even_cover(g, u_t_e, h2h), math.sqrt(er_eff))

        # 奇模
        er_eff = self._er_eff_o_0
        q_5 = 1.794 + 1.14 * math.log(1.0 + 0.638 / (g + 0.517 * _pow(g, 2.43)))
        q_6 = (0.2305 + math.log(_pow(g, 10.0) / (1.0 + _pow(g / 5.8, 10.0))) / 281.3
               + math.log(1.0 + 0.598 * _pow(g, 1.154)) / 5.1)
        q_7 = (10.0 + 190.0 * g * g) / (1.0 + 82.3 * g * g * g)
        q_8 = math.exp(-6.5 - 0.95 * math.log(g) - _pow(g / 0.15, 5.0))
        q_9 = math.log(q_7) * (q_8 + 1.0 / 16.5)
        q_10 = (q_2 * q_4 - q_5 * math.exp(math.log(u_t_o) * q_6 * _pow(u_t_o, -q_9))) / q_2

        self._z0_o_0 = _div(
            z0_single * math.sqrt(_div(er_eff_single, er_eff)),
            1.0 - math.sqrt(er_eff_single) * q_10 * z0_single / ZF0)

        # 奇模覆盖修正以单根线修正为上界：g → ∞ 时两线解耦，奇模退化为孤立
        # 单线，修正必须趋于单根线的值；原式在 g ≳ 2.7 后超过该上界并继续增长
        # 到 540·f_o，会把 Z0_o 推成负数。取 min 保持连续。
        delta_o_cover = self._delta_Z0_odd_cover(g, u_t_o, h2h)
        delta_o_single = self._aux._delta_z0_cover(u_t_o, h2h)
        if 0.0 < delta_o_single < delta_o_cover:
            delta_o_cover = delta_o_single

        self._z0_o_0 -= _div(delta_o_cover, math.sqrt(er_eff))

    # -- 色散（Kirschning-Jansen 1984） ----------------------------------
    def _er_eff_freq(self):
        p = self._p
        er = p[P.EPSILONR]
        u = p[P.PHYS_WIDTH] / p[P.H]
        g = p[P.PHYS_S] / p[P.H]
        f_n = p[P.FREQUENCY] * p[P.H] / 1e06      # 归一化频率 [GHz·mm]

        p_1 = (0.27488 + (0.6315 + 0.525 / _pow(1.0 + 0.0157 * f_n, 20.0)) * u
               - 0.065683 * math.exp(-8.7513 * u))
        p_2 = 0.33622 * (1.0 - math.exp(-0.03442 * er))
        p_3 = 0.0363 * math.exp(-4.6 * u) * (1.0 - math.exp(-_pow(f_n / 38.7, 4.97)))
        p_4 = 1.0 + 2.751 * (1.0 - math.exp(-_pow(er / 15.916, 8.0)))
        p_5 = 0.334 * math.exp(-3.3 * _pow(er / 15.0, 3.0)) + 0.746
        p_6 = p_5 * math.exp(-_pow(f_n / 18.0, 0.368))
        p_7 = (1.0 + 4.069 * p_6 * _pow(g, 0.479)
               * math.exp(-1.347 * _pow(g, 0.595) - 0.17 * _pow(g, 2.5)))

        f_e = p_1 * p_2 * _pow((p_3 * p_4 + 0.1844 * p_7) * f_n, 1.5763)
        self._er_eff_e = er - _div(er - self._er_eff_e_0, 1.0 + f_e)
        self._prop_delay_e = self._unit_propagation_delay(self._er_eff_e)

        p_8 = 0.7168 * (1.0 + 1.076 / (1.0 + 0.0576 * (er - 1.0)))
        p_9 = p_8 - (0.7913 * (1.0 - math.exp(-_pow(f_n / 20.0, 1.424)))
                     * math.atan(2.481 * _pow(er / 8.0, 0.946)))
        p_10 = 0.242 * _pow(er - 1.0, 0.55)
        p_11 = (0.6366 * (math.exp(-0.3401 * f_n) - 1.0)
                * math.atan(1.263 * _pow(u / 3.0, 1.629)))
        p_12 = p_9 + _div(1.0 - p_9, 1.0 + 1.183 * _pow(u, 1.376))
        p_13 = 1.695 * p_10 / (0.414 + 1.605 * p_10)
        p_14 = 0.8928 + 0.1072 * (1.0 - math.exp(-0.42 * _pow(f_n / 20.0, 3.215)))
        p_15 = abs(1.0 - _div(
            0.8928 * (1.0 + p_11) * p_12 * math.exp(-p_13 * _pow(g, 1.092)), p_14))

        f_o = p_1 * p_2 * _pow((p_3 * p_4 + 0.1844) * f_n * p_15, 1.5763)
        self._er_eff_o = er - _div(er - self._er_eff_o_0, 1.0 + f_o)
        self._prop_delay_o = self._unit_propagation_delay(self._er_eff_o)

    def _z0_dispersion(self):
        p = self._p
        er = p[P.EPSILONR]
        u = p[P.PHYS_WIDTH] / p[P.H]
        g = p[P.PHYS_S] / p[P.H]
        f_n = p[P.FREQUENCY] * p[P.H] / 1e06

        e_r_eff_single_f = self._aux._p[P.EPSILON_EFF]
        e_r_eff_single_0 = self._aux._er_eff_0
        z0_single_f = self._aux._p[P.Z0]

        e_r_eff_o_f = self._er_eff_o
        e_r_eff_o_0 = self._er_eff_o_0

        q_11 = 0.893 * (1.0 - 0.3 / (1.0 + 0.7 * (er - 1.0)))
        q_12 = (2.121 * _div(_pow(f_n / 20.0, 4.91),
                             1.0 + q_11 * _pow(f_n / 20.0, 4.91))
                * math.exp(-2.87 * g) * _pow(g, 0.902))
        q_13 = 1.0 + 0.038 * _pow(er / 8.0, 5.1)
        q_14 = 1.0 + 1.203 * _pow(er / 15.0, 4.0) / (1.0 + _pow(er / 15.0, 4.0))
        q_15 = _div(
            1.887 * math.exp(-1.5 * _pow(g, 0.84)) * _pow(g, q_14),
            1.0 + 0.41 * _pow(f_n / 15.0, 3.0) * _pow(u, 2.0 / q_13)
            / (0.125 + _pow(u, 1.626 / q_13)))
        q_16 = (1.0 + 9.0 / (1.0 + 0.403 * _pow(er - 1.0, 2))) * q_15
        q_17 = (0.394 * (1.0 - math.exp(-1.47 * _pow(u / 7.0, 0.672)))
                * (1.0 - math.exp(-4.25 * _pow(f_n / 20.0, 1.87))))
        q_18 = _div(0.61 * (1.0 - math.exp(-2.13 * _pow(u / 8.0, 1.593))),
                    1.0 + 6.544 * _pow(g, 4.17))
        q_19 = _div(0.21 * _pow(g, 4.0),
                    (1.0 + 0.18 * _pow(g, 4.9)) * (1.0 + 0.1 * u * u)
                    * (1.0 + _pow(f_n / 24.0, 3.0)))
        q_20 = (0.09 + 1.0 / (1.0 + 0.1 * _pow(er - 1.0, 2.7))) * q_19
        q_21 = abs(1.0 - _div(
            42.54 * _pow(g, 0.133) * math.exp(-0.812 * g) * _pow(u, 2.5),
            1.0 + 0.033 * _pow(u, 2.5)))

        r_e = _pow(f_n / 28.843, 12)
        q_e = 0.016 + _pow(0.0514 * er * q_21, 4.524)
        p_e = 4.766 * math.exp(-3.228 * _pow(u, 0.641))
        d_e = (5.086 * q_e * _div(r_e, 0.3838 + 0.386 * q_e)
               * _div(math.exp(-22.2 * _pow(u, 1.92)), 1.0 + 1.2992 * r_e)
               * _div(_pow(er - 1.0, 6.0), 1.0 + 10.0 * _pow(er - 1.0, 6.0)))
        c_e = (1.0 + 1.275 * (1.0 - math.exp(
            -0.004625 * p_e * _pow(er, 1.674) * _pow(f_n / 18.365, 2.745)))
            - q_12 + q_16 - q_17 + q_18 + q_20)

        r_1 = 0.03891 * _pow(er, 1.4)
        r_2 = 0.267 * _pow(u, 7.0)
        r_7 = 1.206 - 0.3144 * math.exp(-r_1) * (1.0 - math.exp(-r_2))
        r_10 = 0.00044 * _pow(er, 2.136) + 0.0184
        tmpf = _pow(f_n / 19.47, 6.0)
        r_11 = _div(tmpf, 1.0 + 0.0962 * tmpf)
        r_12 = 1.0 / (1.0 + 0.00245 * u * u)
        r_15 = 0.707 * r_10 * _pow(f_n / 12.3, 1.097)
        r_16 = 1.0 + 0.0503 * er * er * r_11 * (1.0 - math.exp(-_pow(u / 15.0, 6.0)))
        q_0 = r_7 * (1.0 - 1.1241 * _div(r_12, r_16)
                     * math.exp(-0.026 * _pow(f_n, 1.15656) - r_15))

        p[P.Z0_E] = (self._z0_e_0
                     * _pow(0.9408 * _pow(e_r_eff_single_f, c_e) - 0.9603, q_0)
                     / _pow((0.9408 - d_e) * _pow(e_r_eff_single_0, c_e) - 0.9603, q_0))

        q_29 = 15.16 / (1.0 + 0.196 * _pow(er - 1.0, 2.0))
        tmpf = _pow(er - 1.0, 3.0)
        q_28 = _div(0.149 * tmpf, 94.5 + 0.038 * tmpf)
        tmpf = _pow(er - 1.0, 1.5)
        q_27 = 0.4 * _pow(g, 0.84) * (1.0 + _div(2.5 * tmpf, 5.0 + tmpf))
        tmpf = _pow((er - 1.0) / 13.0, 12.0)
        q_26 = 30.0 - 22.2 * _div(tmpf, 1.0 + 3.0 * tmpf) - q_29
        tmpf = (er - 1.0) * (er - 1.0)
        q_25 = _div(0.3 * f_n * f_n, 10.0 + f_n * f_n) * (1.0 + _div(2.333 * tmpf, 5.0 + tmpf))
        q_24 = _div(2.506 * q_28 * _pow(u, 0.894) * _pow((1.0 + 1.3 * u) * f_n / 99.25, 4.29),
                    3.575 + _pow(u, 0.894))
        q_23 = (1.0 + _div(0.005 * f_n * q_27,
                           (1.0 + 0.812 * _pow(f_n / 15.0, 1.9)) * (1.0 + 0.025 * u * u)))
        q_22 = _div(0.925 * _pow(f_n / q_26, 1.536),
                    1.0 + 0.3 * _pow(f_n / 30.0, 1.536))

        p[P.Z0_O] = (z0_single_f
                     + _div(self._z0_o_0 * _pow(_div(e_r_eff_o_f, e_r_eff_o_0), q_22)
                            - z0_single_f * q_23,
                            1.0 + q_24 + _pow(0.46 * g, 2.2) * q_25))

    # -- 损耗 -------------------------------------------------------------
    def _conductor_losses(self):
        p = self._p
        e_r_eff_e_0 = self._er_eff_e_0
        e_r_eff_o_0 = self._er_eff_o_0
        z0_h_e = self._z0_e_0 * math.sqrt(e_r_eff_e_0)   # 均匀介质等效阻抗
        z0_h_o = self._z0_o_0 * math.sqrt(e_r_eff_o_0)
        delta = p[P.SKIN_DEPTH]

        if p[P.FREQUENCY] > 0.0:
            # 电流分布因子（两模相同）
            k = math.exp(-1.2 * _pow(_div(z0_h_e + z0_h_o, 2.0 * ZF0), 0.7))
            r_s = 1.0 / (p[P.SIGMA] * delta)
            r_s *= 1.0 + ((2.0 / math.pi)
                          * math.atan(1.40 * _pow(_div(p[P.ROUGH], delta), 2.0)))

            q_c_e = _div(math.pi * z0_h_e * p[P.PHYS_WIDTH] * p[P.FREQUENCY],
                         r_s * C0 * k)
            alpha_c_e = ((20.0 * math.pi / math.log(10.0)) * p[P.FREQUENCY]
                         * math.sqrt(e_r_eff_e_0) * _div(1.0, C0 * q_c_e))

            q_c_o = _div(math.pi * z0_h_o * p[P.PHYS_WIDTH] * p[P.FREQUENCY],
                         r_s * C0 * k)
            alpha_c_o = ((20.0 * math.pi / math.log(10.0)) * p[P.FREQUENCY]
                         * math.sqrt(e_r_eff_o_0) * _div(1.0, C0 * q_c_o))
        else:
            alpha_c_e = alpha_c_o = 0.0

        self._atten_cond_e = alpha_c_e * p[P.PHYS_LEN]
        self._atten_cond_o = alpha_c_o * p[P.PHYS_LEN]

    def _dielectric_losses(self):
        p = self._p
        er = p[P.EPSILONR]
        e_r_eff_e_0 = self._er_eff_e_0
        e_r_eff_o_0 = self._er_eff_o_0

        if er == 1.0:
            # 均匀空气介质没有介质损耗（KiCad 原式此处为 0/0 → NaN）
            self._atten_dielectric_e = self._atten_dielectric_o = 0.0
            return

        factor = ((20.0 * math.pi / math.log(10.0)) * (p[P.FREQUENCY] / C0)
                  * (er / math.sqrt(e_r_eff_e_0)) * p[P.TAND])
        alpha_d_e = factor * _div(e_r_eff_e_0 - 1.0, er - 1.0)
        factor = ((20.0 * math.pi / math.log(10.0)) * (p[P.FREQUENCY] / C0)
                  * (er / math.sqrt(e_r_eff_o_0)) * p[P.TAND])
        alpha_d_o = factor * _div(e_r_eff_o_0 - 1.0, er - 1.0)

        self._atten_dielectric_e = alpha_d_e * p[P.PHYS_LEN]
        self._atten_dielectric_o = alpha_d_o * p[P.PHYS_LEN]

    def _attenuation(self):
        self._p[P.SKIN_DEPTH] = self._skin_depth()
        self._conductor_losses()
        self._dielectric_losses()

    def _line_angle(self):
        p = self._p
        if p[P.FREQUENCY] <= 0.0:
            self._ang_l_e = self._ang_l_o = 0.0
            return
        v_e = C0 / math.sqrt(self._er_eff_e)
        v_o = C0 / math.sqrt(self._er_eff_o)
        self._ang_l_e = 2.0 * math.pi * p[P.PHYS_LEN] / _div(v_e, p[P.FREQUENCY])
        self._ang_l_o = 2.0 * math.pi * p[P.PHYS_LEN] / _div(v_o, p[P.FREQUENCY])

    def _diff_impedance(self):
        # 差分阻抗 = 2·Z0_O（Pozar 4th ed. §7.6）。用色散后的 Z0_O，
        # 这样几 GHz 以上 Zdiff 会跟着频率走。
        self._p[P.Z_DIFF] = 2.0 * self._p[P.Z0_O]

    def _common_mode_impedance(self):
        # Z_comm = Z0_E/2（两条 Z0_E 线同压并联，Pozar Eq. 7.68-7.69）；
        # k = (Z0e-Z0o)/(Z0e+Z0o)（Pozar Eq. 7.81）。
        p = self._p
        p[P.Z_COMM] = 0.5 * p[P.Z0_E]
        denom = p[P.Z0_E] + p[P.Z0_O]
        p[P.COUPLING_K] = _div(p[P.Z0_E] - p[P.Z0_O], denom) if denom > 0.0 else 0.0

    # -- 综合辅助 ---------------------------------------------------------
    @staticmethod
    def _syn_err_fun(s_h, w_h, e_r, w_h_se, w_h_so):
        g = _cosh(0.5 * math.pi * s_h)
        he = _cosh(math.pi * w_h + 0.5 * math.pi * s_h)

        f1 = (2.0 / math.pi) * _acosh(_div(2.0 * he - g + 1.0, g + 1.0))
        f2 = (2.0 / math.pi) * _acosh(_div(2.0 * he - g - 1.0, g - 1.0))

        if e_r <= 6.0:
            f2 += (4.0 / (math.pi * (1.0 + e_r / 2.0))) * _acosh(1.0 + _div(2.0 * w_h, s_h))
        else:
            f2 += (1.0 / math.pi) * _acosh(1.0 + _div(2.0 * w_h, s_h))

        return f1 - w_h_se, f2 - w_h_so

    def _synth_width(self):
        """Akhtarzad / Hinton 初值 + 二维牛顿（对应 synth_width）。"""
        p = self._p
        e_r = p[P.EPSILONR]
        h = p[P.H]

        def wheeler(z0):
            a = math.exp(z0 * math.sqrt(e_r + 1.0) / 42.4) - 1.0
            return _div(8.0 * math.sqrt(a * ((7.0 + 4.0 / e_r) / 11.0)
                                            + ((1.0 + 1.0 / e_r) / 0.81)), a)

        w_h_se = wheeler(p[P.Z0_E] / 2.0)
        w_h_so = wheeler(p[P.Z0_O] / 2.0)

        ce = _cosh(0.5 * math.pi * w_h_se)
        co = _cosh(0.5 * math.pi * w_h_so)
        s_h = (2.0 / math.pi) * _acosh(_div(ce + co - 2.0, co - ce))
        w_h = _acosh(_div(ce * co - 1.0, co - ce)) / math.pi - s_h / 2.0

        p[P.PHYS_S] = s_h * h
        p[P.PHYS_WIDTH] = w_h * h

        f1, f2 = self._syn_err_fun(s_h, w_h, e_r, w_h_se, w_h_so)

        eps = 1e-4
        # KiCad 原版是无上限的 do-while；这里加迭代上限，避免退化输入下死循环。
        for _ in range(250):
            ft1, ft2 = self._syn_err_fun(s_h + eps, w_h, e_r, w_h_se, w_h_so)
            j11 = (ft1 - f1) / eps
            j21 = (ft2 - f2) / eps
            ft1, ft2 = self._syn_err_fun(s_h, w_h + eps, e_r, w_h_se, w_h_so)
            j12 = (ft1 - f1) / eps
            j22 = (ft2 - f2) / eps

            det = j11 * j22 - j21 * j12
            if det == 0.0 or not math.isfinite(det):
                break

            s_h += (-f1 * j22 + f2 * j12) / det
            w_h += (-f2 * j11 + f1 * j21) / det

            f1, f2 = self._syn_err_fun(s_h, w_h, e_r, w_h_se, w_h_so)
            err = math.sqrt(f1 * f1 + f2 * f2)
            if not math.isfinite(err) or err <= 1e-4:
                break

        p[P.PHYS_S] = s_h * h
        p[P.PHYS_WIDTH] = w_h * h

    def _syn_fun(self, s_h, w_h, z0_e, z0_o):
        p = self._p
        p[P.PHYS_S] = s_h * p[P.H]
        p[P.PHYS_WIDTH] = w_h * p[P.H]
        self.analyse()
        return p[P.Z0_E] - z0_e, p[P.Z0_O] - z0_o


# ---------------------------------------------------------------------------
# 耦合带状线 / 差分对
# ---------------------------------------------------------------------------

class CoupledStripline(TranslineBase):
    """
    耦合带状线（差分对，对应 COUPLED_STRIPLINE）。

    模型：Cohn 1955 "Shielded Coupled-Strip Transmission Line" 式 (2)-(22)，
          零厚度耦合阻抗用椭圆积分闭式，有限厚度用 Cohn 1954 式 (2) 边缘电容修正，
          偏置（非居中）导体用 Wadell 1991 §3.6.3 镜像法 + 三项修正。

    几何：两条等宽 W、边到边间距 S 的导体，夹在上下地平面之间，总间距 H，
          导体上表面到上地平面距离 A（STRIPLINE_A）。A ≤ 0 视为未设置，
          按居中处理（A = H/2）。

    因为是均匀介质 TEM，奇偶模的 eps_eff 都等于基板 er，色散为零。
    输出：Z0_E、Z0_O、Z_DIFF = 2·Z0_O、Z_COMM = Z0_E/2、k = (Z0e-Z0o)/(Z0e+Z0o)。

    前置条件：H > 0、PHYS_WIDTH > 0、PHYS_S > 0、SIGMA > 0、MURC > 0。
    退化几何得到 NaN 并由 ``analysis_results`` 标记 ERROR，而不是抛异常。

    三处适用域需要注意（都源自 Cohn 原式的适用范围，不是实现误差）：

      * **奇模式的分支切换**：Cohn 1955 给了式 (20) 与式 (22) 两支，按
        s/t ≥ 5 硬切换（与 KiCad 一致）。两支在 s/t = 5 处不连续，所以
        Z0_O 对导体厚度 t 不是单调的——W = S = 0.3 mm、H = 1.6 mm 时
        t = 35 µm（s/t = 8.6，走式 20）与 t = 70 µm（s/t = 4.3，走式 22）
        之间 Z0_O 会回升约 0.1 Ω。
      * **偏置导体的厚度域**：Cohn 1954 式 (2) 的边缘电容要求每个虚拟板间距
        上 t < h；镜像法把间距拆成 2a 与 2(h−a)，安全区间是 t/2 < a < h − t/2。
        越界时原式会 0/0 或对非正数取对数，这里显式置 NaN。
      * **偏置导体的位置域**：镜像法要求 0 < a < h（a ≥ h 表示导体已经落到
        或越过上地平面，2(h−a) ≤ 0 会让 tanh 的辐角除零）。越界同样置 NaN。

    有限厚度走的是与零厚度闭式完全独立的一条链（Cohn 1954 边缘电容 +
    Cohn 1955 式 (18)/(20)），t → 0 时两者在 1% 内接上。
    """

    _PARAMS = (
        P.SKIN_DEPTH, P.Z0_E, P.Z0_O, P.Z_DIFF, P.Z_COMM, P.COUPLING_K,
        P.PHYS_WIDTH, P.FREQUENCY, P.PHYS_LEN, P.H, P.PHYS_S, P.T,
        P.EPSILONR, P.MUR, P.MURC, P.SIGMA, P.TAND, P.ROUGH, P.ANG_L,
        P.STRIPLINE_A, P.ATTEN_COND_EVEN, P.ATTEN_COND_ODD,
        P.ATTEN_DILECTRIC_EVEN, P.ATTEN_DILECTRIC_ODD,
        P.EPSILON_EFF_EVEN, P.EPSILON_EFF_ODD,
        P.UNIT_PROP_DELAY_EVEN, P.UNIT_PROP_DELAY_ODD,
        P.DIELECTRIC_MODEL_SEL, P.EPSILONR_SPEC_FREQ,
    )

    def __init__(self):
        super().__init__()
        # 单根带状线辅助计算器（对应 KiCad 的 m_striplineCalc）
        self._stripline = Stripline()
        self._c_f_0 = 0.0            # 零厚度单条导体单边到地的边缘电容
        self._c_f_t_h = 0.0          # 有限厚度单条导体的边缘电容
        self._z0_w_h_0 = 0.0         # 零厚度单导体阻抗
        self._z0_w_h_t_h = 0.0       # 有限厚度单导体阻抗
        self._z0_e_w_h_0_s_h = 0.0   # 零厚度耦合线偶模阻抗
        self._z0_o_w_h_0_s_h = 0.0   # 零厚度耦合线奇模阻抗
        self._e_eff_e = 0.0
        self._e_eff_o = 0.0
        self._ang_l = 0.0
        self._unit_prop_delay_e = 0.0
        self._unit_prop_delay_o = 0.0

    # -- 输出便捷属性 -----------------------------------------------------
    @property
    def z0_even(self):
        return self._p[P.Z0_E]

    @property
    def z0_odd(self):
        return self._p[P.Z0_O]

    @property
    def z_diff(self):
        return self._p[P.Z_DIFF]

    @property
    def z_comm(self):
        return self._p[P.Z_COMM]

    @property
    def coupling_k(self):
        return self._p[P.COUPLING_K]

    @property
    def eps_eff_even(self):
        return self._p[P.EPSILON_EFF_EVEN]

    @property
    def eps_eff_odd(self):
        return self._p[P.EPSILON_EFF_ODD]

    @property
    def width(self):
        return self._p[P.PHYS_WIDTH]

    @property
    def spacing(self):
        return self._p[P.PHYS_S]

    @property
    def length(self):
        return self._p[P.PHYS_LEN]

    @property
    def angle(self):
        return self._p[P.ANG_L]

    # -- 主流程 -----------------------------------------------------------
    def analyse(self):
        self._update_dielectric_model()

        p = self._p
        f = p[P.FREQUENCY]
        raw_eps_r = p[P.EPSILONR]
        raw_tan_d = p[P.TAND]

        p[P.EPSILONR] = self._dispersed_epsilon_r(f)
        p[P.TAND] = self._dispersed_tan_delta(f)

        if not self._geometry_is_usable():
            self._mark_degenerate()
            p[P.EPSILONR] = raw_eps_r
            p[P.TAND] = raw_tan_d
            return

        p[P.SKIN_DEPTH] = self._skin_depth()

        w = p[P.PHYS_WIDTH]
        t = p[P.T]
        s = p[P.PHYS_S]
        h = p[P.H]
        a = p[P.STRIPLINE_A]
        er = p[P.EPSILONR]

        # A ≤ 0 表示调用方没写过 STRIPLINE_A，按居中处理
        if a <= 0.0:
            a = h / 2.0

        self._calc_offset_zero_thickness_coupled_impedances(h, a, w, s, t, er)

        if t == 0.0:
            # 零厚度：直接取闭式结果
            p[P.Z0_E] = self._z0_e_w_h_0_s_h
            p[P.Z0_O] = self._z0_o_w_h_0_s_h
            p[P.Z_DIFF] = 2.0 * p[P.Z0_O]
        elif self._is_centered_offset(a, h):
            self._calc_single_strip_impedances()
            self._calc_fringe_capacitances(h, t, er)
            self._calc_z0_even_mode()
            self._calc_z0_odd_mode(t, s)
        elif not self._is_offset_within_finite_thickness_limits(a, h, t):
            # Cohn 1954 式 (2) 的边缘电容要求每个虚拟板间距上 t < h；镜像法
            # 把间距拆成 2a 与 2(h-a)，安全区间是 t/2 < a < h - t/2。
            # 越界时原式会 0/0 或对非正数取对数，这里显式置 NaN 由状态层报错。
            p[P.Z0_E] = p[P.Z0_O] = p[P.Z_DIFF] = math.nan
        else:
            # Wadell 式 3.6.3.22 镜像法：把偏置结构看成两个虚拟居中带状线
            # （板间距 2a 与 2(h-a)）的并联，再套式 3.6.3.23 三项修正。
            z0e_1, z0o_1 = self._calc_offset_virtual_branch(2.0 * a, w, s, t, er)
            z0e_2, z0o_2 = self._calc_offset_virtual_branch(2.0 * (h - a), w, s, t, er)

            p[P.H] = h

            z0e_image = _div(2.0, _div(1.0, z0e_1) + _div(1.0, z0e_2))
            z0o_image = _div(2.0, _div(1.0, z0o_1) + _div(1.0, z0o_2))

            p[P.Z0_E] = self._apply_offset_correction(z0e_image, a, h, w, t, er)
            p[P.Z0_O] = self._apply_offset_correction(z0o_image, a, h, w, t, er)
            p[P.Z_DIFF] = 2.0 * p[P.Z0_O]

            # 把修正后的偏置零厚度阻抗写回成员变量，供诊断读取
            self._calc_offset_zero_thickness_coupled_impedances(h, a, w, s, t, er)

        self._calc_losses()
        self._calc_dielectrics()

        z0e = p[P.Z0_E]
        z0o = p[P.Z0_O]

        p[P.Z_COMM] = 0.5 * z0e
        denom = z0e + z0o
        p[P.COUPLING_K] = _div(z0e - z0o, denom) if denom > 0.0 else 0.0

        p[P.EPSILON_EFF_EVEN] = self._e_eff_e
        p[P.EPSILON_EFF_ODD] = self._e_eff_o
        p[P.UNIT_PROP_DELAY_EVEN] = self._unit_prop_delay_e
        p[P.UNIT_PROP_DELAY_ODD] = self._unit_prop_delay_o
        p[P.ANG_L] = self._ang_l

        p[P.EPSILONR] = raw_eps_r
        p[P.TAND] = raw_tan_d

    def synthesize(self, opts=SYNTH.DEFAULT, recalculate_length=False):
        """
        综合几何。opts：

            SYNTH.DEFAULT           wcalc 初值 + 二维牛顿联立求解 (W, S)
            SYNTH.FROM_ZDIFF_ZCOMM  以 (Z_DIFF, Z_COMM) 为目标（先换算成 Z0_O/Z0_E）
            SYNTH.FIX_WIDTH         固定线宽 W，反解间距 S 以匹配 Z0_O
            SYNTH.FIX_SPACING       固定间距 S，反解线宽 W 以匹配 Z0_O
        """
        p = self._p

        if opts == SYNTH.FROM_ZDIFF_ZCOMM:
            z_diff_target = p[P.Z_DIFF]
            z_comm_target = p[P.Z_COMM]

            if not (z_diff_target > 0.0) or not (z_comm_target > 0.0):
                return False

            p[P.Z0_O] = 0.5 * z_diff_target
            p[P.Z0_E] = 2.0 * z_comm_target

        if opts == SYNTH.FIX_WIDTH:
            return self._minimise_z0_error_1d(P.PHYS_S, P.Z0_O)

        if opts == SYNTH.FIX_SPACING:
            return self._minimise_z0_error_1d(P.PHYS_WIDTH, P.Z0_O)

        # 初值公式来自 wcalc（GPL-2.0，Dan McMahill），KiCad 原样沿用
        h = p[P.H]
        er = p[P.EPSILONR]
        z0e_target = p[P.Z0_E]
        z0o_target = p[P.Z0_O]

        z0 = math.sqrt(z0e_target * z0o_target)
        k = _div(z0e_target - z0o_target, z0e_target + z0o_target)

        ai = (1.0, -0.301, 3.209, -27.282, 56.609, -37.746)
        bi = (0.020, -0.623, 17.192, -68.946, 104.740, -16.148)
        ci = (0.002, -0.347, 7.171, -36.910, 76.132, -51.616)

        aw = math.exp(z0 * math.sqrt(er + 1.0) / 42.4) - 1.0
        f1 = _div(8.0 * math.sqrt(aw * (7.0 + 4.0 / er) / 11.0
                                  + (1.0 + 1.0 / er) / 0.81), aw)
        f2 = sum(a * (k ** i) for i, a in enumerate(ai))
        f3 = sum((b - c * (9.6 - er)) * ((0.6 - k) ** i)
                 for i, (b, c) in enumerate(zip(bi, ci)))

        w = h * abs(f1 * f2)
        s = h * abs(f1 * f3)

        delta = MIL * 1e-5
        cval = 1e-12 * z0e_target * z0o_target

        iters = 0
        done = False

        while not done and iters < 50:
            iters += 1

            p[P.PHYS_WIDTH] = w
            p[P.PHYS_S] = s
            self.analyse()

            ze0 = p[P.Z0_E]
            zo0 = p[P.Z0_O]
            err = (ze0 - z0e_target) ** 2 + (zo0 - z0o_target) ** 2

            if err < cval:
                done = True
                continue

            # 近似雅可比
            p[P.PHYS_WIDTH] = w + delta
            p[P.PHYS_S] = s
            self.analyse()
            ze1, zo1 = p[P.Z0_E], p[P.Z0_O]

            p[P.PHYS_WIDTH] = w
            p[P.PHYS_S] = s + delta
            self.analyse()
            ze2, zo2 = p[P.Z0_E], p[P.Z0_O]

            dedw = (ze1 - ze0) / delta
            dodw = (zo1 - zo0) / delta
            deds = (ze2 - ze0) / delta
            dods = (zo2 - zo0) / delta

            det = dedw * dods - deds * dodw
            if det == 0.0 or not math.isfinite(det):
                return False

            # 每步最多改 10%，避免牛顿法发散
            dw = _div(-1.0 * ((ze0 - z0e_target) * dods - (zo0 - z0o_target) * deds), det)
            if abs(dw) > 0.1 * w:
                dw = 0.1 * w if dw > 0.0 else -0.1 * w
            w = abs(w + dw)

            ds = _div((ze0 - z0e_target) * dodw - (zo0 - z0o_target) * dedw, det)
            if abs(ds) > 0.1 * s:
                ds = 0.1 * s if ds > 0.0 else -0.1 * s
            s = abs(s + ds)

        if not done:
            return False

        # 用最终参数复算一次
        p[P.PHYS_WIDTH] = w
        p[P.PHYS_S] = s
        self.analyse()

        p[P.Z0_E] = z0e_target
        p[P.Z0_O] = z0o_target
        return True

    # -- 结果表 -----------------------------------------------------------
    def _publish_results(self, sink, impedance_failure, geometry_failure):
        p = self._p

        def checked(param, value, positive, failure):
            invalid = (not math.isfinite(value)) or (value <= 0.0 if positive else value < 0.0)
            sink(param, value, failure if invalid else STATUS.OK)

        sink(P.EPSILON_EFF_EVEN, self._e_eff_e)
        sink(P.EPSILON_EFF_ODD, self._e_eff_o)
        sink(P.UNIT_PROP_DELAY_EVEN, self._unit_prop_delay_e)
        sink(P.UNIT_PROP_DELAY_ODD, self._unit_prop_delay_o)
        sink(P.SKIN_DEPTH, p[P.SKIN_DEPTH])
        sink(P.ATTEN_COND_EVEN, p[P.ATTEN_COND_EVEN])
        sink(P.ATTEN_COND_ODD, p[P.ATTEN_COND_ODD])
        sink(P.ATTEN_DILECTRIC_EVEN, p[P.ATTEN_DILECTRIC_EVEN])
        sink(P.ATTEN_DILECTRIC_ODD, p[P.ATTEN_DILECTRIC_ODD])

        checked(P.Z0_E, p[P.Z0_E], True, impedance_failure)
        checked(P.Z0_O, p[P.Z0_O], True, impedance_failure)
        checked(P.Z_DIFF, p[P.Z_DIFF], True, impedance_failure)
        sink(P.Z_COMM, p[P.Z_COMM], STATUS.OK)
        sink(P.COUPLING_K, p[P.COUPLING_K], STATUS.OK)
        checked(P.ANG_L, p[P.ANG_L], False, impedance_failure)
        checked(P.PHYS_WIDTH, p[P.PHYS_WIDTH], True, geometry_failure)
        checked(P.PHYS_LEN, p[P.PHYS_LEN], False, geometry_failure)
        checked(P.PHYS_S, p[P.PHYS_S], True, geometry_failure)

    def _set_analysis_results(self):
        self._publish_results(self._set_analysis_result, STATUS.ERROR, STATUS.WARNING)

    def _set_synthesis_results(self):
        self._publish_results(self._set_synthesis_result, STATUS.WARNING, STATUS.ERROR)

    # -- 退化几何 ---------------------------------------------------------
    def _geometry_is_usable(self):
        p = self._p
        for key in (P.H, P.PHYS_WIDTH, P.PHYS_S, P.EPSILONR):
            value = p[key]
            if not math.isfinite(value) or value <= 0.0:
                return False
        return True

    def _mark_degenerate(self):
        p = self._p
        p[P.Z0_E] = p[P.Z0_O] = p[P.Z_DIFF] = math.nan
        p[P.Z_COMM] = 0.0
        p[P.COUPLING_K] = 0.0
        p[P.SKIN_DEPTH] = 0.0
        p[P.ATTEN_COND_EVEN] = p[P.ATTEN_COND_ODD] = 0.0
        p[P.ATTEN_DILECTRIC_EVEN] = p[P.ATTEN_DILECTRIC_ODD] = 0.0
        self._e_eff_e = self._e_eff_o = math.nan
        self._unit_prop_delay_e = self._unit_prop_delay_o = math.nan
        self._ang_l = 0.0
        p[P.EPSILON_EFF_EVEN] = p[P.EPSILON_EFF_ODD] = math.nan
        p[P.UNIT_PROP_DELAY_EVEN] = p[P.UNIT_PROP_DELAY_ODD] = math.nan
        p[P.ANG_L] = 0.0

    # -- 几何判据 ---------------------------------------------------------
    @staticmethod
    def _is_centered_offset(a, h):
        return abs(a - h / 2.0) <= h * 1e-9

    @staticmethod
    def _is_offset_within_finite_thickness_limits(a, h, t):
        return (a > 0.5 * t) and (a < h - 0.5 * t)

    # -- Cohn 1955 闭式 ---------------------------------------------------
    def _calc_fringe_capacitances(self, h, t, er):
        """Cohn 1954 式 (2) 与 Cohn 1955 式 (13) 的边缘电容。"""
        x = 1.0 - _div(t, h)
        if x <= 0.0:
            self._c_f_t_h = math.nan
        else:
            log_x = math.log(_div(1.0, x) + 1.0)
            log_term = math.log(_div(1.0, x * x) - 1.0)
            self._c_f_t_h = (E0 * er / math.pi) * ((2.0 / x) * log_x
                                                   - (1.0 / x - 1.0) * log_term)
        self._c_f_0 = (E0 * er / math.pi) * 2.0 * math.log(2.0)

    def _calc_zero_thickness_coupled_impedances(self, h, w, s, er):
        """Cohn 1955 式 (2)-(7)：零厚度耦合线奇偶模阻抗。"""
        k_e = math.tanh(math.pi * w / (2.0 * h)) * math.tanh(math.pi * (w + s) / (2.0 * h))
        k_o = math.tanh(math.pi * w / (2.0 * h)) * _coth(math.pi * (w + s) / (2.0 * h))
        k_e_p = math.sqrt(max(0.0, 1.0 - k_e * k_e))
        k_o_p = math.sqrt(max(0.0, 1.0 - k_o * k_o))

        self._z0_e_w_h_0_s_h = (ZF0 / (4.0 * math.sqrt(er))) * _div(
            self._elliptic_integral(k_e_p)[0], self._elliptic_integral(k_e)[0])
        self._z0_o_w_h_0_s_h = (ZF0 / (4.0 * math.sqrt(er))) * _div(
            self._elliptic_integral(k_o_p)[0], self._elliptic_integral(k_o)[0])

    def _calc_single_strip_impedances(self):
        """有限厚度与零厚度单导体阻抗。"""
        p = self._p
        er = p[P.EPSILONR]
        h = p[P.H]
        w = p[P.PHYS_WIDTH]

        self._z0_w_h_t_h = self._calc_z0_symmetric_stripline()

        # Cohn 1954 式 (5)-(6)（已含 1/sqrt(er) 修正）
        k = _sech(math.pi * w / (2.0 * h))
        k_p = math.tanh(math.pi * w / (2.0 * h))
        self._z0_w_h_0 = (ZF0 / (4.0 * math.sqrt(er))) * _div(
            self._elliptic_integral(k)[0], self._elliptic_integral(k_p)[0])

    def _calc_z0_even_mode(self):
        # Cohn 1955 式 (18)
        z_e = _div(1.0, (_div(1.0, self._z0_w_h_t_h)
                         - _div(self._c_f_t_h, self._c_f_0)
                         * (_div(1.0, self._z0_w_h_0) - _div(1.0, self._z0_e_w_h_0_s_h))))
        self._p[P.Z0_E] = z_e

    def _calc_z0_odd_mode(self, t, s):
        # Cohn 1955 式 (20)
        z_o_1 = _div(1.0, (_div(1.0, self._z0_w_h_t_h)
                           + _div(self._c_f_t_h, self._c_f_0)
                           * (_div(1.0, self._z0_o_w_h_0_s_h) - _div(1.0, self._z0_w_h_0))))

        # Cohn 1955 式 (22)
        z_o_2 = _div(1.0, (_div(1.0, self._z0_o_w_h_0_s_h)
                           + (_div(1.0, self._z0_w_h_t_h) - _div(1.0, self._z0_w_h_0))
                           - (2.0 / ZF0) * _div(self._c_f_t_h - self._c_f_0, E0)
                           + (2.0 * t) / (ZF0 * s)))

        # Cohn 1955 给了两支奇模式，按 s/t = 5 硬切换（与 KiCad 一致）：
        # 式 (20) 适用于 s/t ≥ 5 的弱耦合，式 (22) 覆盖 s/t < 5 的强耦合。
        # 两支在 s/t = 5 处不连续，故 Z0_O 对厚度 t 不是单调的——例如 W=S=0.3mm、
        # H=1.6mm 时 t = 35µm（s/t = 8.6，走式 20）与 t = 70µm（s/t = 4.3，走式 22）
        # 之间 Z0_O 会回升约 0.1 Ω。这是原式的适用域切分，不是实现误差。
        z_o = z_o_1 if _div(s, t) >= 5.0 else z_o_2

        self._p[P.Z0_O] = z_o
        self._p[P.Z_DIFF] = 2.0 * z_o

    def _calc_z0_symmetric_stripline(self):
        p = self._p
        sl = self._stripline
        sl.set(P.EPSILONR, p[P.EPSILONR])
        sl.set(P.T, p[P.T])
        sl.set(P.STRIPLINE_A, p[P.H] / 2.0)
        sl.set(P.H, p[P.H])
        sl.set(P.PHYS_LEN, p[P.PHYS_LEN])
        sl.set(P.FREQUENCY, p[P.FREQUENCY])
        sl.set(P.TAND, 0.0)
        sl.set(P.PHYS_WIDTH, p[P.PHYS_WIDTH])
        sl.set(P.ANG_L, 0.0)
        sl.set(P.SIGMA, p[P.SIGMA])
        sl.set(P.MURC, p[P.MURC])
        sl.analyse()
        return sl._p[P.Z0]

    def _calc_offset_virtual_branch(self, a_virtual_h, w, s, t, er):
        """跑一次虚拟居中带状线的完整有限厚度流程，返回 (Z0e, Z0o)。"""
        p = self._p
        p[P.H] = a_virtual_h
        self._calc_zero_thickness_coupled_impedances(a_virtual_h, w, s, er)
        self._calc_single_strip_impedances()
        self._calc_fringe_capacitances(a_virtual_h, t, er)
        self._calc_z0_even_mode()
        self._calc_z0_odd_mode(t, s)
        return p[P.Z0_E], p[P.Z0_O]

    def _calc_offset_zero_thickness_coupled_impedances(self, h, a, w, s, t, er):
        """偏置结构的零厚度耦合阻抗（Wadell 式 3.6.3.22 + 3.6.3.23）。"""
        if self._is_centered_offset(a, h):
            self._calc_zero_thickness_coupled_impedances(h, w, s, er)
            return

        # 镜像法把板间距拆成 2a 与 2(h-a)，两者都必须为正。a ≥ h 表示导体已经
        # 落到（或越过）上地平面，2(h-a) ≤ 0 会让 tanh 的辐角除零；a ≤ 0 同理。
        # 这类几何没有物理意义，按本类约定返回 NaN 交给状态层报错。
        if not (0.0 < a < h):
            self._z0_e_w_h_0_s_h = self._z0_o_w_h_0_s_h = math.nan
            return

        self._calc_zero_thickness_coupled_impedances(2.0 * a, w, s, er)
        z0e_1 = self._z0_e_w_h_0_s_h
        z0o_1 = self._z0_o_w_h_0_s_h

        self._calc_zero_thickness_coupled_impedances(2.0 * (h - a), w, s, er)
        z0e_2 = self._z0_e_w_h_0_s_h
        z0o_2 = self._z0_o_w_h_0_s_h

        z0e_image = _div(2.0, _div(1.0, z0e_1) + _div(1.0, z0e_2))
        z0o_image = _div(2.0, _div(1.0, z0o_1) + _div(1.0, z0o_2))

        self._z0_e_w_h_0_s_h = self._apply_offset_correction(z0e_image, a, h, w, t, er)
        self._z0_o_w_h_0_s_h = self._apply_offset_correction(z0o_image, a, h, w, t, er)

    @staticmethod
    def _apply_offset_correction(z_image, a_offset, plate_spacing, width, thickness, er):
        """
        Wadell 式 3.6.3.23 三项修正：

            Z = Z_image · (1 - (0.26π/8)·√er·Z_image·|0.5 - a/h|^2.2·((t+w)/h)^2.9)

        位置因子在 a = h/2 处为零，故居中极限严格还原。
        """
        position = abs(0.5 - _div(a_offset, plate_spacing))
        position_factor = _pow(position, 2.2)
        width_factor = _pow(_div(thickness + width, plate_spacing), 2.9)
        correction = (0.26 * math.pi / 8.0) * math.sqrt(er) * z_image * position_factor * width_factor
        return z_image * (1.0 - correction)

    # -- 损耗与传播 -------------------------------------------------------
    def _calc_losses(self):
        p = self._p
        length = p[P.PHYS_LEN]
        freq = p[P.FREQUENCY]
        er = p[P.EPSILONR]
        tand = p[P.TAND]

        # 均匀 TEM：介质填满整个截面，奇偶模 α_d 相同
        atten_d = LOG2DB * (math.pi / C0) * freq * math.sqrt(er) * tand * length
        p[P.ATTEN_DILECTRIC_EVEN] = atten_d
        p[P.ATTEN_DILECTRIC_ODD] = atten_d

        # 导体损耗复用单根带状线的增量电感律结果，再按 Z0_single/Z0_mode 缩放
        loss_single = self._single_stripline_conductor_loss()
        z0_single = self._stripline._p[P.Z0]
        z0_e = p[P.Z0_E]
        z0_o = p[P.Z0_O]

        p[P.ATTEN_COND_EVEN] = (loss_single * _div(z0_single, z0_e)
                                if (z0_e > 0.0 and math.isfinite(z0_e)) else 0.0)
        p[P.ATTEN_COND_ODD] = (loss_single * _div(z0_single, z0_o)
                               if (z0_o > 0.0 and math.isfinite(z0_o)) else 0.0)

    def _single_stripline_conductor_loss(self):
        p = self._p
        sl = self._stripline
        sl.set(P.EPSILONR, p[P.EPSILONR])
        sl.set(P.T, p[P.T])
        sl.set(P.STRIPLINE_A, p[P.H] / 2.0)
        sl.set(P.H, p[P.H])
        sl.set(P.PHYS_LEN, p[P.PHYS_LEN])
        sl.set(P.FREQUENCY, p[P.FREQUENCY])
        sl.set(P.TAND, 0.0)
        sl.set(P.PHYS_WIDTH, p[P.PHYS_WIDTH])
        sl.set(P.ANG_L, 0.0)
        sl.set(P.SIGMA, p[P.SIGMA])
        sl.set(P.MURC, p[P.MURC])
        sl.analyse()
        return sl._p[P.LOSS_CONDUCTOR]

    def _calc_dielectrics(self):
        # 介质均匀包围导体，奇偶模的 eps_eff 都等于 er（无色散）
        p = self._p
        er = p[P.EPSILONR]

        self._e_eff_e = er
        self._e_eff_o = er

        unit_prop_delay = self._unit_propagation_delay(er)
        self._unit_prop_delay_e = unit_prop_delay
        self._unit_prop_delay_o = unit_prop_delay

        self._ang_l = 2.0 * math.pi * p[P.PHYS_LEN] * _div(
            math.sqrt(er) * p[P.FREQUENCY], C0)


# ---------------------------------------------------------------------------
# 自检（直接运行本文件时执行）
# ---------------------------------------------------------------------------

def _self_test():
    """复现 KiCad 官方回归测试 test_transline_roundtrip.cpp 的微带/带状线用例。"""
    print("=" * 68)
    print("KiCad 官方往返测试复现")
    print("=" * 68)

    # --- Microstrip：FR-4 50 ohm 典型线，er 4.3, tand 0.02, H 0.2mm,
    #     W 0.4mm, T 35um, 1GHz。综合目标为 PHYS_WIDTH。
    ms = Microstrip()
    ms.set(P.EPSILONR, 4.3).set(P.TAND, 0.02).set(P.MUR, 1.0).set(P.MURC, 1.0)
    ms.set(P.ROUGH, 0.0).set(P.SIGMA, 5.8e7)
    ms.set(P.H, 0.2 * MM).set(P.H_T, 1.0e20).set(P.T, 35.0 * MICRON)
    ms.set(P.PHYS_WIDTH, 0.4 * MM).set(P.PHYS_LEN, 100.0 * MM)
    ms.set(P.FREQUENCY, 1.0e9).set(P.ANG_L, 0.0)

    ms.analyse()
    z0_orig = ms.analysis_results[P.Z0][0]
    print(f"微带 分析  : Z0 = {z0_orig:.4f} ohm, eps_eff = {ms.eps_eff:.5f}, "
          f"att_cond = {ms.attenuation_conductor_db:.4f} dB, "
          f"att_diel = {ms.attenuation_dielectric_db:.4f} dB")

    ms.set(P.Z0, z0_orig).set(P.ANG_L, math.pi / 2.0)
    ok = ms.synthesize()
    w_synth = ms.width
    ms.analyse()
    z0_check = ms.analysis_results[P.Z0][0]
    print(f"微带 综合  : W = {w_synth / MM:.5f} mm, Z0 复算 = {z0_check:.4f} ohm, "
          f"相对误差 = {abs(z0_check - z0_orig) / z0_orig * 100:.4f}%  (收敛={ok})")

    # --- Stripline：b = 1.6mm (H), W = 0.4mm, T = 35um, 导体居中。
    sl = Stripline()
    sl.set(P.EPSILONR, 4.3).set(P.TAND, 0.02).set(P.MURC, 1.0).set(P.SIGMA, 5.8e7)
    h = 1.6 * MM
    t = 35.0 * MICRON
    sl.set(P.H, h).set(P.T, t).set(P.STRIPLINE_A, (h - t) / 2.0)
    sl.set(P.PHYS_WIDTH, 0.4 * MM).set(P.PHYS_LEN, 100.0 * MM)
    sl.set(P.FREQUENCY, 1.0e9).set(P.ANG_L, 0.0)

    sl.analyse()
    z0_orig_sl = sl.analysis_results[P.Z0][0]
    print(f"带状线 分析: Z0 = {z0_orig_sl:.4f} ohm, eps_eff = {sl.eps_eff:.5f}, "
          f"loss_cond = {sl.loss_conductor_db:.4f} dB, "
          f"loss_diel = {sl.loss_dielectric_db:.4f} dB")

    sl.set(P.Z0, z0_orig_sl).set(P.ANG_L, math.pi / 2.0)
    ok_sl = sl.synthesize()
    w_synth_sl = sl.width
    sl.analyse()
    z0_check_sl = sl.analysis_results[P.Z0][0]
    print(f"带状线 综合: W = {w_synth_sl / MM:.5f} mm, Z0 复算 = {z0_check_sl:.4f} ohm, "
          f"相对误差 = {abs(z0_check_sl - z0_orig_sl) / z0_orig_sl * 100:.4f}%  (收敛={ok_sl})")
    print()


if __name__ == "__main__":
    _self_test()