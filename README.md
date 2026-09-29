# Automated CSV Profiler and Verified Insight Generator

CSCI 5502 — Assignment #2: From CSV to Evidence

## Overview

This project is a reusable Python profiler for single-table CSV files. It examines the structure and quality of a dataset before modeling, calculates descriptive statistics and relationships, creates data-appropriate visualizations, saves a structured analysis summary, and can pass the verified results to a language model for a short narrative explanation.

All statistics are calculated in Python before the language model is used.

`CSV file -> Python analysis -> verified summary -> optional LLM explanation -> generated report`

The analysis is not tied to either included dataset. A different supported CSV can be analyzed by changing the input path and output directory.

## Environment

- Python 3.13.5 was used for development and testing.
- The code uses Python 3.10+ syntax.
- Required packages are listed in `requirements.txt`.

Main libraries:

- pandas
- NumPy
- Matplotlib

The Ollama option uses Python's standard-library HTTP modules, so no Ollama Python package or API key is needed.

## Installation

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks virtual-environment activation, the environment can still be used directly:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Running the profiler

### Dataset A — Palmer Penguins

With local Ollama:

```powershell
python src/profiler.py data/dataset_a_penguins.csv output/dataset_a --llm-provider ollama --model qwen2.5:0.5b
```

Without the LLM component:

```powershell
python src/profiler.py data/dataset_a_penguins.csv output/dataset_a --llm-provider none
```

### Dataset B — Plotly Stocks

With local Ollama:

```powershell
python src/profiler.py data/dataset_b_stocks.csv output/dataset_b --llm-provider ollama --model qwen2.5:0.5b
```

Without the LLM component:

```powershell
python src/profiler.py data/dataset_b_stocks.csv output/dataset_b --llm-provider none
```

For another CSV, use the same command and change the first argument to that CSV file and the second argument to the desired output directory. No dataset-specific analysis code needs to be changed.

The main Python entry point is also available as a function:

```python
from src.profiler import generate_profile

generate_profile(
    csv_path="data/dataset_a_penguins.csv",
    output_dir="output/dataset_a",
    use_llm=True,
    llm_provider="ollama",
    llm_model="qwen2.5:0.5b",
)
```

## LLM configuration

### Local Ollama

Install and start Ollama, then make sure the selected model is available:

```powershell
ollama pull qwen2.5:0.5b
ollama list
```

Run the profiler with `--llm-provider ollama`. If Ollama is unavailable, the profiler still completes the deterministic analysis and records that the narrative step was skipped.

### Disable the LLM

Use `--llm-provider none`. The dataset overview, data-quality checks, descriptive statistics, visualizations, verified insights, and JSON summary are still produced.

### Model used for the included reports

The included report outputs were produced with the program's manual LLM mode using **ChatGPT GPT-5.6 Sol**. The exact prompt and returned narrative are saved in each dataset's output folder as `llm_prompt.txt` and `llm_response.txt`. The calculations referenced in those narratives come from `analysis_summary.json`, not from the model.

The source code also supports local Ollama so the project can be run without a cloud API or stored API credentials.

## Generated output

Each dataset output folder contains:

```text
report.md
column_profile.csv
analysis_summary.json
llm_prompt.txt
llm_response.txt
plots/
```

`report.md` is written by the Python program as part of the profiling run.

## Implemented analysis

The profiler includes:

- dataset filename, row count, column count, and column names;
- inferred technical types and probable column roles;
- non-missing counts, missing percentages, and unique-value counts;
- duplicate-row detection;
- constant-column detection;
- high-missingness flags using a 30% default threshold;
- mixed/inconsistent type heuristics;
- identifier-like and high-cardinality heuristics;
- potentially sensitive-field warnings based on names and simple value patterns;
- categorical frequency counts and percentages, limited to the most frequent values;
- numeric count, missingness, minimum, maximum, mean, median, mode when meaningful, standard deviation, quartiles, IQR, and potential outlier counts;
- potential outliers using the 1.5 x IQR rule;
- Pearson correlations for eligible numeric measures while excluding identifier-like and date-like fields;
- strongest positive and negative numeric relationships when available;
- adaptive visualizations with titles and labeled axes;
- evidence-based deterministic insights plus an optional LLM narrative based on the verified summary.

The profiler flags possible data issues but does not automatically remove rows, missing values, or outliers.

## Dataset sources

### Dataset A — Palmer Penguins

- **Organization / provider:** Palmer Station Long Term Ecological Research (LTER) / palmerpenguins project
- **Dataset:** Palmer Penguins, simplified dataset
- **Source:** https://github.com/allisonhorst/palmerpenguins
- **CSV:** https://github.com/allisonhorst/palmerpenguins/blob/main/inst/extdata/penguins.csv
- **Accessed:** September 28, 2026

### Dataset B — Plotly Stocks

- **Organization / provider:** Plotly
- **Dataset:** `plotly.data.stocks()`
- **Source documentation:** https://plotly.com/python-api-reference/generated/plotly.data.html
- **Accessed:** September 28, 2026

Additional notes are in `data/README.md`.

## Known limitations

1. Type and role inference is heuristic and may be wrong without a schema or data dictionary.
2. The profiler cannot determine undocumented field meanings, measurement units, valid ranges, or sampling design from a CSV alone.
3. Missing-value detection depends on values pandas recognizes as missing. Undocumented sentinel values such as `-999` may require additional configuration.
4. Sensitive-field detection is a warning heuristic and can produce both false positives and false negatives.
5. The 1.5 x IQR rule identifies potential outliers but cannot determine whether an observation is erroneous.
6. Pearson correlation describes linear association and does not establish causation.
7. Automatic plot selection uses general-purpose rules; a domain-specific analysis may require different visualizations.
8. Input files must fit in available memory.

## Security

No API keys, passwords, tokens, or private credentials are stored in the repository. Credentials should not be added to source files or committed to Git.

## Video submission

The recording itself is not stored in the repository. The 5–7 screencast is uploaded to Google Drive, its accessible share URL should be placed in `video/video_link.txt`.
