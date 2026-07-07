# BIPP Streamlit app

Bitcoin Compute Infrastructure Purchasing Power.

This is financial methodology and visualization tooling, not investment advice. BIPP measures how many composite AI GPU-hours one BTC can buy under a selected hardware-price basket. It does not measure direct intelligence output, model quality, or investment return.

The app fetches live Ornn GPU index history and Coinbase BTC/USD candles by default. Synthetic fixture data is available from the sidebar and as a fallback if live fetches fail.

## Live app

https://bipp-appgit-ch4gsg26dv9hmumykniewu.streamlit.app/

## Run locally

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m streamlit run app.py
```

## Features

- Live Ornn plus Coinbase data source.
- Synthetic fixture fallback.
- Basket controls for H100 SXM, H200, and B200 weights.
- Windows for 7D, 30D, 60D, 90D, all available data, and custom ranges.
- BIPP index, compute per BTC, BTC/USD, and hardware-basket panels.
- Processed CSV download.

## Data boundaries

- Do not commit raw Ornn API responses.
- Do not publish derived BIPP values unless Ornn permission scope is preserved.
- Do not describe BIPP as direct intelligence output.
- Label the metric as compute infrastructure purchasing power.

