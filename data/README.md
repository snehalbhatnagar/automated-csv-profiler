# Dataset Sources

The two CSV files are included so the profiler can be run without downloading data during grading. The same analysis code is used for both datasets.

## Dataset A — Palmer Penguins

- **Organization / provider:** Palmer Station Long Term Ecological Research (LTER) / palmerpenguins project
- **Dataset:** Palmer Penguins, simplified dataset
- **Repository file:** `dataset_a_penguins.csv`
- **Size:** 344 rows, 8 columns
- **Reason for selection:** The data include numeric and categorical variables, missing values, and a year field. This makes the dataset useful for testing missingness checks, category summaries, numeric relationships, grouped plots, and an ordered year visualization.
- **Public source:** https://github.com/allisonhorst/palmerpenguins
- **CSV source:** https://github.com/allisonhorst/palmerpenguins/blob/main/inst/extdata/penguins.csv
- **Date accessed:** September 28, 2026
- **Sensitivity:** Public ecological observations; no private or confidential records are used.

## Dataset B — Plotly Stocks

- **Organization / provider:** Plotly
- **Dataset:** `plotly.data.stocks()`
- **Repository file:** `dataset_b_stocks.csv`
- **Size:** 105 rows, 7 columns
- **Reason for selection:** The structure differs from Dataset A. It contains one date-like column and six numeric series, with no categorical columns and no missing values. This tests whether the profiler skips unsupported analyses cleanly while still producing numeric relationship and time-based plots.
- **Public source documentation:** https://plotly.com/python-api-reference/generated/plotly.data.html
- **Date accessed:** September 28, 2026
- **Sensitivity:** Public demonstration market data; no private or confidential records are used.

Dataset A is the simplified `penguins.csv` table from the palmerpenguins project. Dataset B was exported from Plotly's built-in stocks dataset to CSV so both inputs use the same file-based profiling workflow.
