from __future__ import annotations

import pandas as pd


def top_bottom_spread(prices: pd.DataFrame, n: int = 2, price_col: str = "price_eur") -> pd.DataFrame:
    """Daily top-bottom n spread by zone.

    TBn = average of top-n hourly prices minus average of bottom-n hourly prices.
    Uses date_local to avoid UTC/local-day distortions around DST and cross-border comparisons.
    """
    if n not in {2, 4}:
        raise ValueError("n must be 2 or 4")
    required = {"zone", "date_local", price_col}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    def calc(g: pd.DataFrame) -> pd.Series:
        s = pd.to_numeric(g[price_col], errors="coerce").dropna()
        if len(s) < 2 * n:
            return pd.Series({f"TB{n}": pd.NA, f"top{n}_avg": pd.NA, f"bottom{n}_avg": pd.NA, "hours": len(s)})
        top = s.nlargest(n).mean()
        bottom = s.nsmallest(n).mean()
        return pd.Series({f"TB{n}": top - bottom, f"top{n}_avg": top, f"bottom{n}_avg": bottom, "hours": len(s)})

    out = prices.groupby(["zone", "date_local"], as_index=False).apply(calc, include_groups=False)
    return out.sort_values(["date_local", "zone"]).reset_index(drop=True)


def bess_arbitrage_proxy(
    prices: pd.DataFrame,
    duration_h: int = 2,
    round_trip_efficiency: float = 0.88,
    price_col: str = "price_eur",
) -> pd.DataFrame:
    """Simple daily BESS arbitrage proxy.

    Revenue proxy per MW = avg(top duration_h) * duration_h * sqrt(RTE)
                          - avg(bottom duration_h) * duration_h / sqrt(RTE)
    This is not an optimization model; it is a transparent spread proxy with efficiency losses.
    """
    if duration_h not in {2, 4}:
        raise ValueError("duration_h must be 2 or 4")
    if not (0 < round_trip_efficiency <= 1):
        raise ValueError("round_trip_efficiency must be in (0, 1]")

    eta_half = round_trip_efficiency ** 0.5

    def calc(g: pd.DataFrame) -> pd.Series:
        s = pd.to_numeric(g[price_col], errors="coerce").dropna()
        if len(s) < 2 * duration_h:
            return pd.Series({"gross_spread_eur_mw_day": pd.NA, "net_proxy_eur_mw_day": pd.NA, "hours": len(s)})
        top_avg = s.nlargest(duration_h).mean()
        bottom_avg = s.nsmallest(duration_h).mean()
        gross = (top_avg - bottom_avg) * duration_h
        net = top_avg * duration_h * eta_half - bottom_avg * duration_h / eta_half
        return pd.Series({
            "gross_spread_eur_mw_day": gross,
            "net_proxy_eur_mw_day": net,
            "top_avg": top_avg,
            "bottom_avg": bottom_avg,
            "hours": len(s),
        })

    return prices.groupby(["zone", "date_local"], as_index=False).apply(calc, include_groups=False).reset_index(drop=True)
