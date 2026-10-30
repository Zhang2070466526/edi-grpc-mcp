"""
广义切比雪夫带通滤波器阶数与传输零点估计器
=====================================================
输入: 通带频率 + 阻带抑制点(dB) + 回波损耗 + 谐振器无载Q值
输出: 阶数 N + 有限传输零点数量 k 及位置(归一化低通 / 带通域)

理论基础:
  - Cameron 1999, IEEE T-MTT 47(4):433-442 (N+2 耦合矩阵综合)
  - Cameron 1982, ESA Journal 6(1):83-95 (不对称零点生成)
  - Milosavljević 2005 (不对称零点闭式解, k≤4)

通带等纹波约束: |S11|²_max = ε²/(1+ε²)
能量守恒:       |F|² + (1/ε²)|P|² = |E|²
"""

import numpy as np
from scipy.optimize import minimize, least_squares
import json
from typing import List, Tuple, Optional, Dict, Any


# ============================================================
# Step 1: 规范归一化
# ============================================================

def bandpass_to_lowpass(f_p1: float, f_p2: float, f_array: np.ndarray) -> Tuple[np.ndarray, float, float]:
    """带通 → 归一化低通变换
    Ω = (f/f0 - f0/f) / FBW, 通带边 |Ω|=1
    """
    f0 = float(np.sqrt(f_p1 * f_p2))
    FBW = (f_p2 - f_p1) / f0
    Omega = (np.asarray(f_array, dtype=float) / f0 - f0 / np.asarray(f_array, dtype=float)) / FBW
    return Omega, f0, FBW


def lowpass_to_bandpass_freq(Omega: float, f0: float, FBW: float) -> float:
    """归一化低通 Ω → 带通域频率(正解, 取>0)
    解 Ω*FBW = x - 1/x, x=f/f0 → x² - (Ω*FBW)*x - 1 = 0
    正根: x = [Ω*FBW + sqrt((Ω*FBW)² + 4)] / 2 (>0 恒成立, Ω<0 也取正根)
    """
    a = Omega * FBW
    x = (a + np.sqrt(a * a + 4.0)) / 2.0
    return float(f0 * x)


def return_loss_to_epsilon_sq(RL_dB: float) -> float:
    """回波损耗(dB, 负值) → ε²
    |S11|²_max = 10^(RL/10), ε² = |S11|²_max / (1 - |S11|²_max)
    RL=-20dB → ε² ≈ 0.010101
    """
    s11_sq = 10.0 ** (RL_dB / 10.0)
    return s11_sq / (1.0 - s11_sq)


def epsilon_sq_to_ripple_dB(epsilon_sq: float) -> float:
    """ε² → 通带纹波(dB) = 10*log10(1+ε²)"""
    return 10.0 * np.log10(1.0 + epsilon_sq)


def expand_stopband_specs(stopband_specs, f_p1: float, f_p2: float,
                          n_per_band: int = 41) -> Tuple[np.ndarray, np.ndarray]:
    """把阻带规范(单点 / 频带)展开为采样点集

    条目格式:
      (f, A)            -> 抑制点
      (f_lo, f_hi, A)   -> 抑制频带 [f_lo, f_hi] 内均需 ≥ A
    频带采样在靠近通带的一端加密(该处衰减变化最剧烈, 通常是最坏点)
    返回 (f_points, A_points)
    """
    f_pts, A_pts = [], []
    for spec in stopband_specs:
        if len(spec) == 2:
            f_pts.append(float(spec[0]))
            A_pts.append(float(spec[1]))
            continue
        f_lo, f_hi, A_s = float(spec[0]), float(spec[1]), float(spec[2])
        t = np.linspace(0.0, 1.0, n_per_band)
        if f_hi <= f_p1:          # 左侧频带, 靠近通带端为 f_hi
            s = 1.0 - (1.0 - t) ** 2
        elif f_lo >= f_p2:        # 右侧频带, 靠近通带端为 f_lo
            s = t ** 2
        else:                     # 与通带重叠(非物理, 兜底均匀)
            s = t
        f_band = f_lo + (f_hi - f_lo) * s
        f_pts.extend(f_band.tolist())
        A_pts.extend([A_s] * n_per_band)
    return np.array(f_pts, dtype=float), np.array(A_pts, dtype=float)


# ============================================================
# Step 2: 阶数下界(无有限零点, 标准切比雪夫)
# ============================================================

def chebyshev_order_lower_bound(Omega_s_list: np.ndarray, A_s_list: np.ndarray,
                                epsilon_sq: float) -> float:
    """对每个阻带点单独算切比雪夫阶数, 取最大
    N_i ≥ acosh(√((10^(As/10)-1)/ε²)) / acosh(|Ω_s|)
    """
    orders = []
    for Om, As in zip(Omega_s_list, A_s_list):
        if abs(Om) <= 1.0 + 1e-9:
            continue  # 阻带点在通带内或边界, 无意义
        ratio = np.sqrt((10.0 ** (As / 10.0) - 1.0) / epsilon_sq)
        if ratio <= 1.0:
            orders.append(0.0)
        else:
            N_i = np.arccosh(ratio) / np.arccosh(abs(Om))
            orders.append(float(N_i))
    return max(orders) if orders else 0.0


# ============================================================
# Step 3: 广义切比雪夫多项式综合(P, F, E)
# ============================================================

def _poly_negate_s(poly: np.ndarray) -> np.ndarray:
    """返回 P*(-s) 系数(降幂), 即 a_i → conj(a_i) * (-1)^(N-i)
    复系数多项式需同时共轭系数和替换 s→-s, 使 E*E*(-s)=F*F*(-s)+(1/eps^2)*P*P*(-s)
    在 s=j*Omega 处给出 |E|^2=|F|^2+(1/eps^2)|P|^2 (正确的能量守恒)
    """
    N = len(poly) - 1
    signs = (-1.0) ** (N - np.arange(len(poly)))
    return np.conj(poly) * signs


def _poly_add(p1: np.ndarray, p2: np.ndarray) -> np.ndarray:
    """多项式加法(降幂表示, 右对齐即常数项对齐)"""
    n1, n2 = len(p1), len(p2)
    if n1 == n2:
        return p1 + p2
    if n1 > n2:
        return p1 + np.concatenate([np.zeros(n1 - n2), p2])
    return np.concatenate([np.zeros(n2 - n1), p1]) + p2


def _solve_reflection_zeros(N: int, finite_zeros: np.ndarray,
                            epsilon_sq: float, P: np.ndarray) -> np.ndarray:
    """用 Remez 迭代求 N 个反射零点(实数, |r|<1)

    等纹波条件(Cameron): 通带 [-1,1] 内 |S11(j\\Omega)|^2 在 N+1 个极值点
    达到最大值 eps^2/(1+eps^2). 代数等价: |F_tilde(Omega)| = |P_tilde(Omega)|,
    符号交替 -> F_tilde(Omega_k) = (-1)^k * P_tilde(Omega_k).

    Remez 迭代(每步解线性系统, 不走极点留数法):
      1. 初始化 N+1 个极值点(标准切比雪夫极值 cos(k*pi/N))
      2. 解 Vandermonde 线性系统 F_tilde(Omega_k) = (-1)^k * P_tilde(Omega_k)
         求 F_tilde 的 N+1 个升幂系数
      3. 求 |S11|^2 实际极值: F_tilde'*P_tilde - F_tilde*P_tilde' = 0
         在 (-1,1) 内的实根 + 边界 +-1
      4. 更新极值点, 迭代至收敛
      5. 反射零点 = F_tilde 的根(实数, |r|<1)
    """
    if N <= 0:
        return np.array([])
    if N == 1:
        return np.array([0.0])

    from numpy.polynomial.polynomial import polymul, polyder, polyval

    def _pad_add(p1, p2):
        """升幂多项式加法(常数项对齐, 末端补零)"""
        p1 = np.asarray(p1, dtype=float)
        p2 = np.asarray(p2, dtype=float)
        n1, n2 = len(p1), len(p2)
        if n1 >= n2:
            return p1 + np.concatenate([p2, np.zeros(n1 - n2)])
        return np.concatenate([p1, np.zeros(n2 - n1)]) + p2

    # P_tilde(Omega) 实多项式, 归一化 |P_tilde(0)|=1, 升幂
    k = len(finite_zeros)
    if k > 0:
        Ptilde_desc = np.poly(np.asarray(finite_zeros, dtype=float))
        Ptilde_asc = Ptilde_desc[::-1]  # 升幂
        P0 = Ptilde_asc[0]
        if abs(P0) > 1e-30:
            Ptilde_asc = Ptilde_asc / abs(P0)
    else:
        Ptilde_asc = np.array([1.0])

    Ptilde_prime_asc = polyder(Ptilde_asc)  # P_tilde' 升幂

    # 初始极值点: 标准切比雪夫极值 cos(k*pi/N), k=0,...,N (从 +1 递减到 -1)
    extrema = np.cos(np.arange(N + 1) * np.pi / N)

    c_asc = None  # F_tilde 升幂系数
    for iteration in range(200):
        # --- Step 2: 解线性系统 V*c = b ---
        # V[k,j] = Omega_k^j (Vandermonde, 升幂)
        # b_k = (-1)^k * P_tilde(Omega_k)
        V = np.vander(extrema, N + 1, increasing=True)
        Ptilde_vals = polyval(extrema, Ptilde_asc)
        signs = (-1.0) ** np.arange(N + 1)
        b = signs * Ptilde_vals

        try:
            c_asc_new = np.linalg.solve(V, b)
        except np.linalg.LinAlgError:
            c_asc_new = np.linalg.lstsq(V, b, rcond=None)[0]

        # --- Step 3: 求 |S11|^2 的内部极值 ---
        # 极值条件: F_tilde'*P_tilde - F_tilde*P_tilde' = 0
        Ftilde_prime_asc = polyder(c_asc_new)
        FP_asc = polymul(Ftilde_prime_asc, Ptilde_asc)   # F_tilde'*P_tilde 升幂
        FPp_asc = polymul(c_asc_new, Ptilde_prime_asc)  # F_tilde*P_tilde' 升幂
        diff_asc = _pad_add(FP_asc, -FPp_asc)            # 差, 升幂

        # 求根(np.roots 需降幂)
        diff_desc = diff_asc[::-1]
        while len(diff_desc) > 1 and abs(diff_desc[0]) < 1e-14:
            diff_desc = diff_desc[1:]

        if len(diff_desc) > 1:
            roots_diff = np.roots(diff_desc)
            real_roots = np.real(roots_diff[np.abs(np.imag(roots_diff)) < 1e-6])
            internal = real_roots[(real_roots > -1 + 1e-8) &
                                  (real_roots < 1 - 1e-8)]
        else:
            internal = np.array([])

        internal = np.sort(internal)[::-1]  # 递减(从近+1到近-1)

        # 需要 N-1 个内部极值
        if len(internal) > N - 1:
            # 多了, 按 |F_tilde| 值排序取最大 N-1 个
            F_abs = np.abs(polyval(internal, c_asc_new))
            idx = np.argsort(F_abs)[::-1][:N - 1]
            internal = np.sort(internal[idx])[::-1]
        elif len(internal) < N - 1:
            # 不够, 用切比雪夫极值补充
            cheb_int = np.cos(np.arange(1, N) * np.pi / N)
            for ce in cheb_int:
                if len(internal) >= N - 1:
                    break
                if len(internal) == 0 or np.min(np.abs(internal - ce)) > 0.05:
                    internal = np.sort(np.append(internal, ce))[::-1]

        # 新极值: +1, internal..., -1 (递减)
        new_extrema = np.concatenate([[1.0], internal, [-1.0]])

        # --- 收敛检查 ---
        if c_asc is not None:
            ext_delta = np.max(np.abs(new_extrema - extrema))
            coef_delta = np.max(np.abs(c_asc_new - c_asc))
            if ext_delta < 1e-13 and coef_delta < 1e-13:
                c_asc = c_asc_new
                extrema = new_extrema
                break

        c_asc = c_asc_new
        extrema = new_extrema

    # --- Step 5: 提取反射零点 = F_tilde 的根 ---
    c_desc = c_asc[::-1]
    while len(c_desc) > 1 and abs(c_desc[0]) < 1e-14:
        c_desc = c_desc[1:]

    roots = np.roots(c_desc)
    real_mask = np.abs(np.imag(roots)) < 1e-6
    r = np.real(roots[real_mask])
    r = np.clip(r, -0.999, 0.999)

    if len(r) != N:
        # 退化: 标准切比雪夫反射零点
        j_arr = np.arange(1, N + 1)
        return np.sort(np.cos((2 * j_arr - 1) * np.pi / (2 * N)))

    return np.sort(r)
def synthesize_polys(N: int, finite_zeros: np.ndarray, epsilon_sq: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """综合广义切比雪夫滤波器的 P, F, E 多项式(系数降幂)

    N: 阶数(谐振器数)
    finite_zeros: 有限传输零点位置(归一化低通虚轴频率, 实数, |z|>1)
    """
    k = len(finite_zeros)
    finite_zeros = np.asarray(finite_zeros, dtype=float)

    # ---------- P(s) ----------
    if k > 0:
        P_roots = 1j * finite_zeros  # 零点在虚轴 s = j*Ω_z
        P = np.poly(P_roots)
        P0 = np.polyval(P, 0.0)
        if abs(P0) > 1e-30:
            P = P / np.abs(P0)  # 归一化 P(0) 为实数 1
    else:
        P = np.array([1.0])

    # ---------- F(s) ----------
    if N == 0:
        F = np.array([1.0])
    elif k == 0:
        # 标准切比雪夫: 反射零点闭式 r_j = cos((2j-1)π/(2N))
        j_arr = np.arange(1, N + 1)
        refl = np.cos((2 * j_arr - 1) * np.pi / (2 * N))
        F = np.poly(1j * refl)  # 首一多项式
    else:
        refl = _solve_reflection_zeros(N, finite_zeros, epsilon_sq, P)
        F = np.poly(1j * refl)

    # Scale 归一化: |F(j·1)|² = |P(j·1)|² (由 |S11|²_max=ε²/(1+ε²) 在通带边推出)
    F_at_j1 = np.polyval(F, 1j)
    P_at_j1 = np.polyval(P, 1j)
    if np.abs(F_at_j1) > 1e-30:
        F = F * (np.abs(P_at_j1) / np.abs(F_at_j1))

    # ---------- E(s) ----------
    # E(s)E(-s) = F(s)F(-s) + (1/ε²) P(s)P(-s)
    FF_neg = np.convolve(F, _poly_negate_s(F))
    PP_neg = np.convolve(P, _poly_negate_s(P))
    EE_neg = _poly_add(FF_neg, (1.0 / epsilon_sq) * PP_neg)

    # 求根
    roots_EE = np.roots(EE_neg)
    tol = 1e-9

    # 取左半平面根(实部<0)
    re = np.real(roots_EE)
    left = roots_EE[re < -tol]
    boundary = roots_EE[np.abs(re) <= tol]
    # 纯虚轴上的共轭对, 取虚部>0 的那一半
    boundary_left = boundary[np.imag(boundary) > 0]
    all_left = np.concatenate([left, boundary_left]) if len(boundary_left) > 0 else left

    # 兜底: 若数量不足 N, 按虚部排序取上半
    if len(all_left) != N:
        idx = np.argsort(np.imag(roots_EE))
        all_left = roots_EE[idx[len(idx) - N:]]

    E = np.poly(all_left)
    # 归一化: 由能量守恒 E(0)² = F(0)² + (1/ε²)·P(0)² (不是简单的 E(0)=1)
    # 这样 |S21|² = (1/ε²)|P|²/|E|² 才正确
    E0 = np.polyval(E, 0.0 + 0j)
    F0 = np.polyval(F, 0.0 + 0j)
    P0 = np.polyval(P, 0.0 + 0j)
    target_E0_sq = abs(F0)**2 + (1.0 / epsilon_sq) * abs(P0)**2  # 模平方(能量守恒)
    if abs(E0) > 1e-30 and target_E0_sq > 1e-30:
        target_E0 = np.sqrt(target_E0_sq)
        E = E * (target_E0 / abs(E0))  # 实数缩放, 保持E的相位

    return P, F, E


# ============================================================
# Step 4: 衰减评估(无损 + 含Q值损耗)
# ============================================================

def evaluate_attenuation(Omega_array: np.ndarray, P: np.ndarray,
                         F: np.ndarray, E: np.ndarray, epsilon_sq: float) -> np.ndarray:
    """计算 |S21|² → 衰减(dB)"""
    eps = np.sqrt(epsilon_sq)
    s = 1j * np.asarray(Omega_array, dtype=float)
    P_val = np.polyval(P, s)
    E_val = np.polyval(E, s)
    S21 = P_val / (eps * E_val)
    S21_sq = np.abs(S21) ** 2
    S21_sq = np.clip(S21_sq, 1e-30, 1.0)
    return -10.0 * np.log10(S21_sq)


def evaluate_attenuation_with_Q(Omega_array: np.ndarray, P: np.ndarray, F: np.ndarray,
                                E: np.ndarray, epsilon_sq: float,
                                Q_unloaded: float, FBW: float) -> np.ndarray:
    """加入谐振器有限Q值后的衰减

    损耗模型 (Matthaei): 等效s平面整体左移 d_loss = 1/(Q*FBW)
    即 P, F, E 的根都做 s_root → s_root - d_loss 变换
    通带中心插损约 4.343 * N * d_loss dB (N阶谐振器总损耗)
    """
    d_loss = 1.0 / (Q_unloaded * FBW)  # 归一化低通域损耗系数

    # 对 P, F, E 的根都左移 d_loss (等效损耗)
    def shift_poly(poly, d):
        if len(poly) <= 1:
            return poly.copy()
        roots = np.roots(poly)
        roots_shifted = roots - d
        # 重新构造, 保持原scale (用首项系数)
        lead = poly[0]
        return np.poly(roots_shifted) * lead

    # Cameron 损耗模型：只把 F、E 的根左移 d_loss（等价 F(s+d)、E(s+d)）。
    # 传输零点多项式 P 不平移 —— 零点由交叉耦合决定、与 Q 无关，若一起平移会把零点推离 jω 轴，
    # 让阻带零点变成有限深度（物理上不对）。2026-10-30 修。
    P_lossy = P
    F_lossy = shift_poly(F, d_loss)
    E_lossy = shift_poly(E, d_loss)

    # 不做 s=0 处重归一化。原先在这里用 |E_lossy(0)|²≈|F_lossy(0)|²+(1/ε²)|P_lossy(0)|²
    # 重新缩放 E，等于把通带中心的损耗精确抵消回 0（2026-10-30 实测：Q=1000、N=3 时中心
    # 衰减 0.033 dB，而本函数 docstring 的 4.343*N*d_loss = 3.446 dB），导致 pass_max 与
    # 「通带可行」判定系统性偏低（低估 3~4 倍）。损耗已由根左移（等价 E(s+d)）体现，无需再缩放。
    return evaluate_attenuation(Omega_array, P_lossy, F_lossy, E_lossy, epsilon_sq)


# ============================================================
# Step 5: 有限零点位置优化
# ============================================================

def optimize_zeros(N: int, Omega_s_list: np.ndarray, A_s_list: np.ndarray,
                   zeros_init: np.ndarray, epsilon_sq: float,
                   Q_unloaded: float, FBW: float,
                   zero_side_signs: Optional[List[int]] = None) -> Tuple[np.ndarray, float]:
    """SLSQP 优化零点位置

    目标: 在所有阻带点满足要求下, 最小化零点数带来的过度衰减(避免零点贴在阻带点上)
    """
    zeros_init = np.asarray(zeros_init, dtype=float)
    A_s_arr = np.asarray(A_s_list, dtype=float)
    ripple_dB = 10 * np.log10(1 + epsilon_sq)  # 通带纹波
    max_passband_dB = ripple_dB * 3.0  # 通带内最大允许衰减(3倍纹波, 容忍SLSQP不完美)

    def objective(z):
        try:
            P, F, E = synthesize_polys(N, z, epsilon_sq)
            if Q_unloaded > 0:
                attens = evaluate_attenuation_with_Q(Omega_s_list, P, F, E,
                                                     epsilon_sq, Q_unloaded, FBW)
                # 通带采样(Q损耗下也要验证)
                Om_pass = np.linspace(-0.99, 0.99, 21)
                pass_attens = evaluate_attenuation_with_Q(Om_pass, P, F, E,
                                                          epsilon_sq, Q_unloaded, FBW)
            else:
                attens = evaluate_attenuation(Omega_s_list, P, F, E, epsilon_sq)
                Om_pass = np.linspace(-0.99, 0.99, 21)
                pass_attens = evaluate_attenuation(Om_pass, P, F, E, epsilon_sq)

            margins = attens - A_s_arr
            violations = np.maximum(0.0, -margins)
            # 只惩罚违约与通带超标; 不惩罚"远端过度衰减"(否则远端巨大的
            # 固有衰减会形成常数项, 淹没零点位置的影响, 导致零点漂移到无意义位置)
            # 通带判据用「波动」而非绝对衰减：含 Q 时绝对衰减里含插损（由 Q 与 FBW 决定，
            # 与阶数/零点选择无关），若按绝对衰减判会恒被罚（2026-10-30 修）
            pass_ripple = float(np.max(pass_attens) - np.min(pass_attens))
            pass_excess = np.maximum(0.0, pass_ripple - max(ripple_dB * 20.0, 1.0))

            return float(100.0 * np.max(violations) + np.sum(violations ** 2)
                         + 5.0 * np.sum(pass_excess))
        except Exception:
            return 1e6

    # 约束: |Ω_z| > 1.05 (严格在阻带, 不允许贴在通带边)
    cons = [{'type': 'ineq', 'fun': lambda z, i=i: abs(z[i]) - 1.05}
            for i in range(len(zeros_init))]
    # 零点间距约束 (防止重合)
    for i in range(len(zeros_init)):
        for j in range(i + 1, len(zeros_init)):
            cons.append({'type': 'ineq', 'fun': lambda z, i=i, j=j: abs(z[i] - z[j]) - 0.3})
    # 指定侧约束
    if zero_side_signs is not None:
        for i, sgn in enumerate(zero_side_signs):
            if sgn > 0:
                cons.append({'type': 'ineq', 'fun': lambda z, i=i: z[i] - 1.05})
            else:
                cons.append({'type': 'ineq', 'fun': lambda z, i=i: -z[i] - 1.05})

    bounds = [(1.05, 30.0) if v >= 0 else (-30.0, -1.05) for v in zeros_init]

    try:
        result = minimize(objective, zeros_init, method='SLSQP',
                          bounds=bounds, constraints=cons,
                          options={'maxiter': 500, 'ftol': 1e-9})
        return result.x, objective(result.x)
    except Exception:
        return zeros_init, objective(zeros_init)


# ============================================================
# Step 6: 主函数
# ============================================================

def estimate_filter_order(f_p1: float, f_p2: float,
                          stopband_specs: List[Tuple[float, float]],
                          RL_dB: float = -20.0,
                          Q_unloaded: Optional[float] = None,
                          max_zeros: int = 4,
                          verbose: bool = True) -> Dict[str, Any]:
    """估计广义切比雪夫带通滤波器的阶数和传输零点

    参数:
        f_p1, f_p2: 通带边频率(Hz)
        stopband_specs: 阻带规范列表, 每项为
            (f_s_Hz, A_s_dB)             -> 抑制点
            (f_lo_Hz, f_hi_Hz, A_s_dB)   -> 抑制频带 [f_lo, f_hi] 内均 ≥ A_s
        RL_dB: 回波损耗(dB, 负值)
        Q_unloaded: 谐振器无载Q值(None=无损)
        max_zeros: 最大允许的有限传输零点数

    枚举: 传输零点数 k = 0,1,2,... 递增为外层; 每个 k 求满足规范的最小阶数 N
          (须满足工程约束 k ≤ N/2), 直到该 k 下不存在可行 N 为止。
    """
    # ---- 阻带规范展开为采样点(支持单点 / 频带) ----
    f_s_arr, A_s_arr = expand_stopband_specs(stopband_specs, f_p1, f_p2)
    Omega_s, f0, FBW = bandpass_to_lowpass(f_p1, f_p2, f_s_arr)
    epsilon_sq = return_loss_to_epsilon_sq(RL_dB)

    if verbose:
        print("=" * 60)
        print("广义切比雪夫带通滤波器 - 阶数与零点估计")
        print("=" * 60)
        print(f"\n[规范归一化]")
        print(f"  中心频率 f0 = {f0:.6g} Hz")
        print(f"  相对带宽 FBW = {FBW:.6g} ({FBW*100:.2f}%)")
        print(f"  回波损耗 RL = {RL_dB} dB → ε² = {epsilon_sq:.6g}")
        print(f"  通带纹波 = {epsilon_sq_to_ripple_dB(epsilon_sq):.4f} dB")
        if Q_unloaded:
            print(f"  谐振器无载Q = {Q_unloaded}")
            print(f"  归一化损耗系数 d = {1.0/(Q_unloaded*FBW):.6g}")
        print(f"\n  阻带规范:")
        for i, spec in enumerate(stopband_specs):
            if len(spec) == 2:
                fs, As = spec
                Om_i = bandpass_to_lowpass(f_p1, f_p2, np.array([fs]))[0][0]
                side = "左" if Om_i < 0 else "右"
                print(f"    [{i}] 点 f={fs:.6g}Hz → Ω={Om_i:+.4f} ({side}侧), 抑制 ≥ {As} dB")
            else:
                f_lo, f_hi, As = spec
                Om_lo = bandpass_to_lowpass(f_p1, f_p2, np.array([f_lo]))[0][0]
                Om_hi = bandpass_to_lowpass(f_p1, f_p2, np.array([f_hi]))[0][0]
                side = "左" if Om_hi < 0 else "右"
                print(f"    [{i}] 带 {f_lo:.6g}~{f_hi:.6g}Hz → Ω∈[{Om_lo:+.3f},{Om_hi:+.3f}] "
                      f"({side}侧), 抑制 ≥ {As} dB")

    # ---- 阶数下界 ----
    N_lower = chebyshev_order_lower_bound(Omega_s, A_s_arr, epsilon_sq)
    if verbose:
        print(f"\n[阶数下界(无有限零点)]")
        print(f"  N_lower = {N_lower:.3f} → 取整 {int(np.ceil(N_lower))}")

    # ---- 搜索可行解 ----
    if verbose:
        print(f"\n[搜索可行解] (k 从 0 递增, 每个 k 求最小 N; N_max = {int(np.ceil(N_lower)) + max_zeros + 1})")

    N_max_search = int(np.ceil(N_lower)) + max_zeros + 1
    ripple_dB = epsilon_sq_to_ripple_dB(epsilon_sq)
    max_passband_dB = max(ripple_dB * 20.0, 1.0)  # 通带最大允许衰减(SLSQP近似误差)

    fail_state: dict = {'pb': None, 'sb': None, 'exc': 0}  # pb=通带波纹卡点；sb=阻带不足；exc=合成失败次数

    def _note_fail(N, k, cause, margin=None, ripple=None):
        """记录失败样本：passband 取最小 N（恰好阻带已满足的那个候选），stopband 取最大 N（最接近可行的）。"""
        if cause == 'passband':
            cur = fail_state['pb']
            if cur is None or N < cur['N']:
                fail_state['pb'] = {'N': N, 'k': k, 'ripple': ripple}
        else:
            cur = fail_state['sb']
            if cur is None or N > cur['N']:
                fail_state['sb'] = {'N': N, 'k': k, 'margin': margin}

    def check_feasibility(N, k, zeros):
        """检查(N, k, zeros)是否可行: 阻带点裕量≥0 且 通带衰减不超阈值"""
        try:
            P, F, E = synthesize_polys(N, zeros, epsilon_sq)
            if Q_unloaded:
                attens = evaluate_attenuation_with_Q(Omega_s, P, F, E, epsilon_sq, Q_unloaded, FBW)
                Om_pass = np.linspace(-0.99, 0.99, 21)
                pass_attens = evaluate_attenuation_with_Q(Om_pass, P, F, E, epsilon_sq, Q_unloaded, FBW)
            else:
                attens = evaluate_attenuation(Omega_s, P, F, E, epsilon_sq)
                Om_pass = np.linspace(-0.99, 0.99, 21)
                pass_attens = evaluate_attenuation(Om_pass, P, F, E, epsilon_sq)

            margins = attens - A_s_arr
            if np.min(margins) < 0:
                _note_fail(N, k, 'stopband', margin=float(np.min(margins)))
                return False, attens, pass_attens
            # 通带判据用「波纹」而非绝对衰减：绝对衰减含 Q 插损（与阶数无关），
            # 若按绝对值判，含 Q 的设计会因插损恒判不可行（2026-10-30 修）
            ripple_now = float(np.max(pass_attens) - np.min(pass_attens))
            if ripple_now > max_passband_dB:
                _note_fail(N, k, 'passband', ripple=ripple_now)
                return False, attens, pass_attens
            return True, attens, pass_attens
        except Exception:
            fail_state['exc'] += 1   # 合成/求值数值失败（多为高阶多项式），计入失败统计
            return False, None, None

    # 各阻带规范靠通带端的最坏点(低通域), 按"离通带最近(最紧)"排序, 作为零点锚点
    anchors = []
    for spec in stopband_specs:
        if len(spec) == 2:
            f_c = float(spec[0])
        else:
            f_c = float(spec[1]) if spec[1] <= f_p1 else float(spec[0])
        anchors.append(bandpass_to_lowpass(f_p1, f_p2, np.array([f_c]))[0][0])
    anchors = np.array(anchors)[np.argsort(np.abs(anchors))]

    # 枚举顺序: 外层为传输零点数 k = 0,1,2,...; 对每个 k 求"不受约束"的真实最小阶数 N
    #          (仅需 k ≤ N, 因为有限零点数不能超过阶数); 求出后再判断工程约束 k ≤ N/2,
    #          一旦某 k 的最小阶数使 k > N/2, 即停止(该 k 不计入可行解)
    solutions = []
    stop_note = None
    for k in range(max_zeros + 1):
        N_min = None
        sol = None
        for N in range(max(1, k), N_max_search + 1):
            # 零点初始位置: 按"离通带最近的阻带锚点"(各规范靠通带端最坏点)循环铺放
            # 锚点按紧度(|Ω| 小者优先)排序, 每条规范取一个; 超出规范条数时向外分层外推
            zeros_init = []
            side_signs = []
            m = len(anchors)
            for i in range(k):
                a = anchors[i % m]
                tier = i // m
                zeros_init.append(np.sign(a) * abs(a) * (1.05 + 0.6 * tier))
                side_signs.append(1 if a > 0 else -1)
            zeros_init = np.array(zeros_init) if zeros_init else np.array([])

            if k == 0:
                zeros_opt = np.array([])
            else:
                zeros_opt, _ = optimize_zeros(
                    N, Omega_s, A_s_arr, zeros_init, epsilon_sq,
                    Q_unloaded if Q_unloaded else 0.0, FBW,
                    zero_side_signs=side_signs
                )

            feasible, attens, pass_attens = check_feasibility(N, k, zeros_opt)
            if not feasible:
                continue

            N_min = N
            sol = {
                'N': N, 'k': k,
                'zeros_lowpass': np.array(zeros_opt),
                'min_margin_dB': float(np.min(attens - A_s_arr)),
                'min_atten_dB': float(np.min(attens)),
                'pass_max_dB': float(np.max(pass_attens)),
            }
            break  # 该 k 下不受约束的最小阶数 N

        if N_min is None:
            stop_note = f"k={k}: 在 N ≤ {N_max_search} 内无可行解"
            break
        # 工程约束 k ≤ N/2: 求出最小 N 后再判定, 不满足即停止
        if k > N_min // 2:
            stop_note = (f"k={k}: 最小阶数 N={N_min}, 但 k > N/2 ({k} > {N_min // 2}), "
                         f"违反工程约束 → 停止枚举")
            break
        solutions.append(sol)

    if not solutions:
        # 用最贴切的失败样本解释无解：优先「阻带已满足、只差通带波纹」的候选
        pb, sb = fail_state['pb'], fail_state['sb']
        if pb and Q_unloaded:
            reason = (f"N={pb['N']},k={pb['k']} 时阻带已满足，但通带波纹 {pb['ripple']:.2f} dB 超过 "
                      f"{max_passband_dB:.2f} dB 门限（Q={Q_unloaded:g}，Q*FBW={Q_unloaded * FBW:.3g}，"
                      f"损耗已使通带响应畸变）→ 提高谐振器 Q 或放宽回波损耗")
        elif sb:
            extra = f"，另有 {fail_state['exc']} 个候选在多项式合成阶段数值失败" if fail_state['exc'] else ""
            reason = (f"评估到 N={sb['N']},k={sb['k']} 时阻带最紧处仍差 {-sb['margin']:.2f} dB"
                      f"（N ≤ {N_max_search} 内无可解{extra}）→ 放宽抑制或增大 max_zeros")
        else:
            reason = 'no feasible solution'
        if verbose:
            print(f"\n✗ 未找到可行解: {reason}")
        return {'success': False, 'reason': reason}

    # 最优解 = 所有 k 中阶数最小的那一组
    best = min(solutions, key=lambda s: s['N'])

    def zero_row(sol):
        """归一化低通零点 → 带通域频率(Hz)"""
        return [lowpass_to_bandpass_freq(z, f0, FBW) for z in sol['zeros_lowpass']]

    # ---- 最终验证: 在细网格上复核阻带最坏点(避免采样漏掉谷底) ----
    P, F, E = synthesize_polys(best['N'], best['zeros_lowpass'], epsilon_sq)

    def atten_at(freqs):
        Om_v, _, _ = bandpass_to_lowpass(f_p1, f_p2, np.asarray(freqs, dtype=float))
        if Q_unloaded:
            return evaluate_attenuation_with_Q(Om_v, P, F, E, epsilon_sq, Q_unloaded, FBW)
        return evaluate_attenuation(Om_v, P, F, E, epsilon_sq)

    verify_rows = []
    for spec in stopband_specs:
        if len(spec) == 2:
            freqs, A_s = np.array([spec[0]], dtype=float), float(spec[1])
        else:
            freqs = np.linspace(float(spec[0]), float(spec[1]), 401)
            A_s = float(spec[2])
        att_v = atten_at(freqs)
        i_w = int(np.argmin(att_v))
        verify_rows.append((float(freqs[i_w]), float(att_v[i_w]), A_s))

    if verbose:
        print(f"\n[按 k 递增枚举: 每个传输零点数 k 对应的最小阶数 N]")
        print("   k    最小N   最差阻带裕量     零点(带通域/GHz)")
        for s in solutions:
            zs = zero_row(s)
            zs_str = ", ".join(f"{z/1e9:.4f}" for z in zs) if zs else "—"
            print(f"  {s['k']:>2}   {s['N']:>5}    {s['min_margin_dB']:>+9.2f} dB    [{zs_str}]")
        if stop_note:
            print(f"  [停止] {stop_note}")

        print(f"\n[最小阶解]")
        print(f"  阶数 N = {best['N']}")
        print(f"  有限传输零点数 k = {best['k']}")
        if best['k'] > 0:
            print(f"  零点位置(归一化低通 Ω): {np.round(best['zeros_lowpass'], 4).tolist()}")
            print(f"  零点位置(带通域 Hz):    {[round(z, 4) for z in zero_row(best)]}")
        print(f"\n[阻带细网格复核(401点/带)]")
        for (fw, aw, As) in verify_rows:
            ok = "✓" if aw >= As else "✗"
            print(f"  最坏点 f={fw:.6g}Hz | 要求 ≥ {As} dB | 实际 {aw:.2f} dB | {ok}")

    return {
        'success': True,
        'N': int(best['N']),
        'k': int(best['k']),
        'zeros_lowpass': best['zeros_lowpass'].tolist(),
        'zeros_bandpass_Hz': [float(z) for z in zero_row(best)],
        'f0': f0,
        'FBW': FBW,
        'epsilon_sq': float(epsilon_sq),
        'passband_ripple_dB': float(epsilon_sq_to_ripple_dB(epsilon_sq)),
        'N_lower_bound_no_zeros': float(N_lower),
        'stop_note': stop_note,
        # 401 点细网格复核结果（每条阻带规范的最坏点），供调用方自证达标
        'verify_rows': [
            {'f_Hz': float(fw), 'atten_dB': float(aw), 'required_dB': float(As)}
            for fw, aw, As in verify_rows
        ],
        'solutions': [
            {
                'N': int(s['N']),
                'k': int(s['k']),
                'zeros_lowpass': s['zeros_lowpass'].tolist(),
                'zeros_bandpass_Hz': [float(z) for z in zero_row(s)],
                'min_margin_dB': s['min_margin_dB'],
                'min_atten_dB': s['min_atten_dB'],
                'pass_max_dB': s['pass_max_dB'],
            }
            for s in solutions
        ],
    }


# ============================================================
# 示例 / 自测
# ============================================================

if __name__ == '__main__':
    np.set_printoptions(precision=6, suppress=True)

    # 示例1: 非对称带通(左侧抑制要求高, 右侧低)
    print("\n" + "█" * 60)
    print("█ 示例1: 2GHz 中心, 100MHz 通带, 非对称抑制")
    print("█" * 60)
    result1 = estimate_filter_order(
        f_p1=1.95e9, f_p2=2.05e9,          # 通带 1.95-2.05 GHz
        stopband_specs=[
            (1.85e9, 35.0),                  # 左侧 1.85GHz, 35dB 抑制
            (2.20e9, 25.0),                  # 右侧 2.20GHz, 25dB 抑制(要求低)
        ],
        RL_dB=-20.0,
        Q_unloaded=500.0,
        max_zeros=4,
        verbose=True,
    )

    # 示例2: 无Q值损耗, 纯理想综合
    print("\n\n" + "█" * 60)
    print("█ 示例2: 同上规范, 无Q损耗(对比)")
    print("█" * 60)
    result2 = estimate_filter_order(
        f_p1=1.95e9, f_p2=2.05e9,
        stopband_specs=[
            (1.85e9, 35.0),
            (2.20e9, 25.0),
        ],
        RL_dB=-20.0,
        Q_unloaded=None,
        max_zeros=4,
        verbose=True,
    )

    # 示例3: 紧规范强制 k>0, 验证新 SLSQP 反射零点求解器的等纹波精度
    print("\n\n" + "█" * 60)
    print("█ 示例3: 紧规范(左侧50dB紧贴通带), 强制 k>0")
    print("█" * 60)
    result3 = estimate_filter_order(
        f_p1=1.95e9, f_p2=2.05e9,
        stopband_specs=[
            (1.92e9, 50.0),                  # 左侧紧贴通带, 高抑制(标准切比雪夫需大N)
            (2.15e9, 30.0),                  # 右侧较远, 中等抑制
        ],
        RL_dB=-20.0,
        Q_unloaded=None,                    # 无Q损耗, 直接验证综合精度
        max_zeros=4,
        verbose=True,
    )

    # 直接验证: 对最终结果采样通带, 检查最大衰减 vs 理论纹波
    if result3.get('success') and result3['k'] > 0:
        print("\n[等纹波精度验证]")
        from scipy.signal import argrelextrema
        Om_dense = np.linspace(-0.999, 0.999, 2001)
        P, F, E = synthesize_polys(result3['N'],
                                   np.array(result3['zeros_lowpass']),
                                   result3['epsilon_sq'])
        attens_dense = evaluate_attenuation(Om_dense, P, F, E, result3['epsilon_sq'])
        ripple_theory = epsilon_sq_to_ripple_dB(result3['epsilon_sq'])
        pass_max = float(np.max(attens_dense))
        pass_min = float(np.min(attens_dense))
        # 等纹波滤波器: 通带内衰减应在 [0, ripple_theory] 之间等幅振荡
        print(f"  理论纹波 = {ripple_theory:.6f} dB")
        print(f"  通带采样最大衰减 = {pass_max:.6f} dB")
        print(f"  通带采样最小衰减 = {pass_min:.6f} dB")
        print(f"  实际纹波(峰-谷) = {pass_max - pass_min:.6f} dB")
        ratio = (pass_max - pass_min) / ripple_theory if ripple_theory > 0 else float('inf')
        print(f"  实际/理论 = {ratio:.3f}x (目标 ≤ 1.5x)")
        # 等纹波性: 找局部极大值, 检查是否都在 ripple_theory 附近
        idx_max = argrelextrema(attens_dense, np.greater)[0]
        if len(idx_max) > 0:
            peak_vals = attens_dense[idx_max]
            print(f"  局部极大值数 = {len(peak_vals)} (期望 N+1={result3['N']+1})")
            print(f"  极大值范围 = [{np.min(peak_vals):.4f}, {np.max(peak_vals):.4f}] dB")
            peak_spread = np.max(peak_vals) - np.min(peak_vals)
            print(f"  极大值离散度 = {peak_spread:.6f} dB (等纹波应≈0)")

    print("\n\n[结果JSON]")
    print(json.dumps(result1, indent=2, default=str))
