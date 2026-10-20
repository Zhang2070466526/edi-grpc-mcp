"""
广义切比雪夫低通滤波器阶数与传输零点估计器
=====================================================
输入: 通带截止频率 + 阻带抑制点(dB) + 回波损耗 + 谐振器无载Q值
输出: 阶数 N + 有限传输零点(对称零点对)位置

与带通版 (filter_order_estimator.py) 的差异:
  * 频率归一化:  Ω = f / f_p   (通带边 |Ω|=1)
     取代带通的        Ω = (f/f0 - f0/f) / FBW
  * 阻带仅存在于上阻带 (f > f_p), 规范只描述 Ω > 1 一侧
  * 低通响应关于 Ω 对称 → 有限传输零点必须成对出现在 s = ±jΩ_z,
    实系数 P/F/E 才能成立。故"1 个零点对(notch)"对应 deg(P)=2 个零点,
    可实现性要求 deg(P)=2m ≤ N (由 N_start = max(1, 2m) 在构造层强制)。
  * 默认仅枚举奇数阶 (odd_only=True): 偶数阶切比雪夫/椭圆低通在 DC 处
    有非零插损, 工程上一般只取奇数阶。
  * 枚举截断判据: 某 m 的最小阶数超过已找到的最优阶数 → 增加零点
    不再降阶, 停止枚举 (替代带通版的 k ≤ N/2 判断; 该判断在低通因
    N_min ≥ 2m+1 恒成立而永不触发)。
  * 有限Q损耗系数 d = 1/Q  (低通等价于 FBW=1 的带通)

理论基础(与带通版共用):
  - Cameron 1999, IEEE T-MTT 47(4):433-442 (N+2 耦合矩阵综合)
  - Milosavljević 2005 (不对称零点闭式解)

通带等纹波约束: |S11|²_max = ε²/(1+ε²)
能量守恒:       |F|² + (1/ε²)|P|² = |E|²
"""
import numpy as np
from scipy.optimize import minimize
from typing import List, Tuple, Optional, Dict, Any


def lowpass_normalize(f_p, f_array):
    return np.asarray(f_array, dtype=float) / f_p


def omega_to_freq(Omega, f_p):
    return np.asarray(Omega, dtype=float) * f_p


def return_loss_to_epsilon_sq(RL_dB):
    s11_sq = 10.0 ** (RL_dB / 10.0)
    return s11_sq / (1.0 - s11_sq)


def epsilon_sq_to_ripple_dB(epsilon_sq):
    return 10.0 * np.log10(1.0 + epsilon_sq)


def expand_stopband_specs(stopband_specs, f_p, n_per_band=41):
    f_pts, A_pts = [], []
    for spec in stopband_specs:
        if len(spec) == 2:
            f_pts.append(float(spec[0])); A_pts.append(float(spec[1])); continue
        f_lo, f_hi, A_s = float(spec[0]), float(spec[1]), float(spec[2])
        t = np.linspace(0.0, 1.0, n_per_band)
        s = t ** 2 if f_lo >= f_p else t
        f_band = f_lo + (f_hi - f_lo) * s
        f_pts.extend(f_band.tolist()); A_pts.extend([A_s] * n_per_band)
    return np.array(f_pts, dtype=float), np.array(A_pts, dtype=float)


def chebyshev_order_lower_bound(Omega_s_list, A_s_list, epsilon_sq):
    orders = []
    for Om, As in zip(Omega_s_list, A_s_list):
        if abs(Om) <= 1.0 + 1e-9:
            continue
        ratio = np.sqrt((10.0 ** (As / 10.0) - 1.0) / epsilon_sq)
        orders.append(0.0 if ratio <= 1.0 else float(np.arccosh(ratio) / np.arccosh(abs(Om))))
    return max(orders) if orders else 0.0


def _poly_negate_s(poly):
    N = len(poly) - 1
    signs = (-1.0) ** (N - np.arange(len(poly)))
    return np.conj(poly) * signs


def _poly_add(p1, p2):
    n1, n2 = len(p1), len(p2)
    if n1 == n2:
        return p1 + p2
    if n1 > n2:
        return p1 + np.concatenate([np.zeros(n1 - n2), p2])
    return np.concatenate([np.zeros(n2 - n1), p1]) + p2


def _solve_reflection_zeros(N, finite_zeros, epsilon_sq, P):
    if N <= 0:
        return np.array([])
    if N == 1:
        return np.array([0.0])
    from numpy.polynomial.polynomial import polymul, polyder, polyval

    def _pad_add(p1, p2):
        p1 = np.asarray(p1, dtype=float); p2 = np.asarray(p2, dtype=float)
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
            internal = real_roots[(real_roots > -1 + 1e-8) & (real_roots < 1 - 1e-8)]
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
            if np.max(np.abs(new_extrema - extrema)) < 1e-13 and np.max(np.abs(c_asc_new - c_asc)) < 1e-13:
                c_asc = c_asc_new; extrema = new_extrema; break
        c_asc = c_asc_new; extrema = new_extrema
    c_desc = c_asc[::-1]
    while len(c_desc) > 1 and abs(c_desc[0]) < 1e-14:
        c_desc = c_desc[1:]
    roots = np.roots(c_desc)
    r = np.real(roots[np.abs(np.imag(roots)) < 1e-6])
    r = np.clip(r, -0.999, 0.999)
    if len(r) != N:
        j_arr = np.arange(1, N + 1)
        return np.sort(np.cos((2 * j_arr - 1) * np.pi / (2 * N)))
    return np.sort(r)


def synthesize_polys(N, finite_zeros, epsilon_sq):
    k = len(finite_zeros)
    finite_zeros = np.asarray(finite_zeros, dtype=float)
    if k > 0:
        P = np.poly(1j * finite_zeros)
        P0 = np.polyval(P, 0.0)
        if abs(P0) > 1e-30:
            P = P / np.abs(P0)
    else:
        P = np.array([1.0])
    if N == 0:
        F = np.array([1.0])
    elif k == 0:
        j_arr = np.arange(1, N + 1)
        refl = np.cos((2 * j_arr - 1) * np.pi / (2 * N))
        F = np.poly(1j * refl)
    else:
        refl = _solve_reflection_zeros(N, finite_zeros, epsilon_sq, P)
        F = np.poly(1j * refl)
    F_at_j1 = np.polyval(F, 1j); P_at_j1 = np.polyval(P, 1j)
    if np.abs(F_at_j1) > 1e-30:
        F = F * (np.abs(P_at_j1) / np.abs(F_at_j1))
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
    E0 = np.polyval(E, 0.0 + 0j); F0 = np.polyval(F, 0.0 + 0j); P0 = np.polyval(P, 0.0 + 0j)
    target_E0_sq = abs(F0) ** 2 + (1.0 / epsilon_sq) * abs(P0) ** 2
    if abs(E0) > 1e-30 and target_E0_sq > 1e-30:
        E = E * (np.sqrt(target_E0_sq) / abs(E0))
    return P, F, E


def evaluate_attenuation(Omega_array, P, F, E, epsilon_sq):
    eps = np.sqrt(epsilon_sq)
    s = 1j * np.asarray(Omega_array, dtype=float)
    S21_sq = np.abs(np.polyval(P, s) / (eps * np.polyval(E, s))) ** 2
    return -10.0 * np.log10(np.clip(S21_sq, 1e-30, 1.0))


def evaluate_attenuation_with_Q(Omega_array, P, F, E, epsilon_sq, Q_unloaded):
    d_loss = 1.0 / Q_unloaded

    def shift_poly(poly, d):
        if len(poly) <= 1:
            return poly.copy()
        return np.poly(np.roots(poly) - d) * poly[0]
    P2 = shift_poly(P, d_loss); F2 = shift_poly(F, d_loss); E2 = shift_poly(E, d_loss)
    E0 = np.polyval(E2, 0.0 + 0j); F0 = np.polyval(F2, 0.0 + 0j); P0 = np.polyval(P2, 0.0 + 0j)
    t = abs(F0) ** 2 + (1.0 / epsilon_sq) * abs(P0) ** 2
    if abs(E0) > 1e-30 and t > 1e-30:
        E2 = E2 * (np.sqrt(t) / abs(E0))
    return evaluate_attenuation(Omega_array, P2, F2, E2, epsilon_sq)


def build_symmetric_zeros(notches):
    notches = np.asarray(notches, dtype=float)
    z = np.empty(2 * len(notches))
    z[0::2] = notches; z[1::2] = -notches
    return z


def optimize_notches(N, Omega_s_list, A_s_list, notches_init, epsilon_sq, Q_unloaded):
    notches_init = np.asarray(notches_init, dtype=float)
    A_s_arr = np.asarray(A_s_list, dtype=float)
    ripple_dB = 10 * np.log10(1 + epsilon_sq)
    Om_pass = np.linspace(0.0, 0.99, 41)

    def objective(nz):
        try:
            P, F, E = synthesize_polys(N, build_symmetric_zeros(nz), epsilon_sq)
            if Q_unloaded > 0:
                attens = evaluate_attenuation_with_Q(Omega_s_list, P, F, E, epsilon_sq, Q_unloaded)
                pass_attens = evaluate_attenuation_with_Q(Om_pass, P, F, E, epsilon_sq, Q_unloaded)
            else:
                attens = evaluate_attenuation(Omega_s_list, P, F, E, epsilon_sq)
                pass_attens = evaluate_attenuation(Om_pass, P, F, E, epsilon_sq)
            violations = np.maximum(0.0, A_s_arr - attens)
            pass_excess = np.maximum(0.0, pass_attens - max(ripple_dB * 20.0, 1.0))
            return float(100.0 * np.max(violations) + np.sum(violations ** 2) + 5.0 * np.sum(pass_excess))
        except Exception:
            return 1e6
    m = len(notches_init)
    cons = [{'type': 'ineq', 'fun': lambda nz, i=i: nz[i] - 1.05} for i in range(m)]
    for i in range(m):
        for j in range(i + 1, m):
            cons.append({'type': 'ineq', 'fun': lambda nz, i=i, j=j: abs(nz[i] - nz[j]) - 0.3})
    bounds = [(1.05, 30.0)] * m
    try:
        res = minimize(objective, notches_init, method='SLSQP', bounds=bounds,
                       constraints=cons, options={'maxiter': 500, 'ftol': 1e-9})
        return res.x, objective(res.x)
    except Exception:
        return notches_init, objective(notches_init)


def _ceil_odd(x):
    """向上取到最近的奇数"""
    c = int(np.ceil(x))
    return c if c % 2 == 1 else c + 1


def estimate_lowpass_filter_order(f_p, stopband_specs, RL_dB=-20.0,
                                  Q_unloaded=None, max_notches=4, verbose=True,
                                  odd_only=True):
    f_s_arr, A_s_arr = expand_stopband_specs(stopband_specs, f_p)
    Omega_s = lowpass_normalize(f_p, f_s_arr)
    epsilon_sq = return_loss_to_epsilon_sq(RL_dB)
    N_lower = chebyshev_order_lower_bound(Omega_s, A_s_arr, epsilon_sq)
    N0 = _ceil_odd(N_lower) if odd_only else int(np.ceil(N_lower))

    if verbose:
        print("=" * 60)
        print("广义切比雪夫低通滤波器 - 阶数与零点估计")
        print("=" * 60)
        print(f"  通带截止 f_p = {f_p/1e9:.4g} GHz | RL = {RL_dB} dB (纹波 {epsilon_sq_to_ripple_dB(epsilon_sq):.4f} dB)")
        print(f"  Q_unloaded = {Q_unloaded if Q_unloaded else '∞(无损)'}")
        print(f"  阶数下界(无零点) N_lower = {N_lower:.3f} → 起搜阶数 {N0}"
              + (" (仅奇数阶)" if odd_only else ""))

    N_max_search = N0 + 2 * max_notches + 2
    ripple_dB = epsilon_sq_to_ripple_dB(epsilon_sq)
    max_passband_dB = max(ripple_dB * 20.0, 1.0)

    def check_feasibility(N, zeros):
        try:
            P, F, E = synthesize_polys(N, zeros, epsilon_sq)
            Om_pass = np.linspace(0.0, 0.99, 41)
            if Q_unloaded:
                attens = evaluate_attenuation_with_Q(Omega_s, P, F, E, epsilon_sq, Q_unloaded)
                pass_attens = evaluate_attenuation_with_Q(Om_pass, P, F, E, epsilon_sq, Q_unloaded)
            else:
                attens = evaluate_attenuation(Omega_s, P, F, E, epsilon_sq)
                pass_attens = evaluate_attenuation(Om_pass, P, F, E, epsilon_sq)
            if np.min(attens - A_s_arr) < 0:
                return False, attens, pass_attens
            if np.max(pass_attens) > max_passband_dB:
                return False, attens, pass_attens
            return True, attens, pass_attens
        except Exception:
            return False, None, None

    anchors = np.array([float(lowpass_normalize(f_p, np.array([float(spec[0])]))[0]) for spec in stopband_specs])
    anchors = anchors[np.argsort(anchors)]

    solutions = []
    stop_note = None
    for m in range(max_notches + 1):
        k = 2 * m
        N_min = None; sol = None
        N_start = max(1, k)
        if odd_only and N_start % 2 == 0:
            N_start += 1
        for N in range(N_start, N_max_search + 1, 2 if odd_only else 1):
            notches_init = []
            for i in range(m):
                a = anchors[i % len(anchors)]; tier = i // len(anchors)
                notches_init.append(max(float(a), 1.05) * (1.0 + 0.6 * tier))
            notches_init = np.array(notches_init) if notches_init else np.array([])
            if m == 0:
                zeros_opt = np.array([])
            else:
                notches_opt, _ = optimize_notches(N, Omega_s, A_s_arr, notches_init,
                                                  epsilon_sq, Q_unloaded if Q_unloaded else 0.0)
                zeros_opt = build_symmetric_zeros(notches_opt)
            feasible, attens, pass_attens = check_feasibility(N, zeros_opt)
            if not feasible:
                continue
            N_min = N
            notches_pos = np.sort(np.abs(zeros_opt[0::2])) if m > 0 else np.array([])
            sol = {'N': N, 'k': k, 'notches': m, 'notches_lowpass': notches_pos,
                   'zeros_lowpass': np.array(zeros_opt),
                   'min_margin_dB': float(np.min(attens - A_s_arr)),
                   'pass_max_dB': float(np.max(pass_attens))}
            break
        if N_min is None:
            stop_note = f"m={m}: 在 N ≤ {N_max_search} 内无可行解"; break
        # 截断判据: 该 m 的最小阶数超过已找到的最优阶数, 说明增加零点不再降阶, 停止枚举
        # (2m ≤ N 已由 N_start = max(1, 2m) 在构造层强制, 无需再查 m ≤ N/2)
        if solutions:
            best_so_far = min(s['N'] for s in solutions)
            if N_min > best_so_far:
                stop_note = (f"m={m}: 最小 N={N_min} > 当前最优 N={best_so_far}, "
                             f"增加零点不再降阶 → 停止枚举")
                break
        solutions.append(sol)

    if not solutions:
        print("\n✗ 未找到可行解")
        return {'success': False}

    best = min(solutions, key=lambda s: s['N'])

    def notch_row(sol):
        return [float(z * f_p) for z in sol['notches_lowpass']]

    P, F, E = synthesize_polys(best['N'], best['zeros_lowpass'], epsilon_sq)

    def atten_at(freqs):
        Om_v = lowpass_normalize(f_p, np.asarray(freqs, dtype=float))
        return evaluate_attenuation_with_Q(Om_v, P, F, E, epsilon_sq, Q_unloaded) if Q_unloaded \
            else evaluate_attenuation(Om_v, P, F, E, epsilon_sq)

    verify_rows = []
    for spec in stopband_specs:
        if len(spec) == 2:
            freqs, A_s = np.array([spec[0]], dtype=float), float(spec[1])
        else:
            freqs = np.linspace(float(spec[0]), float(spec[1]), 401); A_s = float(spec[2])
        att_v = atten_at(freqs); i_w = int(np.argmin(att_v))
        verify_rows.append((float(freqs[i_w]), float(att_v[i_w]), A_s))

    if verbose:
        print(f"\n  m(notch)  最小N  最差阻带裕量   零点(GHz)")
        for s in solutions:
            zs = notch_row(s)
            zs_str = ", ".join(f"{z/1e9:.4f}" for z in zs) if zs else "—"
            print(f"     {s['notches']:>2}      {s['N']:>4}    {s['min_margin_dB']:>+8.2f} dB   [{zs_str}]")
        if stop_note:
            print(f"  [停止] {stop_note}")
        print(f"\n  → 最小阶解: N = {best['N']}, 零点对数 m = {best['notches']}")
        if best['notches'] > 0:
            print(f"    零点幅度(归一化Ω): {np.round(best['notches_lowpass'],4).tolist()}")
            print(f"    零点频率: {[round(z,6) for z in notch_row(best)]} Hz")

    return {'success': True, 'N': int(best['N']), 'k': int(best['k']),
            'notches': int(best['notches']),
            'notches_lowpass': best['notches_lowpass'].tolist(),
            'notches_Hz': [float(z) for z in notch_row(best)],
            'f_p': f_p, 'verify_rows': verify_rows,
            'solutions': [{'N': s['N'], 'notches': s['notches'],
                           'notches_Hz': [float(z) for z in notch_row(s)],
                           'min_margin_dB': s['min_margin_dB']} for s in solutions]}


if __name__ == '__main__':
    np.set_printoptions(precision=6, suppress=True)
    SPEC = [(2.0e9, 3.0e9, 40.0)]
    print("\n########## 无损 (Q = ∞) ##########")
    estimate_lowpass_filter_order(1.0e9, SPEC, RL_dB=-20.0, Q_unloaded=None, verbose=True)
    for Q in (200.0, 100.0):
        print(f"\n########## 含损耗 Q = {Q:.0f} ##########")
        estimate_lowpass_filter_order(1.0e9, SPEC, RL_dB=-20.0, Q_unloaded=Q, verbose=True)
