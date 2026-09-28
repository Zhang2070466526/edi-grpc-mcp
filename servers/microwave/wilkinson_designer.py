#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多级威尔金森功分器设计工具
Multi-Section Wilkinson Power Divider Designer

输入：基板厚度、介电常数、中心频率、级数、端口阻抗（默认50Ω）
输出：各级微带线的线宽、线长和隔离电阻值

算法说明：
  - 臂阻抗：基于 Chebyshev 阻抗变换器综合（小反射近似）
  - 微带线尺寸：复用 transline.Microstrip（Hammerstad-Jensen 闭式公式 + 色散
     + 35 µm 导体厚度修正；综合方向由 transline 内部迭代反解线宽）
  - 隔离电阻：Cohn 方法 + 经验修正（初值，需用 EDA 优化）
"""

import math
import argparse
import sys

from servers.microwave.transline import Microstrip, P, MM, MICRON


# ============================================================================
# Part 1: Chebyshev 多项式与多级阻抗变换器综合
# ============================================================================

def chebyshev_poly(n, x):
    """计算第一类 Chebyshev 多项式 T_n(x) 的值"""
    if n == 0:
        return 1.0
    if n == 1:
        return x
    t_prev = 1.0   # T_0
    t_curr = x     # T_1
    for _ in range(2, n + 1):
        t_next = 2 * x * t_curr - t_prev
        t_prev = t_curr
        t_curr = t_next
    return t_curr


def chebyshev_poly_coeffs(n):
    """返回 T_n(x) 的多项式系数列表 [a_0, a_1, ..., a_n]"""
    if n == 0:
        return [1.0]
    if n == 1:
        return [0.0, 1.0]
    coeffs_prev = [1.0]          # T_0: [1]
    coeffs_curr = [0.0, 1.0]     # T_1: [0, 1]
    for k in range(2, n + 1):
        # T_k = 2x * T_{k-1} - T_{k-2}
        two_x_t = [0.0] + [2 * c for c in coeffs_curr]
        t_prev_pad = coeffs_prev + [0.0] * (len(two_x_t) - len(coeffs_prev))
        coeffs_next = [a - b for a, b in zip(two_x_t, t_prev_pad)]
        coeffs_prev = coeffs_curr
        coeffs_curr = coeffs_next
    return coeffs_curr


def poly_eval(coeffs, x):
    """多项式求值，coeffs[i] 是 x^i 的系数"""
    result = 0.0
    power = 1.0
    for c in coeffs:
        result += c * power
        power *= x
    return result


def chebyshev_expansion(n, alpha):
    """
    将 T_n(α·x) 展开为 Chebyshev 级数：T_n(αx) = Σ_{k=0}^{n} c_k T_k(x)
    返回系数列表 c = [c_0, c_1, ..., c_n]
    使用离散 Chebyshev 变换（DCT-I 型）。
    """
    # Step 1: 得到 T_n(y) 的多项式系数
    tn_coeffs = chebyshev_poly_coeffs(n)

    # Step 2: 代入 y = α·x，得到 T_n(αx) 的多项式系数
    p_coeffs = [a * (alpha ** i) for i, a in enumerate(tn_coeffs)]

    # Step 3: 离散 Chebyshev 变换求展开系数
    # 在 n+1 个极值点采样：x_j = cos(jπ/n), j = 0..n
    m = n + 1
    x_samples = [math.cos(j * math.pi / n) for j in range(m)]
    p_samples = [poly_eval(p_coeffs, x) for x in x_samples]

    c = []
    for k in range(n + 1):
        s = 0.0
        for j in range(m):
            w = 0.5 if (j == 0 or j == n) else 1.0
            s += w * p_samples[j] * math.cos(k * j * math.pi / n)
        if k == 0 or k == n:
            ck = s / n
        else:
            ck = 2 * s / n
        c.append(ck)
    return c


def chebyshev_transformer(z0, zl, n, fbw):
    """
    综合 N 节 Chebyshev 四分之一波长阻抗变换器。
    采用小反射近似（small-reflection approximation），阻抗比 < 3 时精度良好。

    原理：
      - 对称变换器有 N+1 个阻抗阶跃（ρ_0 ... ρ_N），对应 N 段传输线
      - 小反射近似：Γ_in(ω) ≈ Σ_{k=0}^{N} ρ_k e^{-jkω} （ω = 2θ）
      - Chebyshev 等波纹响应通过匹配 Chebyshev 展开系数获得

    参数：
        z0  : 源端阻抗 (Ω)
        zl  : 负载阻抗 (Ω)
        n   : 节数
        fbw : 相对带宽（fractional bandwidth），如 1.0 = 100%

    返回：
        各节线特性阻抗列表 [Z_line_1, Z_line_2, ..., Z_line_N]（从源到负载）
    """
    if n < 1:
        raise ValueError("节数 n 必须 >= 1")
    if fbw <= 0 or fbw >= 2:
        raise ValueError("相对带宽 fbw 必须在 (0, 2) 之间")

    R = zl / z0
    reverse = False
    if R < 1:
        z0, zl = zl, z0
        R = zl / z0
        reverse = True

    # 带边电角度
    theta_m = math.pi / 2 * (1 - fbw / 2)
    M = 1.0 / math.cos(theta_m)  # sec(θ_m)

    # DC 反射系数（小反射近似）
    Gamma_dc = 0.5 * math.log(R)

    # 通带最大反射 = DC反射 / T_n(M)
    T_n_M = chebyshev_poly(n, M)

    # Chebyshev 展开：T_n(M·cos(ω/2)) = Σ c_k cos(kω/2)
    c = chebyshev_expansion(n, M)

    # 阶跃反射系数 ρ_0 ... ρ_n（共 n+1 个）
    # 匹配：A(ω) = Gamma_dc * T_n(M cos(ω/2)) / T_n(M)
    # 其中 A(ω) 是对称变换器响应的实部包络
    #   A(ω) = 2 Σ_{k=0}^{⌊n/2⌋-1} ρ_k cos((n-2k)ω/2) + center
    rho = [0.0] * (n + 1)
    scale = Gamma_dc / T_n_M

    for k in range(n + 1):
        if (k % 2) != (n % 2):
            continue
        idx = (n - k) // 2  # 对应的 ρ 索引
        if k == 0 and n % 2 == 0:
            rho[idx] = scale * c[k]
        else:
            rho[idx] = scale * c[k] / 2.0

    # 对称性：ρ_k = ρ_{n-k}
    for k in range((n + 1) // 2):
        rho[n - k] = rho[k]

    # 计算各节线特性阻抗
    # ρ_k 是第 k 个阶跃（从 Z_{k,left} 到 Z_{k,right}）
    # 第 1 节线阻抗：Z_line_1 = z0 * (1+ρ_0) / (1-ρ_0)
    # 第 k 节线阻抗：从第 k-1 节线通过 ρ_{k-1} 得到
    z_line = []
    z_curr = z0
    for k in range(n):
        z_next = z_curr * (1 + rho[k]) / (1 - rho[k])
        z_line.append(z_next)
        z_curr = z_next

    # 验证：最后一阶跃 ρ_n 应从 Z_line_n 变到 zl
    zl_calc = z_curr * (1 + rho[n]) / (1 - rho[n])
    # 小反射近似有微小偏差，整体几何缩放修正
    if abs(zl_calc - zl) / zl > 1e-6:
        ratio = math.log(zl / z0) / math.log(zl_calc / z0)
        z_line = [z0 * (z / z0) ** ratio for z in z_line]

    if reverse:
        z_line = z_line[::-1]

    return z_line


# ============================================================================
# Part 2: 微带线计算（Hammerstad 闭式公式）
# ============================================================================

def _microstrip_instance(h_mm, er, f0_GHz):
    """构造并初始化 transline.Microstrip（mm/GHz → SI，含默认电导率/磁导率/无覆盖层）。"""
    tl = Microstrip()
    tl.set(P.EPSILONR, er).set(P.H, h_mm * MM).set(P.H_T, 1.0e20)
    tl.set(P.T, 35.0 * MICRON).set(P.FREQUENCY, f0_GHz * 1e9)
    tl.set(P.SIGMA, 5.8e7).set(P.MUR, 1.0).set(P.MURC, 1.0).set(P.ROUGH, 0.0)
    return tl


def microstrip_z_and_eeff(W, h, er, f0_GHz=1.0):
    """
    给定线宽 W、基板厚度 h、介电常数 er，返回 (Z0, eeff)。
    复用 transline.Microstrip（Hammerstad-Jensen + 色散 + 厚度 + 覆盖层修正）。
    W、h 单位一致（mm），结果与 calculate_transline 同源。
    """
    tl = _microstrip_instance(h, er, f0_GHz)
    tl.set(P.PHYS_WIDTH, W * MM)
    tl.analyse()
    return tl.z0, getattr(tl, "eps_eff", er)


def microstrip_width_from_z(Z0_target, h, er, f0_GHz=1.0, tol=1e-6, max_iter=100):
    """
    给定目标阻抗 Z0_target (Ω)、基板厚度 h、介电常数 er，
    反解线宽 W，返回 (W, eeff)。复用 transline.Microstrip 的综合（内部牛顿迭代）。
    tol/max_iter 仅保留签名兼容，实际收敛由 transline 内部控制。
    """
    tl = _microstrip_instance(h, er, f0_GHz)
    tl.set(P.Z0, Z0_target).set(P.ANG_L, math.pi / 2.0)
    if not tl.synthesize():
        raise ValueError(f"目标阻抗 {Z0_target:.1f} Ω 超出微带线可实现范围")
    return tl.width / MM, getattr(tl, "eps_eff", er)


def microstrip_quarterwave_length(f0_GHz, Z0, h, er):
    """
    计算中心频率 f0 (GHz) 处四分之一波长微带线的物理长度。
    返回 (length_mm, eeff)。
    """
    W, eeff = microstrip_width_from_z(Z0, h, er, f0_GHz)
    c = 299792458.0
    lambda0_mm = c / (f0_GHz * 1e9) * 1000
    lambda_g = lambda0_mm / math.sqrt(eeff)
    length = lambda_g / 4
    return length, eeff


# ============================================================================
# Part 3: 多级威尔金森隔离电阻计算
# ============================================================================

def wilkinson_isolation_resistors(z0, n, arm_z_lines):
    """
    计算多级威尔金森功分器的各级隔离电阻（初值估算）。

    方法：
      - N=1: R = 2*Z0（经典结果）
      - N=2: Cohn 近似公式
      - N>2: 经验公式（基于奇模阻抗分析 + 修正因子）

    参数：
        z0          : 端口阻抗 (Ω)
        n           : 级数
        arm_z_lines : 每臂各节线的特性阻抗列表 [Z1, Z2, ..., Zn]
                      （从公共端侧到输出端侧）

    返回：
        隔离电阻列表 [R1, R2, ..., Rn]，R1 靠近公共端，Rn 靠近输出端
    """
    if n == 1:
        return [2 * z0]

    if n == 2:
        Za1 = arm_z_lines[0]  # 第一节臂阻抗（靠近公共端）
        Za2 = arm_z_lines[1]  # 第二节臂阻抗（靠近输出端）

        # Cohn 两级近似：基于奇模等效阻抗
        # R1 (输入端): 约等于 2 * 几何平均 × 修正
        # R2 (输出端): 约等于 2*Z0
        R1_geom = 2 * math.sqrt(Za1 * Za2 * (z0 / Za2) ** 0.5)
        R2_geom = 2 * z0

        # 调整：R1 应大于 R2，且 R1 ≈ 2*Za1 * (Za1/Z0)^(some power)
        R1 = 2 * Za1 * (Za1 / z0) ** 0.35
        R2 = 2 * z0 * (Za2 / z0) ** 0.15

        # 取几何平均折中
        R1 = math.sqrt(R1 * R1_geom)
        R2 = math.sqrt(R2 * R2_geom)

        return [R1, R2]

    # N > 2：经验公式
    # 隔离电阻值从输入到输出递减，输入端 ~ 2*Z_arm * factor，输出端 ~ 2*Z0
    resistors = []
    for k in range(n):
        z_arm = arm_z_lines[k]

        # 修正因子：输入端大，输出端≈1
        # 第 k 节（0-indexed），k=0 最靠输入，k=n-1 最靠输出
        if n > 1:
            factor = 1.0 + 0.6 * (1.0 - k / (n - 1))
        else:
            factor = 1.0

        R_k = 2 * z_arm * factor

        # 输出端最后一个电阻接近 2*Z0
        if k == n - 1:
            R_k = 2 * z0 * (z_arm / z0) ** 0.2

        resistors.append(R_k)

    return resistors


# ============================================================================
# Part 4: 主设计函数
# ============================================================================

def _solve_chebyshev_arg(n, target):
    """数值求解 T_n(M) = target (M > 1, target >= 1)，返回 M"""
    if target <= 1:
        return 1.0
    low = 1.0
    high = 2.0
    while chebyshev_poly(n, high) < target:
        high *= 2
    for _ in range(60):
        mid = (low + high) / 2
        val = chebyshev_poly(n, mid)
        if val < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def design_wilkinson(h_mm, er, f0_GHz, n_sections, z0=50.0, fbw=None, ripple_db=None):
    """
    设计多级威尔金森功分器。

    参数：
        h_mm       : 基板厚度 (mm)
        er         : 相对介电常数
        f0_GHz     : 中心频率 (GHz)
        n_sections : 级数（每臂的四分之一波长节数）
        z0         : 端口阻抗 (Ω)，默认 50
        fbw        : 相对带宽（fractional bandwidth），如 1.0 = 100%
                     若为 None，则根据 ripple_db 自动计算
        ripple_db  : 通带最小回波损耗 (dB)，默认 20 dB（Chebyshev 等波纹）

    返回：
        dict 包含所有设计结果
    """
    # ---- 1. 确定带宽 ----
    if fbw is None:
        if ripple_db is None:
            ripple_db = 20.0
        # 回波损耗 -> 反射系数幅值
        Gamma_m = 10 ** (-ripple_db / 20)
        # 偶模阻抗比 R = 2（等功分：Z0 -> Z0/2）
        Gamma_dc = 0.5 * math.log(2)
        T_n_M = abs(Gamma_dc / Gamma_m)
        if T_n_M <= 1.0:
            fbw = 1.8  # 带宽上限保护
        else:
            M = _solve_chebyshev_arg(n_sections, T_n_M)
            theta_m = math.acos(1 / M)
            fbw = 2 - 4 * theta_m / math.pi
            if fbw > 1.95:
                fbw = 1.95
    else:
        # 由带宽反推回波损耗
        theta_m = math.pi / 2 * (1 - fbw / 2)
        M = 1 / math.cos(theta_m)
        T_n_M = chebyshev_poly(n_sections, M)
        Gamma_dc = 0.5 * math.log(2)
        Gamma_m = abs(Gamma_dc / T_n_M)
        ripple_db = -20 * math.log10(Gamma_m)

    # ---- 2. 偶模等效 Chebyshev 变换器 ----
    # 公共端 Z0 -> 等效负载 Z0/2（两臂并联）
    z_source = z0
    z_load = z0 / 2.0

    z_lines_eq = chebyshev_transformer(z_source, z_load, n_sections, fbw)

    # ---- 3. 每臂阻抗（×2，因为两臂并联）----
    arm_z_lines = [2 * z for z in z_lines_eq]

    # ---- 4. 计算各级微带线尺寸 ----
    sections = []
    for i in range(n_sections):
        z_arm = arm_z_lines[i]
        W, eeff = microstrip_width_from_z(z_arm, h_mm, er, f0_GHz)
        L, _ = microstrip_quarterwave_length(f0_GHz, z_arm, h_mm, er)
        sections.append({
            'section': i + 1,
            'z_arm': z_arm,
            'width_mm': W,
            'length_mm': L,
            'eeff': eeff,
        })

    # ---- 5. 计算隔离电阻 ----
    resistors = wilkinson_isolation_resistors(z0, n_sections, arm_z_lines)

    # ---- 6. 附加信息 ----
    c = 299792458.0
    lambda0_mm = c / (f0_GHz * 1e9) * 1000

    result = {
        'h_mm': h_mm,
        'er': er,
        'f0_GHz': f0_GHz,
        'n_sections': n_sections,
        'z0': z0,
        'fbw': fbw,
        'ripple_db': ripple_db,
        'lambda0_mm': lambda0_mm,
        'arm_z_lines': arm_z_lines,
        'sections': sections,
        'isolation_resistors': resistors,
    }

    return result


# ============================================================================
# Part 5: 结果打印
# ============================================================================

def print_results(result):
    """格式化打印设计结果"""
    print("=" * 72)
    print("  多级威尔金森功分器设计结果")
    print("  Multi-Section Wilkinson Power Divider Design")
    print("=" * 72)
    print()
    print("┌─ 设计参数 ─────────────────────────────────────────────────────────┐")
    print(f"│  基板厚度 h        : {result['h_mm']:.4f} mm", " " * 41, "│", sep="")
    print(f"│  相对介电常数 εr   : {result['er']:.2f}", " " * 45, "│", sep="")
    print(f"│  中心频率 f0       : {result['f0_GHz']:.4f} GHz", " " * 38, "│", sep="")
    print(f"│  自由空间波长 λ0   : {result['lambda0_mm']:.4f} mm", " " * 37, "│", sep="")
    print(f"│  端口阻抗 Z0       : {result['z0']:.1f} Ω", " " * 43, "│", sep="")
    print(f"│  级数 N            : {result['n_sections']}", " " * 48, "│", sep="")
    print(f"│  相对带宽 FBW      : {result['fbw']*100:.1f} %", " " * 42, "│", sep="")
    print(f"│  最小回波损耗(估)  : {result['ripple_db']:.1f} dB", " " * 40, "│", sep="")
    print("└───────────────────────────────────────────────────────────────────┘")
    print()

    print("┌─ 每臂各级传输线阻抗 ───────────────────────────────────────────────┐")
    print(f"│  {'节号':>4}  {'阻抗 Z_arm (Ω)':>14}  {'位置':<16}                    │")
    print(f"│  {'─'*4}  {'─'*14}  {'─'*16}                    │")
    for i, z in enumerate(result['arm_z_lines']):
        pos = "公共端侧" if i == 0 else ("输出端侧" if i == len(result['arm_z_lines']) - 1 else "中间")
        print(f"│  {i+1:>4}  {z:>14.3f}  {pos:<16}                    │")
    print("└───────────────────────────────────────────────────────────────────┘")
    print()

    print("┌─ 各级微带线尺寸（中心频率 f0 处的 λ/4 线）────────────────────────┐")
    print(f"│  {'节号':>4}  {'阻抗 (Ω)':>10}  {'线宽 (mm)':>10}  {'线长 (mm)':>10}  {'εeff':>8}    │")
    print(f"│  {'─'*4}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*8}    │")
    for sec in result['sections']:
        print(f"│  {sec['section']:>4}  {sec['z_arm']:>10.3f}  "
              f"{sec['width_mm']:>10.4f}  {sec['length_mm']:>10.4f}  "
              f"{sec['eeff']:>8.4f}    │")
    print("└───────────────────────────────────────────────────────────────────┘")
    print()

    print("┌─ 隔离电阻（初值估算，建议用 ADS/AWR 优化）────────────────────────┐")
    print(f"│  {'电阻':>6}  {'阻值 (Ω)':>10}  {'位置':<22}                  │")
    print(f"│  {'─'*6}  {'─'*10}  {'─'*22}                  │")
    for i, r in enumerate(result['isolation_resistors']):
        if i == 0:
            pos = "靠近公共端"
        elif i == len(result['isolation_resistors']) - 1:
            pos = "靠近输出端"
        else:
            pos = f"第 {i+1} 节中间"
        print(f"│  R{i+1:>3}  {r:>10.2f}  {pos:<22}                  │")
    print("└───────────────────────────────────────────────────────────────────┘")
    print()

    print("【说明】")
    print("  1. 臂阻抗基于 Chebyshev 阻抗变换器综合（小反射近似），阻抗比<3时精度良好。")
    print("  2. 微带线尺寸由 transline.Microstrip 计算（Hammerstad-Jensen 闭式公式 + 色散，含默认 35 µm 导体厚度修正）；输出为几何尺寸，未含导体/介质损耗。")
    print("  3. 隔离电阻为初值估算，实际电路中需用 ADS/AWR 等电磁仿真工具优化。")
    print("  4. 线长为中心频率处的四分之一波长，未考虑 T 型结等不连续效应的影响。")
    print("  5. 本程序输出为初始设计值，最终参数需通过全波仿真验证和优化。")
    print("=" * 72)


# ============================================================================
# Part 6: 命令行接口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='多级威尔金森功分器设计工具 (Multi-Section Wilkinson Power Divider Designer)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本用法：FR4 基板 0.8mm, er=4.4, 2.45GHz, 1级, 50Ω
  python wilkinson_designer.py -H 0.8 -er 4.4 -f 2.45 -n 1

  # 指定带宽（100% 相对带宽）
  python wilkinson_designer.py -H 0.508 -er 3.48 -f 10 -n 3 -fbw 1.0

  # 指定回波损耗（25 dB）
  python wilkinson_designer.py -H 0.254 -er 9.9 -f 30 -n 4 -ripple 25

  # 自定义端口阻抗（75Ω）
  python wilkinson_designer.py -H 1.0 -er 4.4 -f 1 -n 2 -z0 75
        """
    )
    parser.add_argument('-H', '--thickness', type=float, required=True,
                        help='基板厚度 (mm)')
    parser.add_argument('-er', '--epsilon', type=float, required=True,
                        help='相对介电常数 εr')
    parser.add_argument('-f', '--frequency', type=float, required=True,
                        help='中心频率 (GHz)')
    parser.add_argument('-n', '--sections', type=int, required=True,
                        help='级数（每臂的四分之一波长节数）')
    parser.add_argument('-z0', '--impedance', type=float, default=50.0,
                        help='端口阻抗 (Ω)，默认 50')
    parser.add_argument('-fbw', '--fractional-bandwidth', type=float, default=None,
                        help='相对带宽 (如 1.0 = 100%%)，与回波损耗二选一')
    parser.add_argument('-ripple', '--ripple-db', type=float, default=None,
                        help='通带最小回波损耗 (dB)，默认 20 dB (Chebyshev 等波纹)')

    args = parser.parse_args()

    if args.sections < 1:
        print("错误：级数必须 >= 1", file=sys.stderr)
        sys.exit(1)

    try:
        result = design_wilkinson(
            h_mm=args.thickness,
            er=args.epsilon,
            f0_GHz=args.frequency,
            n_sections=args.sections,
            z0=args.impedance,
            fbw=args.fractional_bandwidth,
            ripple_db=args.ripple_db,
        )
        print_results(result)
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
