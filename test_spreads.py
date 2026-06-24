"""
Data loaders for DAM Top-Bottom Spread dashboard.
Sources:
- EU: Energy-Charts API /price, EUR/MWh.
- UA: OREE public monthly hourly DAM table, UAH/MWh, VAT excluded.
- FX: NBU official EUR/UAH daily rate.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import EU_ZONES, UA_ZONE

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

ENERGY_CHARTS_PRICE_URL = "https://api.energy-charts.info/price"
OREE_PRICECTR_URL = "https://www.oree.com.ua/index.php/pricectr"
NBU_FX_URL = "https://bank.gov.ua/NBU_Exchange/exchange_site"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; tb-spread-dashboard/1.0; market analytics)"
}


def _request_json(url: str, params: dict, timeout: int = 30) -> object:
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _request_text(url: str, params: dict | None = None, timeout: int = 30) -> str:
    r = requests.get(url, params=params or {}, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    # OREE is usually utf-8, but requests can guess cp1251 in some environments.
    if not r.encoding or r.encoding.lower() in {"iso-8859-1", "windows-1252"}:
        r.encoding = "utf-8"
    return r.text


# ---------- Energy-Charts ----------
def fetch_energycharts_zone(bzn: str, start: date, end: date) -> pd.DataFrame:
    """Fetch hourly DAM prices for one bidding zone from Energy-Charts.

    Returns columns: datetime_utc, datetime_local, date_local, hour_local, zone, price_eur.
    """
    js = _request_json(
        ENERGY_CHARTS_PRICE_URL,
        {"bzn": bzn, "start": start.isoformat(), "end": end.isoformat()},
    )
    if "unix_seconds" not in js or "price" not in js:
        raise ValueError(f"Unexpected Energy-Charts response for {bzn}: keys={list(js)[:10]}")

    tz = EU_ZONES[bzn].timezone
    df = pd.DataFrame(
        {
            "datetime_utc": pd.to_datetime(js["unix_seconds"], unit="s", utc=True),
            "price_eur": pd.to_numeric(js["price"], errors="coerce"),
        }
    ).dropna(subset=["price_eur"])
    df["datetime_local"] = df["datetime_utc"].dt.tz_convert(tz)
    df["date_local"] = df["datetime_local"].dt.date
    df["hour_local"] = df["datetime_local"].dt.hour + 1
    df["zone"] = bzn
    return df[["datetime_utc", "datetime_local", "date_local", "hour_local", "zone", "price_eur"]]


def fetch_energycharts(zones: Iterable[str], start: date, end: date, sleep_s: float = 0.15) -> pd.DataFrame:
    frames = []
    errors = []
    for z in zones:
        try:
            frames.append(fetch_energycharts_zone(z, start, end))
            time.sleep(sleep_s)
        except Exception as exc:
            errors.append((z, str(exc)))
    if not frames:
        raise RuntimeError(f"No EU data loaded. Errors: {errors}")
    df = pd.concat(frames, ignore_index=True)
    df.attrs["errors"] = errors
    return df


# ---------- OREE ----------
def parse_oree_pricectr_html(html: str, year: int | None = None, month: int | None = None) -> pd.DataFrame:
    """Parse OREE pricectr page with 24 hourly columns.

    The public page exposes a monthly table: Date + columns 1...24. This parser is defensive:
    it scans all tables and keeps rows where the first cell is dd.mm.yyyy and at least 24 numeric cells follow.
    """
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True).replace("\xa0", " ") for c in tr.find_all(["td", "th"])]
            if len(cells) < 25:
                continue
            dt = pd.to_datetime(cells[0], format="%d.%m.%Y", errors="coerce")
            if pd.isna(dt):
                continue
            if year is not None and dt.year != year:
                continue
            if month is not None and dt.month != month:
                continue
            for h in range(1, 25):
                raw = cells[h].replace(" ", "").replace(",", ".")
                price = pd.to_numeric(raw, errors="coerce")
                if pd.notna(price):
                    local_ts = pd.Timestamp(dt.date()).tz_localize(UA_ZONE.timezone) + pd.Timedelta(hours=h - 1)
                    rows.append(
                        {
                            "datetime_utc": local_ts.tz_convert("UTC"),
                            "datetime_local": local_ts,
                            "date_local": local_ts.date(),
                            "hour_local": h,
                            "zone": UA_ZONE.code,
                            "price_uah": float(price),
                        }
                    )
    if not rows:
        raise ValueError("Could not find OREE hourly DAM table in HTML")
    df = pd.DataFrame(rows).drop_duplicates(["date_local", "hour_local", "zone"]).sort_values(["date_local", "hour_local"])
    return df


def fetch_oree_month(year: int, month: int) -> pd.DataFrame:
    # OREE has historically accepted date-like query parameters inconsistently; keep several variants for resilience.
    html = None
    attempted = []
    for params in [
        {"date": f"{month:02d}.{year}"},
        {"month": f"{month:02d}", "year": str(year)},
        {},
    ]:
        try:
            attempted.append(params)
            html = _request_text(OREE_PRICECTR_URL, params=params)
            df = parse_oree_pricectr_html(html, year=year, month=month)
            return df
        except Exception:
            continue
    raise RuntimeError(f"Failed to fetch/parse OREE month {year}-{month:02d}; attempted={attempted}")


def fetch_oree_range(start: date, end: date) -> pd.DataFrame:
    months = pd.period_range(start, end, freq="M")
    frames = [fetch_oree_month(p.year, p.month) for p in months]
    df = pd.concat(frames, ignore_index=True)
    mask = (pd.to_datetime(df["date_local"]) >= pd.Timestamp(start)) & (pd.to_datetime(df["date_local"]) <= pd.Timestamp(end))
    return df.loc[mask].reset_index(drop=True)


# ---------- NBU FX ----------
def fetch_nbu_eur(start: date, end: date) -> pd.DataFrame:
    js = _request_json(
        NBU_FX_URL,
        {
            "start": pd.Timestamp(start).strftime("%Y%m%d"),
            "end": pd.Timestamp(end).strftime("%Y%m%d"),
            "valcode": "eur",
            "sort": "exchangedate",
            "order": "asc",
            "json": "",
        },
    )
    df = pd.DataFrame(js)
    if df.empty:
        raise ValueError("NBU returned empty FX dataset")
    df["date_local"] = pd.to_datetime(df["exchangedate"], format="%d.%m.%Y", errors="coerce").dt.date
    df["uah_per_eur"] = pd.to_numeric(df["rate"], errors="coerce")
    return df[["date_local", "uah_per_eur"]].dropna().drop_duplicates("date_local")


def attach_fx_to_ua(ua: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    ua = ua.copy()
    fx_idx = fx.copy()
    fx_idx["date_local"] = pd.to_datetime(fx_idx["date_local"])
    full_dates = pd.date_range(pd.to_datetime(ua["date_local"]).min(), pd.to_datetime(ua["date_local"]).max(), freq="D")
    fx_full = (
        fx_idx.set_index("date_local")[["uah_per_eur"]]
        .reindex(full_dates)
        .ffill()
        .bfill()
        .rename_axis("date_local")
        .reset_index()
    )
    fx_full["date_local"] = fx_full["date_local"].dt.date
    ua = ua.merge(fx_full, on="date_local", how="left")
    ua["price_eur"] = ua["price_uah"] / ua["uah_per_eur"]
    return ua
