"""Closed-form Black-Scholes price of a European call (the correctness reference)."""

from __future__ import annotations

import math


def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def call_price(S0: float, K: float, r: float, sigma: float, T: float) -> float:
    if sigma <= 0 or T <= 0:
        raise ValueError("sigma and T must be positive")
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S0 / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return S0 * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
