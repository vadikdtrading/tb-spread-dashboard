from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from config import DEFAULT_ZONES, EU_ZONES, UA_ZONE
from data_sources import fetch_energycharts, fetch_oree_range, fetch_nbu_eur, attach_fx_to_ua
from spreads import top_bottom_spread, bess_arbitrage_proxy

st.set_page_config(page_title="DAM Top-Bottom Spread", layout="wide", page_icon="⚡")

st.title("⚡ DAM Top-Bottom 2/4 Spread — Ukraine vs EU")
st.caption("Day-ahead electricity prices · sources: Energy-Charts, OREE, NBU")

with st.sidebar:
    st.header("Controls")
    end_date = st.date_input("End date", value=date.today() - timedelta(days=1))
    start_date = st.date_input("Start date", value=end_date - timedelta(days=90))
    if start_date > end_date:
        st.error("Start date must be before end date")
        st.stop()
    zones = st.multiselect("EU bidding zones", list(EU_ZONES.keys()), default=DEFAULT_ZONES)
    include_ua = st.toggle("Include Ukraine", value=True)
    metric = st.radio("Spread metric", ["TB2", "TB4"], horizontal=True)
    n = int(metric[-1])
    rte = st.slider("BESS round-trip efficiency", 0.70, 1.00, 0.88, 0.01)
    show_raw = st.toggle("Show raw data", value=False)

@st.cache_data(ttl=3600, show_spinner="Loading EU DAM prices…")
def load_eu(zones, start_date, end_date):
    if not zones:
        return pd.DataFrame(columns=["datetime_utc", "datetime_local", "date_local", "hour_local", "zone", "price_eur"])
    return fetch_energycharts(zones, start_date, end_date)

@st.cache_data(ttl=3600, show_spinner="Loading Ukraine DAM prices from OREE…")
def load_ua(start_date, end_date):
    ua = fetch_oree_range(start_date, end_date)
    fx = fetch_nbu_eur(start_date, end_date)
    return attach_fx_to_ua(ua, fx)

errors = []
frames = []
if zones:
    eu = load_eu(tuple(zones), start_date, end_date)
    errors.extend(eu.attrs.get("errors", []))
    frames.append(eu)
if include_ua:
    try:
        frames.append(load_ua(start_date, end_date))
    except Exception as exc:
        st.warning(f"Ukraine data could not be loaded: {exc}")

if not frames:
    st.warning("Select at least one EU zone or include Ukraine.")
    st.stop()

prices = pd.concat(frames, ignore_index=True)
prices["date_local"] = pd.to_datetime(prices["date_local"]).dt.date

if errors:
    with st.expander("Data loading warnings"):
        for z, msg in errors:
            st.write(f"- {z}: {msg}")

spread = top_bottom_spread(prices, n=n)
bess = bess_arbitrage_proxy(prices, duration_h=n, round_trip_efficiency=rte)
latest_date = max(spread["date_local"])
latest = spread[spread["date_local"] == latest_date].sort_values(f"TB{n}", ascending=False)

st.subheader(f"Latest available day: {latest_date}")
card_count = min(6, max(1, len(latest)))
cols = st.columns(card_count)
for col, (_, row) in zip(cols, latest.head(card_count).iterrows()):
    col.metric(row["zone"], f"€{row[f'TB{n}']:.1f}/MWh")

c1, c2 = st.columns([2, 1])
with c1:
    fig = px.line(
        spread,
        x="date_local",
        y=f"TB{n}",
        color="zone",
        title=f"Top-Bottom {n} daily spread, EUR/MWh",
        labels={"date_local": "Date", f"TB{n}": "EUR/MWh", "zone": "Zone"},
    )
    fig.update_layout(hovermode="x unified", height=430)
    st.plotly_chart(fig, use_container_width=True)
with c2:
    avg = spread.groupby("zone", as_index=False)[f"TB{n}"].mean().sort_values(f"TB{n}")
    fig = px.bar(
        avg,
        x=f"TB{n}",
        y="zone",
        orientation="h",
        title=f"Average TB{n} over period",
        labels={f"TB{n}": "EUR/MWh", "zone": ""},
    )
    fig.update_layout(height=430)
    st.plotly_chart(fig, use_container_width=True)

pivot = spread.pivot(index="zone", columns="date_local", values=f"TB{n}")
fig = px.imshow(
    pivot,
    aspect="auto",
    title=f"TB{n} heatmap, EUR/MWh",
    labels={"x": "Date", "y": "Zone", "color": "EUR/MWh"},
)
st.plotly_chart(fig, use_container_width=True)

st.subheader("BESS arbitrage proxy")
fig = px.line(
    bess,
    x="date_local",
    y="net_proxy_eur_mw_day",
    color="zone",
    title=f"{n}h BESS net proxy with RTE={rte:.0%}, EUR/MW/day",
    labels={"date_local": "Date", "net_proxy_eur_mw_day": "EUR/MW/day", "zone": "Zone"},
)
fig.update_layout(hovermode="x unified", height=400)
st.plotly_chart(fig, use_container_width=True)

with st.expander("Downloads"):
    st.download_button("Download hourly prices CSV", prices.to_csv(index=False).encode("utf-8"), "hourly_prices.csv")
    st.download_button(f"Download TB{n} CSV", spread.to_csv(index=False).encode("utf-8"), f"TB{n}.csv")
    st.download_button("Download BESS proxy CSV", bess.to_csv(index=False).encode("utf-8"), "bess_proxy.csv")

if show_raw:
    st.subheader("Raw hourly prices")
    st.dataframe(prices, use_container_width=True, height=350)
