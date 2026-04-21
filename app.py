import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
import plotly.express as px

st.set_page_config(page_title="CHF Treasury Strategy Tracker", layout="wide")

TREASURY_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
SNAPSHOT_PATH = Path("data/snapshots.csv")


@st.cache_data(ttl=900)
def fetch_usdchf() -> float:
    ticker = yf.Ticker("USDCHF=X")
    hist = ticker.history(period="5d", interval="1d", auto_adjust=False)
    if hist.empty:
        raise ValueError("No USD/CHF data returned from Yahoo Finance.")
    return float(hist["Close"].dropna().iloc[-1])


@st.cache_data(ttl=3600)
def fetch_treasury_curve() -> pd.DataFrame:
    year = datetime.utcnow().year
    url = TREASURY_URL.format(year=year)
    tables = pd.read_html(url)
    table = None
    for candidate in tables:
        cols = [str(c).strip().lower() for c in candidate.columns]
        if "date" in cols and "6 mo" in cols:
            table = candidate.copy()
            break
    if table is None:
        raise ValueError("Could not parse Treasury yield curve table.")
    table.columns = [str(c).strip() for c in table.columns]
    table["Date"] = pd.to_datetime(table["Date"], errors="coerce")
    table = table.dropna(subset=["Date"]).sort_values("Date")
    numeric_cols = [c for c in table.columns if c != "Date"]
    for col in numeric_cols:
        table[col] = pd.to_numeric(table[col], errors="coerce")
    return table


def load_snapshots() -> pd.DataFrame:
    if SNAPSHOT_PATH.exists():
        df = pd.read_csv(SNAPSHOT_PATH, parse_dates=["timestamp"])
        return df.sort_values("timestamp")
    return pd.DataFrame()


def save_snapshot(row: dict) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    exists = SNAPSHOT_PATH.exists()
    pd.DataFrame([row]).to_csv(SNAPSHOT_PATH, mode="a", header=not exists, index=False)


@st.cache_data(ttl=3600)
def latest_curve_points() -> dict:
    curve = fetch_treasury_curve()
    latest = curve.iloc[-1]
    prior = curve.iloc[-2] if len(curve) > 1 else latest
    return {
        "date": latest["Date"].date(),
        "6m": float(latest["6 Mo"]),
        "3m": float(latest.get("3 Mo", float("nan"))),
        "1y": float(latest.get("1 Yr", float("nan"))),
        "30y": float(latest.get("30 Yr", float("nan"))),
        "6m_change_bps": (float(latest["6 Mo"]) - float(prior["6 Mo"])) * 100,
    }



def compute_strategy(launch_equity: float,
                     launch_borrowed_usd: float,
                     launch_usdchf: float,
                     current_usdchf: float,
                     treasury_yield: float,
                     reinvest_yield: float,
                     borrow_spread: float,
                     borrow_benchmark: float,
                     annual_outflow: float,
                     core_margin_rate: float,
                     side_margin_rate: float,
                     manual_side_bucket: float,
                     projection_years: int) -> tuple[dict, pd.DataFrame]:
    debt_chf = launch_borrowed_usd * launch_usdchf
    debt_usd_now = debt_chf / current_usdchf
    total_assets_now = launch_equity + launch_borrowed_usd + manual_side_bucket
    gross_income_now = (launch_equity + launch_borrowed_usd) * treasury_yield / 100 + manual_side_bucket * reinvest_yield / 100
    borrow_rate = max(0.0, borrow_benchmark) + borrow_spread
    debt_interest_now = debt_usd_now * borrow_rate / 100
    retained_carry_now = gross_income_now - debt_interest_now - annual_outflow
    margin_requirement_now = (launch_equity + launch_borrowed_usd) * core_margin_rate / 100 + manual_side_bucket * side_margin_rate / 100
    equity_now = total_assets_now - debt_usd_now
    excess_liquidity_now = equity_now - margin_requirement_now
    fx_margin_call = debt_chf / max(1.0, total_assets_now - margin_requirement_now)
    fx_break_even_starting_equity = debt_chf / max(1.0, total_assets_now - launch_equity)

    rows = []
    side_bucket = manual_side_bucket
    core_assets = launch_equity + launch_borrowed_usd
    for year in range(1, projection_years + 1):
        core_income = core_assets * treasury_yield / 100
        side_income = side_bucket * reinvest_yield / 100
        gross_income = core_income + side_income
        debt_interest = debt_usd_now * borrow_rate / 100
        retained = gross_income - debt_interest - annual_outflow
        side_bucket += retained
        total_assets = core_assets + side_bucket
        equity = total_assets - debt_usd_now
        margin_requirement = core_assets * core_margin_rate / 100 + max(0.0, side_bucket) * side_margin_rate / 100
        excess_liquidity = equity - margin_requirement
        fx_margin_call_year = debt_chf / max(1.0, total_assets - margin_requirement)
        rows.append({
            "Year": year,
            "Core Income": core_income,
            "Side Income": side_income,
            "Gross Income": gross_income,
            "Debt Interest": debt_interest,
            "Outflow": annual_outflow,
            "Retained": retained,
            "Side Bucket": side_bucket,
            "Total Assets": total_assets,
            "Debt USD": debt_usd_now,
            "Equity": equity,
            "Margin Requirement": margin_requirement,
            "Excess Liquidity": excess_liquidity,
            "FX Margin Call (USD/CHF)": fx_margin_call_year,
        })

    current = {
        "Debt CHF": debt_chf,
        "Debt USD": debt_usd_now,
        "Total Assets": total_assets_now,
        "Equity": equity_now,
        "Gross Income": gross_income_now,
        "Debt Interest": debt_interest_now,
        "Retained Carry": retained_carry_now,
        "Margin Requirement": margin_requirement_now,
        "Excess Liquidity": excess_liquidity_now,
        "FX Margin Call (USD/CHF)": fx_margin_call,
        "FX Break-even vs Starting Equity": fx_break_even_starting_equity,
        "Borrow Rate": borrow_rate,
    }
    return current, pd.DataFrame(rows)


st.title("CHF-Funded Treasury Strategy Tracker")
st.caption("Tracks the hypothetical leveraged Treasury trade using live market inputs and your strategy assumptions.")

with st.sidebar:
    st.header("Assumptions")
    auto_fetch = st.toggle("Use live market data", value=True)
    launch_equity = st.number_input("Launch equity (USD)", min_value=0.0, value=5_000_000.0, step=100_000.0, format="%.2f")
    launch_borrowed_usd = st.number_input("Launch borrowed USD equivalent", min_value=0.0, value=25_000_000.0, step=100_000.0, format="%.2f")
    launch_usdchf = st.number_input("USD/CHF at launch", min_value=0.0001, value=0.7823, step=0.0001, format="%.4f")
    annual_outflow = st.number_input("Annual TTD-servicing outflow (USD)", min_value=0.0, value=350_000.0, step=25_000.0, format="%.2f")
    borrow_spread = st.number_input("Borrow spread over CHF benchmark (%)", min_value=0.0, value=0.80, step=0.05, format="%.2f")
    borrow_benchmark = st.number_input("CHF benchmark / policy rate (%)", value=0.00, step=0.05, format="%.2f")
    reinvest_yield = st.number_input("Reinvestment yield (%)", min_value=0.0, value=6.00, step=0.10, format="%.2f")
    core_margin_rate = st.number_input("Core Treasury maintenance margin (%)", min_value=0.0, value=2.00, step=0.25, format="%.2f")
    side_margin_rate = st.number_input("Side bucket margin (%)", min_value=0.0, value=2.00, step=0.25, format="%.2f")
    manual_side_bucket = st.number_input("Manual side-bucket value (USD)", min_value=0.0, value=0.0, step=50_000.0, format="%.2f")
    projection_years = st.slider("Projection years", min_value=1, max_value=20, value=10)
    auto_log = st.toggle("Append snapshot on refresh", value=False)
    log_note = st.text_input("Snapshot note", value="")

curve_error = None
fx_error = None
curve = None
curve_points = None

if auto_fetch:
    try:
        curve = fetch_treasury_curve()
        curve_points = latest_curve_points()
        treasury_yield = curve_points["6m"]
    except Exception as exc:
        curve_error = str(exc)
        treasury_yield = 3.60
    try:
        current_usdchf = fetch_usdchf()
    except Exception as exc:
        fx_error = str(exc)
        current_usdchf = launch_usdchf
else:
    treasury_yield = st.sidebar.number_input("Manual 6-month Treasury yield (%)", min_value=0.0, value=3.60, step=0.05, format="%.2f")
    current_usdchf = st.sidebar.number_input("Manual USD/CHF", min_value=0.0001, value=launch_usdchf, step=0.0001, format="%.4f")

current, projection = compute_strategy(
    launch_equity=launch_equity,
    launch_borrowed_usd=launch_borrowed_usd,
    launch_usdchf=launch_usdchf,
    current_usdchf=current_usdchf,
    treasury_yield=treasury_yield,
    reinvest_yield=reinvest_yield,
    borrow_spread=borrow_spread,
    borrow_benchmark=borrow_benchmark,
    annual_outflow=annual_outflow,
    core_margin_rate=core_margin_rate,
    side_margin_rate=side_margin_rate,
    manual_side_bucket=manual_side_bucket,
    projection_years=projection_years,
)

if auto_log:
    save_snapshot({
        "timestamp": datetime.utcnow().isoformat(),
        "note": log_note,
        "usdchf": current_usdchf,
        "treasury_6m": treasury_yield,
        "borrow_rate": current["Borrow Rate"],
        "assets": current["Total Assets"],
        "debt_usd": current["Debt USD"],
        "equity": current["Equity"],
        "margin_requirement": current["Margin Requirement"],
        "excess_liquidity": current["Excess Liquidity"],
        "retained_carry": current["Retained Carry"],
        "fx_margin_call": current["FX Margin Call (USD/CHF)"],
    })

col1, col2, col3, col4 = st.columns(4)
col1.metric("USD/CHF now", f"{current_usdchf:.4f}")
col2.metric("6M Treasury yield", f"{treasury_yield:.2f}%", None if curve_points is None else f"{curve_points['6m_change_bps']:.1f} bps d/d")
col3.metric("CHF debt in USD", f"${current['Debt USD']:,.0f}")
col4.metric("Borrow rate", f"{current['Borrow Rate']:.2f}%")

col5, col6, col7, col8 = st.columns(4)
col5.metric("Equity", f"${current['Equity']:,.0f}")
col6.metric("Maintenance margin", f"${current['Margin Requirement']:,.0f}")
col7.metric("Excess liquidity", f"${current['Excess Liquidity']:,.0f}")
col8.metric("FX margin-call level", f"{current['FX Margin Call (USD/CHF)']:.3f}")

if curve_error:
    st.warning(f"Treasury live fetch failed, using manual/default value. {curve_error}")
if fx_error:
    st.warning(f"Yahoo FX fetch failed, using manual/default value. {fx_error}")

st.subheader("Live carry view")
carry_cols = st.columns(4)
carry_cols[0].metric("Gross annual income", f"${current['Gross Income']:,.0f}")
carry_cols[1].metric("Annual debt interest", f"${current['Debt Interest']:,.0f}")
carry_cols[2].metric("Annual outflow", f"${annual_outflow:,.0f}")
carry_cols[3].metric("Retained carry", f"${current['Retained Carry']:,.0f}")

left, right = st.columns([1.2, 1])
with left:
    st.subheader("Projection")
    chart_df = projection[["Year", "Total Assets", "Equity", "Margin Requirement", "Excess Liquidity"]].melt("Year", var_name="Series", value_name="USD")
    fig = px.line(chart_df, x="Year", y="USD", color="Series", markers=True)
    fig.update_layout(height=420, yaxis_title="USD", xaxis_title="Year")
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("FX danger map")
    latest_row = projection.iloc[-1]
    fx_table = pd.DataFrame({
        "Scenario USD/CHF": [0.78, 0.76, 0.74, 0.70, 0.65, 0.60],
    })
    debt_chf = current["Debt CHF"]
    total_assets = current["Total Assets"]
    margin_req = current["Margin Requirement"]
    fx_table["Debt USD"] = fx_table["Scenario USD/CHF"].apply(lambda x: debt_chf / x)
    fx_table["Equity"] = total_assets - fx_table["Debt USD"]
    fx_table["Excess Liquidity"] = fx_table["Equity"] - margin_req
    st.dataframe(fx_table.style.format({"Scenario USD/CHF": "{:.3f}", "Debt USD": "${:,.0f}", "Equity": "${:,.0f}", "Excess Liquidity": "${:,.0f}"}), use_container_width=True)

st.subheader("Projected income statement + balance sheet")
st.dataframe(
    projection.style.format({
        "Core Income": "${:,.0f}",
        "Side Income": "${:,.0f}",
        "Gross Income": "${:,.0f}",
        "Debt Interest": "${:,.0f}",
        "Outflow": "${:,.0f}",
        "Retained": "${:,.0f}",
        "Side Bucket": "${:,.0f}",
        "Total Assets": "${:,.0f}",
        "Debt USD": "${:,.0f}",
        "Equity": "${:,.0f}",
        "Margin Requirement": "${:,.0f}",
        "Excess Liquidity": "${:,.0f}",
        "FX Margin Call (USD/CHF)": "{:.3f}",
    }),
    use_container_width=True,
)

snapshots = load_snapshots()
if not snapshots.empty:
    st.subheader("Saved snapshots")
    history_fig = px.line(snapshots, x="timestamp", y=["equity", "margin_requirement", "excess_liquidity"], markers=True)
    history_fig.update_layout(height=380, yaxis_title="USD", xaxis_title="Timestamp")
    st.plotly_chart(history_fig, use_container_width=True)
    st.dataframe(snapshots.sort_values("timestamp", ascending=False).style.format({
        "usdchf": "{:.4f}",
        "treasury_6m": "{:.2f}%",
        "borrow_rate": "{:.2f}%",
        "assets": "${:,.0f}",
        "debt_usd": "${:,.0f}",
        "equity": "${:,.0f}",
        "margin_requirement": "${:,.0f}",
        "excess_liquidity": "${:,.0f}",
        "retained_carry": "${:,.0f}",
        "fx_margin_call": "{:.3f}",
    }), use_container_width=True)
else:
    st.info("No snapshots logged yet. Turn on 'Append snapshot on refresh' in the sidebar to build a history file.")

with st.expander("Data notes"):
    st.markdown(
        """
- USD/CHF is fetched from Yahoo Finance via `yfinance` using ticker `USDCHF=X`.
- The 6-month Treasury yield is fetched from the U.S. Treasury daily yield curve table.
- The dashboard uses a carry-and-balance-sheet model, not a tick-by-tick broker simulator.
- Maintenance margin is modeled from your chosen assumptions, so it is a strategy monitor rather than an IB replacement.
        """
    )
