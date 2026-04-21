# CHF-Funded Treasury Strategy Tracker

A Streamlit dashboard for monitoring a hypothetical CHF-funded Treasury carry trade.

## What it does

- Pulls **USD/CHF** live from Yahoo Finance using `yfinance`
- Pulls the **6-month U.S. Treasury yield** from the U.S. Treasury daily yield curve page
- Converts the fixed CHF borrowing into **current USD debt**
- Calculates:
  - current equity
  - maintenance margin
  - excess liquidity
  - retained annual carry
  - FX margin-call level
- Projects the strategy forward for 1 to 20 years
- Lets you save local **snapshots** over time so you can track how the hypothetical trade would have performed

## Strategy assumptions in the default setup

- Launch equity: **USD 5,000,000**
- Launch borrowing: **USD 25,000,000 equivalent in CHF**
- Core asset: **rolling 6-month U.S. Treasuries**
- Reinvestment bucket: **6% annual yield**
- Annual outflow: **USD 350,000**
- Core Treasury maintenance margin: **2%**

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Notes

- This is a **strategy monitor**, not a broker-grade margin engine.
- The Treasury and FX data are refreshed when the app reloads, subject to the cache timers in the code.
- Snapshot history is stored locally at `data/snapshots.csv`.
