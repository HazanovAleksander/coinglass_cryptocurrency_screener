"""Cross-coin analytics: log returns, correlations/covariances, RV, beta.

Pure functions on ``{ts: value}`` dicts — no I/O; callers load series from
the store and assemble the report via ``build_report``.
"""
from __future__ import annotations

import math

RV_WINDOW = 7  # trailing days of log returns per RV sample (matches RV chart)


def series_closes(pts: list[tuple[int, dict]]) -> dict[int, float]:
    """{ts: close} for points carrying a numeric close (works for price and
    funding: both store the daily value under ``close``)."""
    out: dict[int, float] = {}
    for ts, vals in pts:
        c = vals.get("close")
        if c is not None:
            out[int(ts)] = float(c)
    return out


def log_returns(series: dict[int, float]) -> dict[int, float]:
    """Log returns of adjacent available points, keyed by the later ts."""
    out: dict[int, float] = {}
    prev: float | None = None
    for ts in sorted(series):
        v = series[ts]
        if prev is not None and prev > 0 and v > 0:
            out[ts] = math.log(v / prev)
        prev = v
    return out


def rolling_rv(closes: dict[int, float], window: int = RV_WINDOW) -> dict[int, float]:
    """Rolling realized volatility (annualized fraction) of trailing-window
    log returns, sample std, matching drawVolHistory/exports rv_7d."""
    rets = log_returns(closes)
    ts_sorted = sorted(rets)
    out: dict[int, float] = {}
    for i in range(window - 1, len(ts_sorted)):
        chunk = [rets[t] for t in ts_sorted[i - window + 1 : i + 1]]
        m = sum(chunk) / window
        var = sum((r - m) ** 2 for r in chunk) / (window - 1)
        out[ts_sorted[i]] = math.sqrt(var) * math.sqrt(365.0)
    return out


def align(a: dict[int, float], b: dict[int, float]) -> tuple[list[float], list[float]]:
    """Both series restricted to the intersection of their ts, in ts order."""
    common = sorted(set(a) & set(b))
    return [a[t] for t in common], [b[t] for t in common]


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sxx = sum(d * d for d in dx)
    syy = sum(d * d for d in dy)
    if sxx <= 0 or syy <= 0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / math.sqrt(sxx * syy)


def covariance(xs: list[float], ys: list[float], ddof: int = 1) -> float | None:
    n = len(xs)
    if n <= ddof or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n - ddof)


def beta(xs: list[float], ys: list[float]) -> float | None:
    """Beta of ys against xs (cov/var)."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sxx = sum(d * d for d in dx)
    if sxx <= 0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / sxx


def _sym_matrix(n: int) -> list[list]:
    return [[None] * n for _ in range(n)]


def build_report(
    top: list[str],
    closes: dict[str, dict[int, float]],
    funding: dict[str, dict[int, float]],
    min_overlap: int = 30,
    base: str = "BTC",
) -> dict:
    """Assemble the correlations report.

    ``top`` fixes the symbol order (top-20); ``closes``/``funding`` map the
    symbols that have cached data to ``{ts: value}``. Cells with fewer than
    ``min_overlap`` aligned observations are ``None``.
    """
    symbols = [s for s in top if s in closes]
    missing = [s for s in top if s not in closes]
    rets = {s: log_returns(closes[s]) for s in symbols}
    rvs = {s: rolling_rv(closes[s]) for s in symbols}
    n = len(symbols)
    corr = {"returns": _sym_matrix(n), "rv": _sym_matrix(n), "funding": _sym_matrix(n)}
    cov = {"returns": _sym_matrix(n)}
    overlap = [[0] * n for _ in range(n)]

    for i, a in enumerate(symbols):
        for j in range(i, n):
            b = symbols[j]
            if i == j:
                corr["returns"][i][i] = 1.0
                corr["rv"][i][i] = 1.0
                corr["funding"][i][i] = 1.0
                ra = list(rets[a].values())
                cov["returns"][i][i] = covariance(ra, ra)
                overlap[i][i] = len(ra)
                continue
            xa, xb = align(rets[a], rets[b])
            overlap[i][j] = overlap[j][i] = len(xa)
            if len(xa) >= min_overlap:
                corr["returns"][i][j] = corr["returns"][j][i] = pearson(xa, xb)
                cov["returns"][i][j] = cov["returns"][j][i] = covariance(xa, xb)
            va, vb = align(rvs[a], rvs[b])
            if len(va) >= min_overlap:
                corr["rv"][i][j] = corr["rv"][j][i] = pearson(va, vb)
            if a in funding and b in funding:
                fa, fb = align(funding[a], funding[b])
                if len(fa) >= min_overlap:
                    corr["funding"][i][j] = corr["funding"][j][i] = pearson(fa, fb)

    base_rets = rets.get(base)
    stats = []
    for s in symbols:
        cl = closes[s]
        rv = rvs[s].values()
        fund = funding.get(s, {}).values()
        b = None
        if base_rets is not None and s != base:
            xs, ys = align(base_rets, rets[s])
            if len(xs) >= 2:
                b = beta(xs, ys)
        stats.append({
            "symbol": s,
            "days": len(cl),
            "from_ts": min(cl),
            "to_ts": max(cl),
            "rv_mean": (sum(rv) / len(rv) * 100) if rv else None,
            "rv_last": (max(rvs[s].items())[1] * 100) if rvs[s] else None,
            "funding_mean": (sum(fund) / len(fund) * 100) if fund else None,
            "funding_last": (sorted(funding[s].items())[-1][1] * 100) if funding.get(s) else None,
            "beta": 1.0 if s == base and base_rets is not None else b,
        })

    return {
        "symbols": symbols,
        "missing": missing,
        "min_overlap": min_overlap,
        "base": base if base in symbols else None,
        "corr": corr,
        "cov": cov,
        "overlap": overlap,
        "stats": stats,
    }
