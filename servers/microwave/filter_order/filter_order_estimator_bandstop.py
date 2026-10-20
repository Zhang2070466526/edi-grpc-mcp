"""
广义切比雪夫带阻滤波器阶数与传输零点估计器
=====================================================
输入: 阻带边频率 + 阻带抑制要求(可分子带, 支持不对称) + 两侧通带边(最大衰减)
      + 回波损耗 + 谐振器无载Q值
输出: 低通原型阶数 N (带阻物理阶数 = 2N) + 有限传输零点对数 m
      及位置(原型域 Ω~ / 带阻域陷波频率 Hz)

设计原理 (方案A: 倒数变换法, 复用带通/低通估计器核心算法)
  1. 带阻频率变换(与带通同式):
       Ω = (f/f0 - f0/f) / FBW,  f0 = √(f_s1·f_s2),  FBW = (f_s2-f_s1)/f0
     阻带 [f_s1,f_s2] ↔ |Ω|≤1 ; 两侧通带 ↔ |Ω|>1
  2. 倒数变换 Ω' = 1/Ω:
     带阻通带 ↔ |Ω'|<1, 带阻阻带 ↔ |Ω'|>1
     → 在 Ω' 域退化为标准低通原型问题(通带在内, 阻带在外)
  3. 归一化 Ω~ = Ω'/Ω'_p,  Ω'_p = max(|1/Ω(f_p1)|, |1/Ω(f_p2)|)
     原型: 通带 |Ω~|≤1 (等纹波, 由 RL 定 ε), 阻带 |Ω~|≥Ω~_s=1/Ω'_p 抑制≥A_s
     max 归一化使两通带要求点都落入纹波带内(取紧侧为设计依据);
     含损耗时仍对两个通带点逐一校验
  4. 在 Ω~ 域调用与低通/带通估计器完全相同的框架:
     Remez 等纹波反射零点求解 + SLSQP 零点对优化 + 阶数枚举
  5. 原型零点对 ±Ω~_z ↔ 带阻阻带内两个陷波频率 f_z⁻, f_z⁺
     (分别位于 f0 两侧, 近似几何对称)

不对称支持:
  - 两侧通带要求 (f_p1,A_p1), (f_p2,A_p2) 可不同(max 归一化取紧侧)
  - 阻带可给多个子带/单点的不同抑制要求(如干扰频点深陷波)
  - 原型保持实系数对称零点对(物理可实现), 不对称规范作为逐点约束处理

理论基础(与带通/低通版共用):
  - Cameron 1999, IEEE T-MTT 47(4):433-442 (N+2 耦合矩阵综合)
  - Cameron 1982, ESA Journal 6(1):83-95
  - Milosavljević 2005 (不对称零点闭式解)
  - Matthaei 损耗模型: 原型域根左移 d = 1/(Q·FBW) (工程近似)

通带等纹波约束: |S11|²_max = ε²/(1+ε²)
能量守恒:       |F|² + (1/ε²)|P|² = |E|²
"""

import numpy as np
from scipy.optimize import minimize
from typing import List, Tuple, Optional, Dict, Any, Union


# ============================================================
# Step 1: 频率变换与规范映射 (带阻特有)
# ============================================================

def bandstop_normalize(f_s1: float, f_s2: float,
                       f_array: np.ndarray) -> Tuple[np.ndarray, float, float]:
    """带阻 → 归一化带阻域变换 (与带通同式)
    Ω = (f/f0 - f0/f) / FBW,  阻带边 f_s1→Ω=-1, f_s2→Ω=+1
    """
    f0 = float(np.sqrt(f_s1 * f_s2))
    FBW = (f_s2 - f_s1) / f0
    f_arr = np.asarray(f_array, dtype=float)
    Omega = (f_arr / f0 - f0 / f_arr) / FBW
    return Omega, f0, FBW


def omega_bs_to_freq(Omega_bs: float, f0: float, FBW: float) -> float:
    """归一化带阻域 Ω → 实际频率(正解, f>0)
    解 Ω·FBW = x - 1/x, x=f/f0 → x² - (Ω·FBW)x - 1 = 0, 取正根
    """
    a = Omega_bs * FBW
    x = (a + np.sqrt(a * a + 4.0)) / 2.0
    return float(f0 * x)


def proto_to_freq(Om_tilde: float, Om_p: float, f0: float, FBW: float) -> float:
    """原型域 Ω~(带符号) → 带阻域频率(Hz)
    Ω~ → Ω' = Ω'_p·Ω~ → Ω = 1/Ω' → f
    """
    Omega_bs = 1.0 / (Om_p * Om_tilde)
    return omega_bs_to_freq(Omega_bs, f0, FBW)


def freq_to_proto_abs(f: float, Om_p: float, f0: float, FBW: float) -> float:
    """带阻域频率 → 原型域 |Ω~| (Ω_BS=0 即 f=f0 时返回 inf)"""
    Om_bs = (f / f0 - f0 / f) / FBW
    if abs(Om_bs) < 1e-15:
        return np.inf
    return abs(1.0 / (Om_p * Om_bs))


def return_loss_to_epsilon_sq(RL_dB: float) -> float:
    """回波损耗(dB, 负值) → ε²
    |S11|²_max = 10^(RL/10), ε² = |S11|²_max / (1 - |S11|²_max)
    """
    s11_sq = 10.0 ** (RL_dB / 10.0)
    return s11_sq / (1.0 - s11_sq)


def epsilon_sq_to_ripple_dB(epsilon_sq: float) -> float:
    """ε² → 通带纹波(dB) = 10*log10(1+ε²)"""
    return 10.0 * np.log10(1.0 + epsilon_sq)


def map_stopband_specs(stopband_specs, f_s1: float, f_s2: float,
                       Om_p: float, f0: float, FBW: float,
                       n_per_band: int = 41
                       ) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
    """把带阻阻带规范映射为原型域(Ω~)采样点集

    条目格式:
      (f, A)          -> 抑制点 (f ∈ [f_s1, f_s2])
      (f_lo, f_hi, A) -> 抑制子带 [f_lo, f_hi] ⊆ [f_s1, f_s2], 均需 ≥ A
    映射规则:
      f → |Ω~| = 1/(Ω'_p·|Ω_BS(f)|)
      阻带边 ↔ |Ω~|=Ω~_s (最坏点), 阻带中心 f0 ↔ |Ω~|→∞
      采样在 |Ω~| 低端(靠阻带边一侧)加密
      跨越 f0 的子带 → |Ω~| ∈ [lo, ∞), 采样上限截断并附加远区点(查衰减平台)
    返回 (Om_points, A_points, spec_infos)
    """
    Om_pts, A_pts = [], []
    infos = []
    Om_s_tilde = 1.0 / Om_p

    for spec in stopband_specs:
        if len(spec) == 2:
            f_x, A_x = float(spec[0]), float(spec[1])
            if not (f_s1 - 1e-9 <= f_x <= f_s2 + 1e-9):
                raise ValueError(f"抑制点 f={f_x} 不在阻带 [{f_s1},{f_s2}] 内")
            Om_x = freq_to_proto_abs(f_x, Om_p, f0, FBW)
            if np.isinf(Om_x):
                # f0 处: 原型衰减→∞(或零点全有限时的衰减平台), 不作为约束点
                infos.append({'type': 'point', 'f': f_x, 'A': A_x,
                              'Om_lo': np.inf, 'Om_hi': np.inf, 'skipped': True})
                continue
            Om_pts.append(Om_x)
            A_pts.append(A_x)
            infos.append({'type': 'point', 'f': f_x, 'A': A_x,
                          'Om_lo': Om_x, 'Om_hi': Om_x, 'skipped': False})
            continue

        f_lo, f_hi, A_s = float(spec[0]), float(spec[1]), float(spec[2])
        if f_lo < f_s1 - 1e-9 or f_hi > f_s2 + 1e-9 or f_lo >= f_hi:
            raise ValueError(f"抑制子带 [{f_lo},{f_hi}] 必须包含于阻带 [{f_s1},{f_s2}]")
        Om_a = freq_to_proto_abs(f_lo, Om_p, f0, FBW)
        Om_b = freq_to_proto_abs(f_hi, Om_p, f0, FBW)
        spanning = (f_lo <= f0 <= f_hi)          # 跨越阻带中心 → 高端 ∞
        finite_vals = [v for v in (Om_a, Om_b) if np.isfinite(v)]
        lo = min(finite_vals) if finite_vals else Om_s_tilde
        lo = max(lo, Om_s_tilde)                  # 数值兜底
        hi = np.inf if spanning else max(Om_a, Om_b)

        # 采样: 低端(lo, 靠阻带边, 最坏点)加密; 高端截断
        hi_s = hi if np.isfinite(hi) else max(10.0 * lo, 20.0)
        hi_s = min(hi_s, 1.0e4)
        t = np.linspace(0.0, 1.0, n_per_band) ** 2   # t=0 处(lo端)加密
        Om_band = lo + (hi_s - lo) * t
        Om_pts.extend(Om_band.tolist())
        A_pts.extend([A_s] * n_per_band)
        if spanning:
            # 远区衰减平台检查点(零点全有限时 |S21|→常数, 平台可能不达标)
            Om_pts.append(1.0e4)
            A_pts.append(A_s)
        infos.append({'type': 'band', 'f_lo': f_lo, 'f_hi': f_hi, 'A': A_s,
                      'Om_lo': lo, 'Om_hi': hi, 'spanning': spanning, 'skipped': False})

    return np.array(Om_pts, dtype=float), np.array(A_pts, dtype=float), infos


# ============================================================
# Step 2: 阶数下界(无有限零点, 标准切比雪夫) —— 与低通版同式
# ============================================================

def chebyshev_order_lower_bound(Om_s_list: np.ndarray, A_s_list: np.ndarray,
                                epsilon_sq: float) -> float:
    """对每个原型阻带点单独算切比雪夫阶数, 取最大
    N_i ≥ acosh(√((10^(As/10)-1)/ε²)) / acosh(|Ω~_s|)
    """
    orders = []
    for Om, As in zip(Om_s_list, A_s_list):
        if abs(Om) <= 1.0 + 1e-9:
            continue
        ratio = np.sqrt((10.0 ** (As / 10.0) - 1.0) / epsilon_sq)
        if ratio <= 1.0:
            orders.append(0.0)
        else:
            orders.append(float(np.arccosh(ratio) / np.arccosh(abs(Om))))
    return max(orders) if orders else 0.0


# ============================================================
# Step 3: 广义切比雪夫多项式综合(P, F, E) —— 与低通/带通版完全相同
# ============================================================

def _poly_negate_s(poly: np.ndarray) -> np.ndarray:
    """返回 P*(-s) 系数(降幂): a_i → conj(a_i)·(-1)^(N-i)
    使 E·E(-s)=F·F(-s)+(1/ε²)P·P(-s) 在 s=jΩ 处给出 |E|²=|F|²+(1/ε²)|P|²
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

    等纹波条件(Cameron): 通带 [-1,1] 内 |S11(jΩ)|² 在 N+1 个极值点
    达到最大值 ε²/(1+ε²). 代数等价: F_tilde(Ω_k) = (-1)^k · P_tilde(Ω_k).

    Remez 迭代(每步解线性系统, 不走极点留数法):
      1. 初始化 N+1 个极值点(标准切比雪夫极值 cos(kπ/N))
      2. 解 Vandermonde 线性系统求 F_tilde 升幂系数
      3. 求 |S11|² 实际极值: F_tilde'·P_tilde - F_tilde·P_tilde' = 0
      4. 更新极值点, 迭代至收敛
      5. 反射零点 = F_tilde 的根(实数, |r|<1)
    """
    if N <= 0:
        return np.array([])
    if N == 1:
        return np.array([0.0])

    from numpy.polynomial.polynomial import polymul, polyder, polyval

    def _pad_add(p1, p2):
        p1 = np.asarray(p1, dtype=float)
        p2 = np.asarray(p2, dtype=float)
        n1, n2 = len(p1), len(p2)
        if n1 >= n2:
            return p1 + np.concatenate([p2, np.zeros(n1 - n2)])
        return np.concatenate([p1, np.zeros(n2 - n1)]) + p2

    k = len(finite_zeros)
    if k > 0:
        Ptilde_desc = np.poly(np.asarray(finite_zeros, dtype=float))
        Ptilde_asc = Ptilde_desc[::-1]
        P0 = Ptilde_asc[0]
        if abs(P0) > 1e-30:
            Ptilde_asc = Ptilde_asc / abs(P0)
    else:
        Ptilde_asc = np.array([1.0])

    Ptilde_prime_asc = polyder(Ptilde_asc)

    extrema = np.cos(np.arange(N + 1) * np.pi / N)

    c_asc = None
    for iteration in range(200):
        V = np.vander(extrema, N + 1, increasing=True)
        Ptilde_vals = polyval(extrema, Ptilde_asc)
        signs = (-1.0) ** np.arange(N + 1)
        b = signs * Ptilde_vals

        try:
            c_asc_new = np.linalg.solve(V, b)
        except np.linalg.LinAlgError:
            c_asc_new = np.linalg.lstsq(V, b, rcond=None)[0]

        Ftilde_prime_asc = polyder(c_asc_new)
        FP_asc = polymul(Ftilde_prime_asc, Ptilde_asc)
        FPp_asc = polymul(c_asc_new, Ptilde_prime_asc)
        diff_asc = _pad_add(FP_asc, -FPp_asc)

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

        internal = np.sort(internal)[::-1]

        if len(internal) > N - 1:
            F_abs = np.abs(polyval(internal, c_asc_new))
            idx = np.argsort(F_abs)[::-1][:N - 1]
            internal = np.sort(internal[idx])[::-1]
        elif len(internal) < N - 1:
            cheb_int = np.cos(np.arange(1, N) * np.pi / N)
            for ce in cheb_int:
                if len(internal) >= N - 1:
                    break
                if len(internal) == 0 or np.min(np.abs(internal - ce)) > 0.05:
                    internal = np.sort(np.append(internal, ce))[::-1]

        new_extrema = np.concatenate([[1.0], internal, [-1.0]])

        if c_asc is not None:
            ext_delta = np.max(np.abs(new_extrema - extrema))
            coef_delta = np.max(np.abs(c_asc_new - c_asc))
            if ext_delta < 1e-13 and coef_delta < 1e-13:
                c_asc = c_asc_new
                extrema = new_extrema
                break

        c_asc = c_asc_new
        extrema = new_extrema

    c_desc = c_asc[::-1]
    while len(c_desc) > 1 and abs(c_desc[0]) < 1e-14:
        c_desc = c_desc[1:]

    roots = np.roots(c_desc)
    real_mask = np.abs(np.imag(roots)) < 1e-6
    r = np.real(roots[real_mask])
    r = np.clip(r, -0.999, 0.999)

    if len(r) != N:
        j_arr = np.arange(1, N + 1)
        return np.sort(np.cos((2 * j_arr - 1) * np.pi / (2 * N)))

    return np.sort(r)


def synthesize_polys(N: int, finite_zeros: np.ndarray, epsilon_sq: float
                     ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """综合广义切比雪夫滤波器的 P, F, E 多项式(系数降幂)

    N: 原型阶数
    finite_zeros: 有限传输零点(原型域虚轴频率 Ω~, 实数, |z|>1)
      带阻版要求关于 0 对称(±Ω~_z 成对), 保证 P/F/E 实系数
    """
    k = len(finite_zeros)
    finite_zeros = np.asarray(finite_zeros, dtype=float)

    # ---------- P(s) ----------
    if k > 0:
        P_roots = 1j * finite_zeros
        P = np.poly(P_roots)
        P0 = np.polyval(P, 0.0)
        if abs(P0) > 1e-30:
            P = P / np.abs(P0)
    else:
        P = np.array([1.0])

    # ---------- F(s) ----------
    if N == 0:
        F = np.array([1.0])
    elif k == 0:
        j_arr = np.arange(1, N + 1)
        refl = np.cos((2 * j_arr - 1) * np.pi / (2 * N))
        F = np.poly(1j * refl)
    else:
        refl = _solve_reflection_zeros(N, finite_zeros, epsilon_sq, P)
        F = np.poly(1j * refl)

    # Scale 归一化: |F(j·1)|² = |P(j·1)|² (通带边 |S11|²=ε²/(1+ε²))
    F_at_j1 = np.polyval(F, 1j)
    P_at_j1 = np.polyval(P, 1j)
    if np.abs(F_at_j1) > 1e-30:
        F = F * (np.abs(P_at_j1) / np.abs(F_at_j1))

    # ---------- E(s) ----------
    # E(s)E(-s) = F(s)F(-s) + (1/ε²) P(s)P(-s)
    FF_neg = np.convolve(F, _poly_negate_s(F))
    PP_neg = np.convolve(P, _poly_negate_s(P))
    EE_neg = _poly_add(FF_neg, (1.0 / epsilon_sq) * PP_neg)

    roots_EE = np.roots(EE_neg)
    tol = 1e-9

    re = np.real(roots_EE)
    left = roots_EE[re < -tol]
    boundary = roots_EE[np.abs(re) <= tol]
    boundary_left = boundary[np.imag(boundary) > 0]
    all_left = np.concatenate([left, boundary_left]) if len(boundary_left) > 0 else left

    if len(all_left) != N:
        idx = np.argsort(np.imag(roots_EE))
        all_left = roots_EE[idx[len(idx) - N:]]

    E = np.poly(all_left)
    # 归一化: E(0)² = F(0)² + (1/ε²)P(0)² (能量守恒)
    E0 = np.polyval(E, 0.0 + 0j)
    F0 = np.polyval(F, 0.0 + 0j)
    P0 = np.polyval(P, 0.0 + 0j)
    target_E0_sq = abs(F0) ** 2 + (1.0 / epsilon_sq) * abs(P0) ** 2
    if abs(E0) > 1e-30 and target_E0_sq > 1e-30:
        target_E0 = np.sqrt(target_E0_sq)
        E = E * (target_E0 / abs(E0))

    return P, F, E


# ============================================================
# Step 4: 衰减评估(无损 + 含Q值损耗) —— 与低通/带通版相同
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


def evaluate_attenuation_with_d(Omega_array: np.ndarray, P: np.ndarray, F: np.ndarray,
                                E: np.ndarray, epsilon_sq: float,
                                d_loss: float) -> np.ndarray:
    """加入谐振器有限Q值后的衰减(原型域)

    损耗模型 (Matthaei): 原型 s 平面根整体左移 d_loss = 1/(Q·FBW)
    (FBW 为带阻阻带相对带宽; 工程近似, 与带通版一致)
    """
    def shift_poly(poly, d):
        if len(poly) <= 1:
            return poly.copy()
        roots = np.roots(poly)
        roots_shifted = roots - d
        lead = poly[0]
        return np.poly(roots_shifted) * lead

    P_lossy = shift_poly(P, d_loss)
    F_lossy = shift_poly(F, d_loss)
    E_lossy = shift_poly(E, d_loss)

    # 损耗下能量守恒近似不再严格, 用 s=0 处重新归一化 E
    E0 = np.polyval(E_lossy, 0.0 + 0j)
    F0 = np.polyval(F_lossy, 0.0 + 0j)
    P0 = np.polyval(P_lossy, 0.0 + 0j)
    target_E0_sq = abs(F0) ** 2 + (1.0 / epsilon_sq) * abs(P0) ** 2
    if abs(E0) > 1e-30 and target_E0_sq > 1e-30:
        target_E0 = np.sqrt(target_E0_sq)
        E_lossy = E_lossy * (target_E0 / abs(E0))

    return evaluate_attenuation(Omega_array, P_lossy, F_lossy, E_lossy, epsilon_sq)


def build_symmetric_zeros(notches: np.ndarray) -> np.ndarray:
    """正的零点幅度 → 对称零点对 [n1,-n1,n2,-n2,...] (对应 s=±jΩ~_z)"""
    notches = np.asarray(notches, dtype=float)
    z = np.empty(2 * len(notches))
    z[0::2] = notches
    z[1::2] = -notches
    return z


# ============================================================
# Step 5: 有限零点位置优化(零点对幅度为自变量, 内部自动对称)
# ============================================================

def optimize_notches(N: int, Om_s_pts: np.ndarray, A_s_pts: np.ndarray,
                     notches_init: np.ndarray, epsilon_sq: float,
                     d_loss: float, Om_s_tilde: float,
                     Om_pass_pts: np.ndarray, A_pass_pts: np.ndarray,
                     pass_limit_dB: float) -> Tuple[np.ndarray, float]:
    """SLSQP 优化原型零点对位置(自变量为正的归一化幅度 Ω~_z ≥ Ω~_s)

    目标: 阻带点零违约 + 通带要求点达标 + 通带纹波不超标
    约束: Ω~_z ≥ Ω~_s (零点必须位于带阻阻带内, 即原型阻带)
    """
    notches_init = np.asarray(notches_init, dtype=float)
    A_s_arr = np.asarray(A_s_pts, dtype=float)
    A_p_arr = np.asarray(A_pass_pts, dtype=float)
    Om_grid = np.linspace(0.0, 0.999, 41)   # 原型通带网格

    def atten(Om_arr, P, F, E):
        if d_loss > 0:
            return evaluate_attenuation_with_d(Om_arr, P, F, E, epsilon_sq, d_loss)
        return evaluate_attenuation(Om_arr, P, F, E, epsilon_sq)

    def objective(nz):
        try:
            zeros = build_symmetric_zeros(nz)
            P, F, E = synthesize_polys(N, zeros, epsilon_sq)
            attens = atten(Om_s_pts, P, F, E)
            pass_pt_attens = atten(Om_pass_pts, P, F, E)
            pass_attens = atten(Om_grid, P, F, E)

            violations = np.maximum(0.0, A_s_arr - attens)
            viol_pp = np.maximum(0.0, pass_pt_attens - A_p_arr)
            pass_excess = np.maximum(0.0, pass_attens - pass_limit_dB)
            return float(100.0 * np.max(violations) + np.sum(violations ** 2)
                         + 100.0 * (np.max(viol_pp) if len(viol_pp) else 0.0)
                         + 10.0 * np.sum(viol_pp ** 2)
                         + 5.0 * np.sum(pass_excess))
        except Exception:
            return 1e6

    m = len(notches_init)
    z_lo = Om_s_tilde * 1.005          # 零点严格在原型阻带内
    z_hi = max(30.0, 10.0 * Om_s_tilde)
    bounds = [(z_lo, z_hi)] * m
    # 零点对间距约束(防止重合)
    cons = []
    gap = max(0.05, 0.08 * Om_s_tilde)
    for i in range(m):
        for j in range(i + 1, m):
            cons.append({'type': 'ineq',
                         'fun': lambda nz, i=i, j=j: abs(nz[i] - nz[j]) - gap})

    try:
        import warnings
        with warnings.catch_warnings():
            # SLSQP 内部试步可能越界后被截断, 属正常数值行为, 屏蔽其告警
            warnings.simplefilter("ignore", RuntimeWarning)
            result = minimize(objective, notches_init, method='SLSQP',
                              bounds=bounds, constraints=cons,
                              options={'maxiter': 500, 'ftol': 1e-9})
        return result.x, objective(result.x)
    except Exception:
        return notches_init, objective(notches_init)


# ============================================================
# Step 6: 主函数
# ============================================================

def estimate_bandstop_filter_order(f_s1: float, f_s2: float,
                                   stopband_specs: Union[float, List],
                                   passband_specs: Union[Tuple, List],
                                   RL_dB: float = -20.0,
                                   Q_unloaded: Optional[float] = None,
                                   max_notches: int = 4) -> Dict[str, Any]:
    """估计广义切比雪夫带阻滤波器的阶数和传输零点

    参数:
        f_s1, f_s2: 阻带边频率(Hz), 阻带为 [f_s1, f_s2]
        stopband_specs: 阻带抑制要求, 两种形式
            浮点数 A_s                  -> 整个阻带抑制 ≥ A_s (dB)
            列表 [(f, A)] / [(f_lo, f_hi, A)] -> 抑制点 / 抑制子带(支持不对称)
        passband_specs: 两侧通带要求, 两种形式
            (f_p1, A_p1, f_p2, A_p2) 或 [(f_p1, A_p1), (f_p2, A_p2)]
            f_p1 < f_s1 为下通带边, f_p2 > f_s2 为上通带边,
            要求 f≤f_p1 时衰减 ≤ A_p1 (dB), f≥f_p2 时衰减 ≤ A_p2 (dB)
        RL_dB: 通带回波损耗(dB, 负值), 决定等纹波 ε
        Q_unloaded: 谐振器无载Q值(None=无损)
        max_notches: 最大允许的有限传输零点对数(每对 = s=±jΩ~_z, 计 deg(P)=2)

    返回: 原型阶数 N (带阻物理阶数 2N), 零点对数 m, 陷波频率(Hz) 等
    """
    # ---- 输入解析与校验 ----
    if not (0 < f_s1 < f_s2):
        raise ValueError("要求 0 < f_s1 < f_s2")

    if isinstance(stopband_specs, (int, float)):
        stopband_specs = [(f_s1, f_s2, float(stopband_specs))]
    else:
        stopband_specs = [tuple(s) for s in stopband_specs]

    ps = list(passband_specs)
    if len(ps) == 4 and np.isscalar(ps[0]):
        f_p1, A_p1, f_p2, A_p2 = float(ps[0]), float(ps[1]), float(ps[2]), float(ps[3])
    else:
        (f_p1, A_p1), (f_p2, A_p2) = ps
        f_p1, A_p1, f_p2, A_p2 = float(f_p1), float(A_p1), float(f_p2), float(A_p2)
    if not (f_p1 < f_s1 and f_p2 > f_s2):
        raise ValueError("要求 f_p1 < f_s1 < f_s2 < f_p2")

    epsilon_sq = return_loss_to_epsilon_sq(RL_dB)
    ripple_dB = epsilon_sq_to_ripple_dB(epsilon_sq)
    if A_p1 < ripple_dB or A_p2 < ripple_dB:
        raise ValueError(f"通带边最大衰减 A_p 必须 ≥ 纹波 {ripple_dB:.4f} dB, "
                         f"否则规范不可行 (A_p1={A_p1}, A_p2={A_p2})")

    # ---- 频率变换 ----
    _, f0, FBW = bandstop_normalize(f_s1, f_s2, np.array([f_s1]))
    Om_bs_p1 = (f_p1 / f0 - f0 / f_p1) / FBW   # < -1
    Om_bs_p2 = (f_p2 / f0 - f0 / f_p2) / FBW   # > +1
    Om_p = max(abs(1.0 / Om_bs_p1), abs(1.0 / Om_bs_p2))  # Ω'_p < 1
    Om_s_tilde = 1.0 / Om_p                                # 原型阻带边 > 1
    # 通带要求点的原型 |Ω~| (≤1, 即落在纹波带内)
    Om_pp1 = abs(1.0 / Om_bs_p1) / Om_p
    Om_pp2 = abs(1.0 / Om_bs_p2) / Om_p
    Om_pass_pts = np.array([Om_pp1, Om_pp2])
    A_pass_pts = np.array([A_p1, A_p2])

    d_loss = 1.0 / (Q_unloaded * FBW) if Q_unloaded else 0.0

    # ---- 阻带规范映射到原型域 ----
    Om_s_pts, A_s_pts, spec_infos = map_stopband_specs(
        stopband_specs, f_s1, f_s2, Om_p, f0, FBW)

    # ---- 阶数下界(取各规范最坏点, 即 |Ω~| 最小端) ----
    lb_pts, lb_As = [], []
    for info in spec_infos:
        if info.get('skipped'):
            continue
        lb_pts.append(info['Om_lo'])
        lb_As.append(info['A'])
    N_lower = chebyshev_order_lower_bound(np.array(lb_pts), np.array(lb_As), epsilon_sq)

    # ---- 搜索可行解 ----
    N_max_search = int(np.ceil(N_lower)) + 2 * max_notches + 1
    pass_limit_dB = max(ripple_dB * 20.0, A_p1, A_p2, 1.0)
    Om_grid = np.linspace(0.0, 0.999, 41)

    def atten_eval(Om_arr, P, F, E):
        if d_loss > 0:
            return evaluate_attenuation_with_d(Om_arr, P, F, E, epsilon_sq, d_loss)
        return evaluate_attenuation(Om_arr, P, F, E, epsilon_sq)

    def check_feasibility(N, zeros):
        """检查(N, zeros)可行性: 阻带点裕量≥0, 通带要求点达标, 通带纹波不超标"""
        try:
            P, F, E = synthesize_polys(N, zeros, epsilon_sq)
            attens = atten_eval(Om_s_pts, P, F, E)
            pp_attens = atten_eval(Om_pass_pts, P, F, E)
            pass_attens = atten_eval(Om_grid, P, F, E)

            if np.min(attens - A_s_pts) < -1e-6:
                return False, attens, pass_attens
            if np.any(pp_attens > A_pass_pts + 1e-6):
                return False, attens, pass_attens
            if np.max(pass_attens) > pass_limit_dB:
                return False, attens, pass_attens
            return True, attens, pass_attens
        except Exception:
            return False, None, None

    # 各阻带规范的最坏点(|Ω~| 最小端)作为零点锚点, 按紧度排序
    anchors = np.array(sorted(lb_pts))

    # 枚举: 外层零点对数 m=0,1,2,...; 每个 m 求最小 N;
    #       工程约束 m ≤ N/2 (deg(P)=2m ≤ N), 不满足即停止
    solutions = []
    stop_note = None
    for m in range(max_notches + 1):
        k = 2 * m
        N_min = None
        sol = None
        for N in range(max(1, k), N_max_search + 1):
            # 零点初始位置: 锚点循环铺放, 超出规范条数时向外分层外推
            notches_init = []
            for i in range(m):
                a = anchors[i % len(anchors)]
                tier = i // len(anchors)
                notches_init.append(max(float(a), Om_s_tilde * 1.005) * (1.0 + 0.25 * tier))
            notches_init = np.array(notches_init) if notches_init else np.array([])

            if m == 0:
                zeros_opt = np.array([])
            else:
                notches_opt, _ = optimize_notches(
                    N, Om_s_pts, A_s_pts, notches_init, epsilon_sq,
                    d_loss, Om_s_tilde, Om_pass_pts, A_pass_pts, pass_limit_dB)
                zeros_opt = build_symmetric_zeros(notches_opt)

            feasible, attens, pass_attens = check_feasibility(N, zeros_opt)
            if not feasible:
                continue

            N_min = N
            notches_pos = np.sort(np.abs(zeros_opt[0::2])) if m > 0 else np.array([])
            sol = {
                'N': N, 'k': k, 'notches': m,
                'notches_proto': notches_pos,
                'zeros_proto': np.array(zeros_opt),
                'min_margin_dB': float(np.min(attens - A_s_pts)),
                'min_atten_dB': float(np.min(attens)),
                'pass_max_dB': float(np.max(pass_attens)),
            }
            break

        if N_min is None:
            stop_note = f"m={m}: 在 N ≤ {N_max_search} 内无可行解"
            break
        if m > N_min // 2:
            stop_note = (f"m={m}: 最小阶数 N={N_min}, 但 m > N/2 ({m} > {N_min // 2}), "
                         f"违反工程约束 → 停止枚举")
            break
        solutions.append(sol)

    if not solutions:
        return {'success': False, 'reason': 'no feasible solution'}

    best = min(solutions, key=lambda s: s['N'])

    def notch_freq_row(sol):
        """原型零点对幅度 → 带阻域陷波频率对 [(f_下, f_上), ...] (Hz)"""
        rows = []
        for z in sol['notches_proto']:
            Om_bs = 1.0 / (Om_p * z)   # |Ω_BS| ≤ 1, 位于阻带内
            rows.append((omega_bs_to_freq(-Om_bs, f0, FBW),
                         omega_bs_to_freq(+Om_bs, f0, FBW)))
        return rows

    # ---- 最终验证: 细网格复核(阻带各子带 + 两侧通带) ----
    P, F, E = synthesize_polys(best['N'], best['zeros_proto'], epsilon_sq)

    def atten_at_freq(freqs):
        freqs = np.asarray(freqs, dtype=float)
        Om_bs = (freqs / f0 - f0 / freqs) / FBW
        with np.errstate(divide='ignore'):
            Om_t = 1.0 / (Om_p * Om_bs)
        Om_t = np.clip(Om_t, -1e12, 1e12)
        return atten_eval(Om_t, P, F, E)

    verify_stop = []
    for info in spec_infos:
        if info.get('skipped'):
            continue
        if info['type'] == 'point':
            freqs = np.array([info['f']])
        else:
            freqs = np.linspace(info['f_lo'], info['f_hi'], 401)
        att_v = atten_at_freq(freqs)
        i_w = int(np.argmin(att_v))
        verify_stop.append((float(freqs[i_w]), float(att_v[i_w]), info['A']))

    # 通带复核: 下通带 [f_p1/5, f_p1], 上通带 [f_p2, 5·f_p2] (几何等分)
    f_lo_grid = np.geomspace(f_p1 / 5.0, f_p1, 201)
    f_hi_grid = np.geomspace(f_p2, 5.0 * f_p2, 201)
    att_lo = atten_at_freq(f_lo_grid)
    att_hi = atten_at_freq(f_hi_grid)
    pass_check = [
        (float(f_p1), float(atten_at_freq([f_p1])[0]), A_p1,
         float(np.max(att_lo)), float(f_lo_grid[int(np.argmax(att_lo))])),
        (float(f_p2), float(atten_at_freq([f_p2])[0]), A_p2,
         float(np.max(att_hi)), float(f_hi_grid[int(np.argmax(att_hi))])),
    ]

    return {
        'success': True,
        'N': int(best['N']),
        'order_bandstop': int(2 * best['N']),
        'k': int(best['k']),
        'notches': int(best['notches']),
        'notches_proto': best['notches_proto'].tolist(),
        'zeros_proto': best['zeros_proto'].tolist(),
        'notch_freq_pairs_Hz': [[float(lo), float(hi)] for lo, hi in notch_freq_row(best)],
        'f0': f0,
        'FBW': FBW,
        'Om_p_prime': float(Om_p),
        'Om_s_tilde': float(Om_s_tilde),
        'epsilon_sq': float(epsilon_sq),
        'passband_ripple_dB': float(ripple_dB),
        'N_lower_bound_no_zeros': float(N_lower),
        'solutions': [
            {
                'N': int(s['N']),
                'order_bandstop': int(2 * s['N']),
                'k': int(s['k']),
                'notches': int(s['notches']),
                'notches_proto': s['notches_proto'].tolist(),
                'notch_freq_pairs_Hz': [[float(lo), float(hi)] for lo, hi in notch_freq_row(s)],
                'min_margin_dB': s['min_margin_dB'],
                'min_atten_dB': s['min_atten_dB'],
                'pass_max_dB': s['pass_max_dB'],
            }
            for s in solutions
        ],
    }


# ============================================================
# 主函数调用: 通带 0.1~2GHz / 2.3~3GHz, 阻带 2.1~2.2GHz 陷波 >= 20dB, Q=500
# ============================================================

if __name__ == '__main__':
    np.set_printoptions(precision=6, suppress=True)

    result = estimate_bandstop_filter_order(
        f_s1=2.1e9, f_s2=2.2e9,                     # 阻带 2.1~2.2 GHz
        stopband_specs=20.0,                        # 陷波深度 >= 20 dB
        passband_specs=[(2.0e9, 1.0), (2.3e9, 1.0)], # 通带边 2G / 2.3G, 衰减 <= 1dB
        RL_dB=-20.0,
        Q_unloaded=500,
        max_notches=4,
    )

    print(f"\n[最小阶解]")
    print(f"  原型阶数 N = {result['N']}")
    print(f"  有限传输零点对数 m = {result['notches']} (deg(P)=2m={result['k']})")
    if result['notches'] > 0:
        print(f"  零点幅度(原型 Ω~_z): {np.round(result['notches_proto'], 4).tolist()}")
        for i, (lo, hi) in enumerate(result['notch_freq_pairs_Hz']):
            print(f"  陷波对{i+1}: f_下 = {lo:.6g} Hz,  f_上 = {hi:.6g} Hz")
    else:
        print("  (无有限传输零点, 全部位于 f0, 标准全极点带阻)")