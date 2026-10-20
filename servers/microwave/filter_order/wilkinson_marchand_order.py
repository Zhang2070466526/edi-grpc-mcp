#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wilkinson 功分器 / Marchand 巴伦 阶数估算

逻辑: 切比雪夫公式对 N≥2 有效; 公式给出 N=1 时用单节三端口精确
响应校验带边隔离度, 达标则 N=1, 否则升为 N=2。
用法: python wilkinson_marchand_order.py 1 6 [--rl 10]
"""

import argparse
import math

RL_DEFAULT = 15.0


def order_formula(ratio, rl_db, R=2.0):
    theta_m = math.pi / (1.0 + ratio)
    gamma0 = (R - 1.0) / (R + 1.0)
    gamma_m = 10.0 ** (-rl_db / 20.0)
    if gamma_m >= gamma0:
        return 1
    u = math.acosh(gamma0 / gamma_m)
    v = math.acosh(1.0 / math.cos(theta_m))
    return max(1, math.ceil(u / v - 1e-9))


def single_section_bandedge(ratio):
    """单节 Wilkinson 带边精确响应 (S31, S21)。"""
    z0 = 50.0
    theta = math.pi / (1.0 + ratio)
    y = 1.0 / (math.sqrt(2.0) * z0)
    g = 1.0 / z0
    rr = 1.0 / (2.0 * z0)
    c1 = 1.0 / math.tan(theta)
    c2 = 1.0 / math.sin(theta)
    k = (c2 * y) ** 2 / (g - 2j * c1 * y)
    d1 = g - 1j * c1 * y + 2.0 * k
    d2 = g - 1j * c1 * y + 2.0 * rr
    s31 = abs(1.0 / d1 - 1.0 / d2) / z0
    s21 = 2.0 * c2 * y * abs(1.0 / d1) / (abs(g - 2j * c1 * y) * z0)
    return s31, s21


def order_final(ratio, rl_db):
    n = order_formula(ratio, rl_db)
    if n > 1:
        return n
    s31, _ = single_section_bandedge(ratio)
    iso_db = -20.0 * math.log10(s31) if s31 > 0 else float("inf")
    return 1 if iso_db >= rl_db else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="+", help="频率比 或 低频 高频 (GHz)")
    ap.add_argument("--rl", type=float, default=None, help=f"指标 dB, 缺省 {RL_DEFAULT:g}")
    args = ap.parse_args()

    rl_db = args.rl if args.rl is not None else RL_DEFAULT
    r = (float(args.spec[1]) / float(args.spec[0])) if len(args.spec) == 2 else float(args.spec[0])

    # Wilkinson 与 Marchand 同为切比雪夫变阻综合, R=2 时阶数相同, 合并输出
    print(f"N={order_final(r, rl_db)}")


if __name__ == "__main__":
    main()
