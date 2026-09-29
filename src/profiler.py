from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.error
import urllib.request
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


HIGH_MISSING_THRESHOLD = 0.30
MAX_CATEGORY_VALUES = 10
MAX_PLOTS = 8

SENSITIVE_NAME_PATTERN = re.compile(
    r"(^|_)(name|email|e[-_]?mail|phone|mobile|address|street|ssn|social[_ ]?security|"
    r"dob|birth|race|ethnicity|gender|sex|health|diagnos|income|salary|account|credit|"
    r"license|passport|ip)(_|$)",
    re.IGNORECASE,
)
ID_NAME_PATTERN = re.compile(r"(^|_)(id|identifier|uuid|guid|key|code|index|number|no)(_|$)", re.IGNORECASE)
DATE_NAME_PATTERN = re.compile(r"date|time|timestamp|year|month|day", re.IGNORECASE)
BOOL_VALUES = {"true", "false", "yes", "no", "y", "n", "0", "1", "t", "f"}


def clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if np.isnan(value):
            return None
        if np.isinf(value):
            return str(value)
        return float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def fmt_num(value: Any, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    return f"{v:.{digits}f}".rstrip("0").rstrip(".")


def pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def slugify(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    return text.strip("_") or "dataset"


def _string_series(series: pd.Series) -> pd.Series:
    return series.dropna().astype(str).str.strip()


def coerce_numeric_series(series: pd.Series) -> pd.Series:
    """Coerce ordinary numeric values and common comma-formatted numeric text."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = series.astype("string").str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def _date_parse_ratio(values: pd.Series, column_name: str) -> float:
    if values.empty:
        return 0.0
    # Avoid interpreting arbitrary integers/floats as nanosecond timestamps.
    sample = values.astype(str).head(250)
    looks_dateish = bool(DATE_NAME_PATTERN.search(column_name)) or sample.str.contains(r"[-/:]", regex=True).mean() >= 0.5
    if not looks_dateish:
        return 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(sample, errors="coerce")
    return float(parsed.notna().mean())


def infer_column(series: pd.Series, name: str, n_rows: int) -> dict[str, Any]:
    non_null = series.dropna()
    non_missing = int(non_null.shape[0])
    unique = int(non_null.nunique(dropna=True)) if non_missing else 0
    unique_ratio = unique / non_missing if non_missing else 0.0
    missing_count = int(series.isna().sum())
    missing_pct = missing_count / n_rows if n_rows else 0.0

    technical = "unknown"
    role = "Unknown or mixed type"
    mixed = False
    notes: list[str] = []

    if pd.api.types.is_bool_dtype(series):
        technical = "boolean"
        role = "Boolean field"
    elif pd.api.types.is_datetime64_any_dtype(series):
        technical = "datetime"
        role = "Date-like field"
    elif pd.api.types.is_numeric_dtype(series):
        technical = "integer" if pd.api.types.is_integer_dtype(series) else "numeric"
        if DATE_NAME_PATTERN.search(name):
            # A plausible year column is temporal even if pandas reads it as an integer.
            plausible_year = False
            if non_missing:
                numeric = coerce_numeric_series(non_null)
                plausible_year = numeric.notna().all() and numeric.between(1800, 2200).mean() >= 0.95
            if plausible_year or re.search(r"year", name, re.IGNORECASE):
                role = "Date-like field"
            else:
                role = "Numeric measure"
        elif non_missing and set(pd.to_numeric(non_null, errors="coerce").dropna().unique()).issubset({0, 1}) and unique <= 2:
            role = "Boolean field"
        elif ID_NAME_PATTERN.search(name) and unique_ratio >= 0.70:
            role = "Identifier-like field"
        else:
            role = "Numeric measure"
    else:
        values = _string_series(series)
        if values.empty:
            technical = "unknown"
            role = "Unknown or mixed type"
        else:
            lower = values.str.lower()
            bool_ratio = float(lower.isin(BOOL_VALUES).mean())
            numeric_values = coerce_numeric_series(values)
            numeric_ratio = float(numeric_values.notna().mean())
            date_ratio = _date_parse_ratio(values, name)

            if bool_ratio >= 0.95 and unique <= 4:
                technical = "boolean-like text"
                role = "Boolean field"
            elif date_ratio >= 0.80:
                technical = "date-like text"
                role = "Date-like field"
            elif numeric_ratio >= 0.95:
                technical = "numeric-like text"
                role = "Identifier-like field" if ID_NAME_PATTERN.search(name) and unique_ratio >= 0.70 else "Numeric measure"
            else:
                avg_len = float(values.str.len().mean())
                whitespace_ratio = float(values.str.contains(r"\s", regex=True).mean())
                if ID_NAME_PATTERN.search(name) and unique_ratio >= 0.70:
                    technical = "text"
                    role = "Identifier-like field"
                elif unique_ratio >= 0.95 and avg_len <= 40 and whitespace_ratio < 0.30:
                    technical = "text"
                    role = "Identifier-like field"
                    notes.append("High-cardinality text; identifier-like by heuristic.")
                elif unique <= max(20, int(math.sqrt(max(n_rows, 1)) * 2)) or unique_ratio <= 0.20:
                    technical = "text"
                    role = "Categorical attribute"
                elif avg_len >= 30 or whitespace_ratio >= 0.50:
                    technical = "text"
                    role = "Free-text field"
                else:
                    technical = "text"
                    role = "Categorical attribute" if unique_ratio < 0.50 else "Free-text field"

                if 0.10 < numeric_ratio < 0.90:
                    mixed = True
                    notes.append(f"Approximately {numeric_ratio:.1%} of non-missing values parse as numbers while others do not.")
                if 0.10 < date_ratio < 0.80:
                    mixed = True
                    notes.append(f"Approximately {date_ratio:.1%} of sampled non-missing values parse as dates while others do not.")

    return {
        "column": name,
        "pandas_dtype": str(series.dtype),
        "inferred_technical_type": technical,
        "probable_role": role,
        "non_missing_count": non_missing,
        "missing_count": missing_count,
        "missing_percentage": round(missing_pct * 100, 3),
        "unique_values": unique,
        "unique_ratio": round(unique_ratio, 6),
        "mixed_or_inconsistent": mixed,
        "notes": " ".join(notes),
    }


def detect_sensitive_fields(df: pd.DataFrame) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    ssn_re = re.compile(r"^\d{3}-\d{2}-\d{4}$")
    phone_re = re.compile(r"^(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}$")

    for col in df.columns:
        reasons: list[str] = []
        if SENSITIVE_NAME_PATTERN.search(str(col)):
            reasons.append("column-name heuristic")
        vals = _string_series(df[col]).head(500)
        if not vals.empty:
            if vals.map(lambda x: bool(email_re.match(x))).mean() >= 0.20:
                reasons.append("email-like value pattern")
            if vals.map(lambda x: bool(ssn_re.match(x))).mean() >= 0.20:
                reasons.append("SSN-like value pattern")
            if vals.map(lambda x: bool(phone_re.match(x))).mean() >= 0.50:
                reasons.append("phone-like value pattern")
        if reasons:
            findings.append({"column": str(col), "reason": ", ".join(reasons)})
    return findings


def numeric_statistics(series: pd.Series) -> dict[str, Any]:
    numeric = coerce_numeric_series(series)
    valid = numeric.dropna()
    total = len(series)
    valid_count = int(valid.size)
    missing_count = int(total - valid_count)
    missing_pct = missing_count / total if total else 0.0

    if valid_count == 0:
        return {
            "valid_count": 0,
            "missing_count": missing_count,
            "missing_percentage": round(missing_pct * 100, 3),
        }

    q1 = float(valid.quantile(0.25))
    q3 = float(valid.quantile(0.75))
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    outliers = valid[(valid < lower) | (valid > upper)]
    mode_vals = valid.mode(dropna=True)
    mode = None
    if not mode_vals.empty:
        candidate = mode_vals.iloc[0]
        if int((valid == candidate).sum()) > 1 and valid.nunique() < valid_count * 0.90:
            mode = float(candidate)

    return {
        "valid_count": valid_count,
        "missing_count": missing_count,
        "missing_percentage": round(missing_pct * 100, 3),
        "minimum": float(valid.min()),
        "maximum": float(valid.max()),
        "mean": float(valid.mean()),
        "median": float(valid.median()),
        "mode": mode,
        "standard_deviation": float(valid.std(ddof=1)) if valid_count > 1 else 0.0,
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "outlier_lower_bound": lower,
        "outlier_upper_bound": upper,
        "outlier_count": int(outliers.size),
        "outlier_percentage": round((outliers.size / valid_count) * 100, 3) if valid_count else 0.0,
    }


def categorical_statistics(series: pd.Series) -> dict[str, Any]:
    non_null = series.dropna()
    total = len(series)
    if non_null.empty:
        return {
            "unique_categories": 0,
            "most_frequent": [],
            "top_values": [],
        }
    counts = non_null.astype(str).value_counts(dropna=False)
    top_count = int(counts.iloc[0])
    modes = [str(x) for x in counts[counts == top_count].index.tolist()[:5]]
    top = []
    for value, count in counts.head(MAX_CATEGORY_VALUES).items():
        top.append({
            "value": str(value),
            "count": int(count),
            "percentage_of_non_missing": round((int(count) / len(non_null)) * 100, 3),
            "percentage_of_all_rows": round((int(count) / total) * 100, 3) if total else 0.0,
        })
    return {
        "unique_categories": int(non_null.nunique()),
        "most_frequent": modes,
        "top_frequency": top_count,
        "top_values": top,
    }


def analyze_relationships(df: pd.DataFrame, column_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    roles = {p["column"]: p["probable_role"] for p in column_profiles}
    numeric_cols = []
    numeric_frame = pd.DataFrame(index=df.index)
    for c in df.columns:
        if roles.get(c) != "Numeric measure":
            continue
        coerced = coerce_numeric_series(df[c])
        if coerced.nunique(dropna=True) > 1:
            numeric_cols.append(c)
            numeric_frame[c] = coerced
    result: dict[str, Any] = {
        "included_numeric_columns": numeric_cols,
        "correlation_matrix": None,
        "strongest_positive": None,
        "strongest_negative": None,
        "strongest_absolute": None,
        "skipped_reason": None,
    }
    if len(numeric_cols) < 2:
        result["skipped_reason"] = "Fewer than two suitable numeric-measure columns were available after excluding identifier/date-like fields."
        return result

    corr = numeric_frame[numeric_cols].corr(numeric_only=True)
    result["correlation_matrix"] = {
        row: {col: clean_scalar(val) for col, val in corr.loc[row].items()}
        for row in corr.index
    }

    pairs: list[tuple[str, str, float]] = []
    for i, a in enumerate(numeric_cols):
        for b in numeric_cols[i + 1:]:
            val = corr.loc[a, b]
            if pd.notna(val):
                pairs.append((a, b, float(val)))
    if not pairs:
        result["skipped_reason"] = "Correlation coefficients could not be computed from the available numeric data."
        return result

    positives = [p for p in pairs if p[2] > 0]
    negatives = [p for p in pairs if p[2] < 0]
    if positives:
        p = max(positives, key=lambda x: x[2])
        result["strongest_positive"] = {"column_1": p[0], "column_2": p[1], "correlation": p[2]}
    if negatives:
        p = min(negatives, key=lambda x: x[2])
        result["strongest_negative"] = {"column_1": p[0], "column_2": p[1], "correlation": p[2]}
    p = max(pairs, key=lambda x: abs(x[2]))
    result["strongest_absolute"] = {"column_1": p[0], "column_2": p[1], "correlation": p[2]}
    return result


def build_summary(df: pd.DataFrame, csv_path: Path, high_missing_threshold: float) -> dict[str, Any]:
    n_rows, n_cols = df.shape
    profiles = [infer_column(df[c], str(c), n_rows) for c in df.columns]
    profile_map = {p["column"]: p for p in profiles}

    duplicate_rows = int(df.duplicated().sum())
    constant_cols = [str(c) for c in df.columns if df[c].dropna().nunique() <= 1]
    high_missing_cols = [
        {
            "column": str(c),
            "missing_count": int(df[c].isna().sum()),
            "missing_percentage": round(float(df[c].isna().mean() * 100), 3),
        }
        for c in df.columns if float(df[c].isna().mean()) >= high_missing_threshold
    ]
    mixed_cols = [p["column"] for p in profiles if p["mixed_or_inconsistent"]]
    id_or_high_card = [
        {
            "column": p["column"],
            "role": p["probable_role"],
            "unique_values": p["unique_values"],
            "unique_ratio": p["unique_ratio"],
        }
        for p in profiles
        if p["probable_role"] == "Identifier-like field" or (
            p["probable_role"] in {"Categorical attribute", "Free-text field", "Unknown or mixed type"}
            and p["unique_ratio"] >= 0.90
            and p["unique_values"] >= max(20, int(n_rows * 0.5))
        )
    ]
    sensitive = detect_sensitive_fields(df)

    numeric_stats: dict[str, Any] = {}
    categorical_stats: dict[str, Any] = {}
    for c in df.columns:
        role = profile_map[str(c)]["probable_role"]
        if role == "Numeric measure":
            numeric_stats[str(c)] = numeric_statistics(df[c])
        elif role in {"Categorical attribute", "Boolean field"}:
            categorical_stats[str(c)] = categorical_statistics(df[c])

    relationships = analyze_relationships(df, profiles)

    return {
        "dataset": {
            "filename": csv_path.name,
            "rows": int(n_rows),
            "columns": int(n_cols),
            "column_names": [str(c) for c in df.columns],
        },
        "configuration": {
            "high_missing_threshold_percentage": high_missing_threshold * 100,
            "outlier_rule": "1.5 x IQR",
            "max_category_values_shown": MAX_CATEGORY_VALUES,
            "max_plots": MAX_PLOTS,
        },
        "column_profiles": profiles,
        "data_quality": {
            "duplicate_rows": duplicate_rows,
            "duplicate_percentage": round((duplicate_rows / n_rows) * 100, 3) if n_rows else 0.0,
            "constant_columns": constant_cols,
            "high_missing_columns": high_missing_cols,
            "mixed_or_inconsistent_type_columns": mixed_cols,
            "identifier_or_high_cardinality_columns": id_or_high_card,
            "potentially_sensitive_fields": sensitive,
            "sensitive_field_disclaimer": "Sensitive-field detection is heuristic. No warning does not prove that a dataset is safe, and a warning can be a false positive without semantic context.",
        },
        "numeric_statistics": numeric_stats,
        "categorical_statistics": categorical_stats,
        "relationships": relationships,
    }


def save_plot(fig: plt.Figure, plots_dir: Path, number: int, label: str) -> str:
    filename = f"plot_{number:02d}_{slugify(label).lower()}.png"
    path = plots_dir / filename
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return filename


def create_visualizations(
    df: pd.DataFrame,
    summary: dict[str, Any],
    plots_dir: Path,
    max_plots: int = MAX_PLOTS,
) -> tuple[list[dict[str, str]], list[str]]:
    plots_dir.mkdir(parents=True, exist_ok=True)
    for old in plots_dir.glob("plot_*.png"):
        old.unlink()

    plots: list[dict[str, str]] = []
    skipped: list[str] = []
    profile_map = {p["column"]: p for p in summary["column_profiles"]}
    numeric_cols = [c for c in df.columns if profile_map[str(c)]["probable_role"] == "Numeric measure"]
    categorical_cols = [c for c in df.columns if profile_map[str(c)]["probable_role"] in {"Categorical attribute", "Boolean field"}]
    date_cols = [c for c in df.columns if profile_map[str(c)]["probable_role"] == "Date-like field"]

    def can_add() -> bool:
        return len(plots) < max_plots

    # 1. Missing values
    missing = df.isna().sum()
    missing = missing[missing > 0].sort_values(ascending=False)
    if can_add() and not missing.empty:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.bar(missing.index.astype(str), missing.values)
        ax.set_title("Missing Values by Column")
        ax.set_xlabel("Column")
        ax.set_ylabel("Missing row count")
        ax.tick_params(axis="x", rotation=45)
        fn = save_plot(fig, plots_dir, len(plots) + 1, "missing_values")
        plots.append({"file": fn, "title": "Missing Values by Column", "reason": "Shows where incomplete observations are concentrated."})
    else:
        skipped.append("Missing-value bar chart skipped because no columns contain missing values.")

    # Pick informative numeric column: most non-missing variance after scaling by IQR/median is not robust across units,
    # so prefer the first numeric measure in column order for deterministic behavior.
    numeric_primary = numeric_cols[0] if numeric_cols else None

    # 2. Histogram
    if can_add() and numeric_primary is not None:
        vals = coerce_numeric_series(df[numeric_primary]).dropna()
        if vals.nunique() > 1:
            fig, ax = plt.subplots(figsize=(7, 4.5))
            ax.hist(vals, bins=min(20, max(5, int(math.sqrt(len(vals))))))
            ax.set_title(f"Distribution of {numeric_primary}")
            ax.set_xlabel(str(numeric_primary))
            ax.set_ylabel("Frequency")
            fn = save_plot(fig, plots_dir, len(plots) + 1, f"histogram_{numeric_primary}")
            plots.append({"file": fn, "title": f"Distribution of {numeric_primary}", "reason": "A histogram is appropriate for showing the shape and spread of a numeric measure."})
        else:
            skipped.append(f"Histogram for {numeric_primary} skipped because the column is constant.")
    elif numeric_primary is None:
        skipped.append("Histogram skipped because no numeric-measure column was available.")

    # 3. Boxplot
    if can_add() and numeric_primary is not None:
        vals = coerce_numeric_series(df[numeric_primary]).dropna()
        if not vals.empty:
            fig, ax = plt.subplots(figsize=(7, 3.8))
            ax.boxplot(vals, vert=False)
            ax.set_title(f"Boxplot of {numeric_primary}")
            ax.set_xlabel(str(numeric_primary))
            ax.set_yticks([])
            fn = save_plot(fig, plots_dir, len(plots) + 1, f"boxplot_{numeric_primary}")
            plots.append({"file": fn, "title": f"Boxplot of {numeric_primary}", "reason": "A boxplot summarizes median, quartiles, and potential 1.5×IQR outliers."})

    # 4. Categorical frequency
    cat_primary = categorical_cols[0] if categorical_cols else None
    if can_add() and cat_primary is not None:
        counts = df[cat_primary].astype("string").fillna("<missing>").value_counts().head(MAX_CATEGORY_VALUES)
        if not counts.empty:
            fig, ax = plt.subplots(figsize=(8, 4.5))
            ax.bar(counts.index.astype(str), counts.values)
            ax.set_title(f"Top Categories in {cat_primary}")
            ax.set_xlabel(str(cat_primary))
            ax.set_ylabel("Count")
            ax.tick_params(axis="x", rotation=35)
            fn = save_plot(fig, plots_dir, len(plots) + 1, f"categories_{cat_primary}")
            plots.append({"file": fn, "title": f"Top Categories in {cat_primary}", "reason": "A bar chart directly compares frequencies for a categorical attribute."})
    elif cat_primary is None:
        skipped.append("Categorical frequency chart skipped because no categorical or Boolean field was available.")

    # 5. Correlation heatmap (matplotlib only)
    rel = summary["relationships"]
    corr_dict = rel.get("correlation_matrix")
    if can_add() and corr_dict:
        corr = pd.DataFrame(corr_dict).T
        fig, ax = plt.subplots(figsize=(7, 6))
        im = ax.imshow(corr.values.astype(float), vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(len(corr.columns)), corr.columns, rotation=45, ha="right")
        ax.set_yticks(range(len(corr.index)), corr.index)
        ax.set_title("Correlation Matrix of Numeric Measures")
        fig.colorbar(im, ax=ax, label="Pearson correlation")
        for i in range(len(corr.index)):
            for j in range(len(corr.columns)):
                ax.text(j, i, f"{corr.iloc[i, j]:.2f}", ha="center", va="center", fontsize=8)
        fn = save_plot(fig, plots_dir, len(plots) + 1, "correlation_heatmap")
        plots.append({"file": fn, "title": "Correlation Matrix of Numeric Measures", "reason": "A heatmap summarizes pairwise linear relationships among suitable numeric measures; correlation is not causation."})
    else:
        skipped.append(rel.get("skipped_reason") or "Correlation heatmap skipped because relationship analysis was unavailable.")

    # 6. Scatterplot for strongest absolute correlation
    strongest = rel.get("strongest_absolute")
    if can_add() and strongest:
        a, b = strongest["column_1"], strongest["column_2"]
        pair = pd.DataFrame({a: coerce_numeric_series(df[a]), b: coerce_numeric_series(df[b])}).dropna()
        if not pair.empty:
            fig, ax = plt.subplots(figsize=(6.5, 5))
            ax.scatter(pair[a], pair[b], alpha=0.65)
            ax.set_title(f"{b} vs {a}")
            ax.set_xlabel(a)
            ax.set_ylabel(b)
            fn = save_plot(fig, plots_dir, len(plots) + 1, f"scatter_{a}_{b}")
            plots.append({"file": fn, "title": f"{b} vs {a}", "reason": f"This pair has the largest absolute Pearson correlation among eligible numeric measures (r={strongest['correlation']:.3f})."})

    # 7. Numeric distribution by a manageable category
    manageable_cat = None
    for c in categorical_cols:
        u = df[c].nunique(dropna=True)
        if 2 <= u <= 8:
            manageable_cat = c
            break
    if can_add() and numeric_primary is not None and manageable_cat is not None:
        groups = []
        labels = []
        for label, group in df[[manageable_cat, numeric_primary]].dropna().groupby(manageable_cat, observed=False):
            vals = coerce_numeric_series(group[numeric_primary]).dropna().values
            if len(vals):
                groups.append(vals)
                labels.append(str(label))
        if len(groups) >= 2:
            fig, ax = plt.subplots(figsize=(8, 4.8))
            ax.boxplot(groups, tick_labels=labels)
            ax.set_title(f"{numeric_primary} by {manageable_cat}")
            ax.set_xlabel(str(manageable_cat))
            ax.set_ylabel(str(numeric_primary))
            ax.tick_params(axis="x", rotation=25)
            fn = save_plot(fig, plots_dir, len(plots) + 1, f"{numeric_primary}_by_{manageable_cat}")
            plots.append({"file": fn, "title": f"{numeric_primary} by {manageable_cat}", "reason": "Grouped boxplots compare a numeric distribution across a small number of categories."})
    elif manageable_cat is None:
        skipped.append("Numeric-by-category plot skipped because no categorical field with 2–8 levels was available.")

    # 8. Time/year trend if a temporal field exists.
    if can_add() and date_cols and numeric_primary is not None:
        tcol = date_cols[0]
        temp = df[[tcol, numeric_primary]].copy()
        if pd.api.types.is_numeric_dtype(temp[tcol]):
            temp["__time"] = pd.to_numeric(temp[tcol], errors="coerce")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                temp["__time"] = pd.to_datetime(temp[tcol], errors="coerce")
        temp["__value"] = coerce_numeric_series(temp[numeric_primary])
        temp = temp.dropna(subset=["__time", "__value"])
        if not temp.empty and temp["__time"].nunique() >= 2:
            unique_time_per_row = temp["__time"].nunique() == len(temp)
            if unique_time_per_row:
                grouped = temp[["__time", "__value"]].sort_values("__time")
                title = f"{numeric_primary} over {tcol}"
                ylabel = str(numeric_primary)
                reason = "A line plot is appropriate for showing a numeric measure across an ordered date field."
            else:
                grouped = temp.groupby("__time", as_index=False)["__value"].mean().sort_values("__time")
                title = f"Mean {numeric_primary} by {tcol}"
                ylabel = f"Mean {numeric_primary}"
                reason = "A line plot is appropriate for showing how an aggregated numeric measure changes across an ordered date/year field."
            fig, ax = plt.subplots(figsize=(8, 4.5))
            ax.plot(grouped["__time"], grouped["__value"], marker="o")
            ax.set_title(title)
            ax.set_xlabel(str(tcol))
            ax.set_ylabel(ylabel)
            fn = save_plot(fig, plots_dir, len(plots) + 1, f"time_{numeric_primary}_by_{tcol}")
            plots.append({"file": fn, "title": title, "reason": reason})
    elif not date_cols:
        skipped.append("Time-series plot skipped because no date-like field was inferred.")

    if len(plots) < 5:
        skipped.append(f"Only {len(plots)} meaningful plots were generated; additional plots were not created solely to reach five.")
    return plots, skipped


def build_deterministic_insights(summary: dict[str, Any]) -> list[str]:
    insights: list[str] = []
    ds = summary["dataset"]
    dq = summary["data_quality"]
    nums = summary["numeric_statistics"]
    cats = summary["categorical_statistics"]
    rel = summary["relationships"]

    # 1) Data quality
    high_missing = dq["high_missing_columns"]
    all_missing = sorted(
        [p for p in summary["column_profiles"] if p["missing_count"] > 0],
        key=lambda x: x["missing_percentage"],
        reverse=True,
    )
    if high_missing:
        x = high_missing[0]
        insights.append(
            f"Data quality: `{x['column']}` is missing in {x['missing_count']:,} of {ds['rows']:,} rows ({x['missing_percentage']:.1f}%), meeting the configured high-missingness threshold of {summary['configuration']['high_missing_threshold_percentage']:.0f}%."
        )
    elif all_missing:
        x = all_missing[0]
        insights.append(
            f"Data quality: the largest missingness occurs in `{x['column']}` with {x['missing_count']:,} missing rows ({x['missing_percentage']:.1f}%); no column reaches the {summary['configuration']['high_missing_threshold_percentage']:.0f}% high-missingness threshold."
        )
    else:
        insights.append(
            f"Data quality: all {ds['rows']:,} rows are complete across the {ds['columns']} columns with respect to pandas-recognized missing values; this does not rule out undocumented sentinel codes."
        )

    # 2) Distribution
    if nums:
        col = next(iter(nums))
        s = nums[col]
        insights.append(
            f"Distribution: `{col}` has median {fmt_num(s.get('median'))}, Q1 {fmt_num(s.get('q1'))}, Q3 {fmt_num(s.get('q3'))}, and ranges from {fmt_num(s.get('minimum'))} to {fmt_num(s.get('maximum'))}. The 1.5×IQR rule flags {s.get('outlier_count', 0):,} values ({s.get('outlier_percentage', 0):.1f}% of valid observations) as potential outliers."
        )
    else:
        insights.append("Distribution: no numeric-measure column was inferred, so numeric distribution statistics were skipped rather than invented.")

    # 3) Categorical
    if cats:
        col = next(iter(cats))
        s = cats[col]
        if s["top_values"]:
            top = s["top_values"][0]
            insights.append(
                f"Categorical pattern: `{col}` has {s['unique_categories']:,} observed categories; `{top['value']}` is the most frequent with {top['count']:,} rows ({top['percentage_of_all_rows']:.1f}% of all rows)."
            )
    else:
        insights.append("Categorical pattern: no categorical or Boolean field was inferred, so category-frequency analysis was skipped.")

    # 4) Relationship
    strongest = rel.get("strongest_absolute")
    if strongest:
        insights.append(
            f"Relationship: `{strongest['column_1']}` and `{strongest['column_2']}` have the largest absolute Pearson correlation among eligible numeric measures (r={strongest['correlation']:.3f}). This describes linear association only and does not establish causation."
        )
    else:
        insights.append(f"Relationship: numeric relationship analysis was skipped because {rel.get('skipped_reason', 'the dataset did not support it')}")

    # 5) Coverage / limitation grounded in the file itself
    insights.append(
        f"Limitation: this automated profile describes the {ds['rows']:,} rows and {ds['columns']} columns present in `{ds['filename']}` only. Without an external schema or data dictionary, field meaning, units, valid ranges, sampling design, and representativeness cannot be verified from the CSV alone."
    )

    # 6) Further question
    if strongest and cats:
        insights.append(
            f"Further investigation: examine whether the association between `{strongest['column_1']}` and `{strongest['column_2']}` remains similar within major categories rather than relying only on the overall correlation."
        )
    elif strongest and not cats:
        insights.append(
            f"Further investigation: examine whether the association between `{strongest['column_1']}` and `{strongest['column_2']}` is stable across ordered portions of the dataset, such as different time windows when an ordered date field is available."
        )
    elif nums and cats:
        ncol = next(iter(nums))
        ccol = next(iter(cats))
        insights.append(f"Further investigation: compare the distribution of `{ncol}` across levels of `{ccol}` and check whether missingness changes the comparison.")
    elif nums:
        ncol = next(iter(nums))
        insights.append(f"Further investigation: inspect the rows flagged as potential outliers in `{ncol}` and determine from domain documentation whether they are errors, rare valid observations, or expected extremes.")
    else:
        insights.append("Further investigation: obtain a data dictionary or domain documentation before drawing semantic conclusions from the profiled fields.")

    # 7) Duplicate / constant warning when notable, otherwise sensitive heuristic note.
    if dq["duplicate_rows"] > 0:
        insights.append(
            f"Additional quality finding: {dq['duplicate_rows']:,} duplicate row{'s' if dq['duplicate_rows'] != 1 else ''} was detected ({dq['duplicate_percentage']:.1f}% of rows). They are flagged only; the program does not delete them automatically."
        )
    elif dq["constant_columns"]:
        insights.append(f"Additional quality finding: constant columns were detected: {', '.join('`'+c+'`' for c in dq['constant_columns'])}. Constant fields add no variation for relationship analysis.")
    else:
        insights.append("Additional quality finding: no duplicate rows or constant columns were detected by the automated checks.")

    return insights[:8]


def create_llm_prompt(summary: dict[str, Any], deterministic_insights: list[str]) -> str:
    compact = {
        "dataset": summary["dataset"],
        "data_quality": summary["data_quality"],
        "numeric_statistics": summary["numeric_statistics"],
        "categorical_statistics": summary["categorical_statistics"],
        "relationships": summary["relationships"],
        "verified_insights": deterministic_insights,
    }
    return (
        "You are writing a concise evidence-based narrative for an automated CSV profiling report.\n"
        "All calculations have already been completed by Python. You MUST use only the verified evidence below.\n"
        "Do not calculate or invent statistics, units, field meanings, causes, or sampling claims.\n"
        "Do not claim correlation is causation. If a field meaning is unclear, say so.\n"
        "Return 5-8 concise bullet points. Include at least one data-quality point, one distribution point, "
        "one categorical point when available, one relationship point when available, one limitation/bias warning, "
        "and one follow-up question. Quantitative statements must reuse values already present below.\n\n"
        "VERIFIED PYTHON EVIDENCE:\n"
        + json.dumps(compact, indent=2, ensure_ascii=False)
    )


def call_ollama(prompt: str, model: str, base_url: str = "http://localhost:11434") -> str:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    response = str(body.get("response", "")).strip()
    if not response:
        raise RuntimeError("Ollama returned an empty response.")
    return response


def run_llm(
    prompt: str,
    provider: str,
    model: str,
    manual_response_file: Path | None = None,
) -> tuple[str, str]:
    provider = provider.lower()
    if provider == "none":
        return "skipped", "AI-generated narrative insights were skipped because the LLM component was disabled."
    if provider == "manual":
        if manual_response_file is None or not manual_response_file.exists():
            return "unavailable", "AI-generated narrative insights were skipped because manual LLM mode requires an existing response file."
        return "ok", manual_response_file.read_text(encoding="utf-8").strip()
    if provider == "ollama":
        try:
            return "ok", call_ollama(prompt, model)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, OSError) as exc:
            return "unavailable", f"AI-generated narrative insights were skipped because Ollama was unavailable or failed: {type(exc).__name__}: {exc}"
    raise ValueError(f"Unsupported LLM provider: {provider}")


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def esc(x: Any) -> str:
        s = "" if x is None else str(x)
        return s.replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(map(esc, headers)) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    out.extend("| " + " | ".join(esc(x) for x in row) + " |" for row in rows)
    return "\n".join(out)


def build_report(
    summary: dict[str, Any],
    plots: list[dict[str, str]],
    skipped_plots: list[str],
    deterministic_insights: list[str],
    llm_status: str,
    llm_response: str,
    llm_provider: str,
    llm_model: str,
) -> str:
    ds = summary["dataset"]
    dq = summary["data_quality"]
    lines: list[str] = []
    lines.append(f"# Automated CSV Profile: {ds['filename']}")
    lines.append("")
    lines.append("## 1. Dataset Overview")
    lines.append("")
    lines.append(f"- **Filename:** `{ds['filename']}`")
    lines.append(f"- **Rows:** {ds['rows']:,}")
    lines.append(f"- **Columns:** {ds['columns']:,}")
    lines.append(f"- **Column names:** {', '.join('`'+c+'`' for c in ds['column_names'])}")
    lines.append("")
    profile_rows = []
    for p in summary["column_profiles"]:
        profile_rows.append([
            p["column"], p["pandas_dtype"], p["inferred_technical_type"], p["probable_role"],
            f"{p['non_missing_count']:,}", f"{p['missing_percentage']:.1f}%", f"{p['unique_values']:,}",
        ])
    lines.append(markdown_table(
        ["Column", "Pandas dtype", "Inferred technical type", "Probable role", "Non-missing", "Missing %", "Unique"],
        profile_rows,
    ))

    lines.append("\n## 2. Data Quality Profile\n")
    lines.append(f"- **Duplicate rows:** {dq['duplicate_rows']:,} ({dq['duplicate_percentage']:.1f}%)")
    lines.append(f"- **Constant columns:** {', '.join('`'+c+'`' for c in dq['constant_columns']) if dq['constant_columns'] else 'None detected'}")
    lines.append(f"- **High-missingness threshold:** {summary['configuration']['high_missing_threshold_percentage']:.0f}%")
    if dq["high_missing_columns"]:
        lines.append("- **Columns at/above the threshold:** " + "; ".join(
            f"`{x['column']}` = {x['missing_count']:,} missing ({x['missing_percentage']:.1f}%)" for x in dq["high_missing_columns"]
        ))
    else:
        lines.append("- **Columns at/above the threshold:** None")
    lines.append(f"- **Mixed/inconsistent type columns:** {', '.join('`'+c+'`' for c in dq['mixed_or_inconsistent_type_columns']) if dq['mixed_or_inconsistent_type_columns'] else 'None detected'}")
    if dq["identifier_or_high_cardinality_columns"]:
        lines.append("- **Identifier-like/high-cardinality fields:** " + ", ".join(f"`{x['column']}`" for x in dq["identifier_or_high_cardinality_columns"]))
    else:
        lines.append("- **Identifier-like/high-cardinality fields:** None detected")
    if dq["potentially_sensitive_fields"]:
        lines.append("- **Potentially sensitive fields (heuristic):** " + "; ".join(f"`{x['column']}` ({x['reason']})" for x in dq["potentially_sensitive_fields"]))
    else:
        lines.append("- **Potentially sensitive fields (heuristic):** None flagged")
    lines.append(f"- **Important:** {dq['sensitive_field_disclaimer']}")
    lines.append("- The program **does not automatically delete** rows, missing values, or outliers; it profiles and flags them.")

    lines.append("\n## 3. Descriptive Statistics\n")
    if summary["numeric_statistics"]:
        lines.append("### Numeric columns\n")
        num_rows = []
        for c, s in summary["numeric_statistics"].items():
            num_rows.append([
                c, s.get("valid_count"), f"{s.get('missing_percentage', 0):.1f}%", fmt_num(s.get("minimum")),
                fmt_num(s.get("maximum")), fmt_num(s.get("mean")), fmt_num(s.get("median")), fmt_num(s.get("mode")),
                fmt_num(s.get("standard_deviation")), fmt_num(s.get("q1")), fmt_num(s.get("q3")), fmt_num(s.get("iqr")),
                f"{s.get('outlier_count', 0)} ({s.get('outlier_percentage', 0):.1f}%)",
            ])
        lines.append(markdown_table(
            ["Column", "Valid", "Missing %", "Min", "Max", "Mean", "Median", "Mode", "Std dev", "Q1", "Q3", "IQR", "1.5×IQR outliers"],
            num_rows,
        ))
    else:
        lines.append("No numeric-measure columns were inferred, so numeric descriptive statistics were skipped.")

    lines.append("\n### Categorical columns\n")
    if summary["categorical_statistics"]:
        for c, s in summary["categorical_statistics"].items():
            lines.append(f"#### `{c}`")
            lines.append(f"- Unique categories: {s['unique_categories']:,}")
            lines.append(f"- Most frequent value(s): {', '.join('`'+x+'`' for x in s['most_frequent']) if s['most_frequent'] else 'N/A'}")
            if s["top_values"]:
                rows = [[x["value"], x["count"], f"{x['percentage_of_all_rows']:.1f}%"] for x in s["top_values"]]
                lines.append(markdown_table(["Value", "Count", "% of all rows"], rows))
            lines.append("")
    else:
        lines.append("No categorical/Boolean columns were inferred, so categorical frequency analysis was skipped.")

    lines.append("\n## 4. Relationships Between Variables\n")
    rel = summary["relationships"]
    if rel["correlation_matrix"]:
        if rel["strongest_positive"]:
            x = rel["strongest_positive"]
            lines.append(f"- Strongest positive correlation: `{x['column_1']}` vs `{x['column_2']}` = **{x['correlation']:.3f}**")
        else:
            lines.append("- No positive off-diagonal correlation was present among eligible numeric measures.")
        if rel["strongest_negative"]:
            x = rel["strongest_negative"]
            lines.append(f"- Strongest negative correlation: `{x['column_1']}` vs `{x['column_2']}` = **{x['correlation']:.3f}**")
        else:
            lines.append("- No negative off-diagonal correlation was present among eligible numeric measures.")
        lines.append("- **Interpretation caution:** Pearson correlation measures linear association and must not be interpreted as causation.")
    else:
        lines.append(f"Relationship analysis skipped: {rel['skipped_reason']}")

    lines.append("\n## 5. Adaptive Visualizations\n")
    if plots:
        for p in plots:
            lines.append(f"### {p['title']}")
            lines.append(f"**Why this plot is appropriate:** {p['reason']}")
            lines.append(f"\n![{p['title']}](plots/{p['file']})\n")
    if skipped_plots:
        lines.append("### Skipped plot types")
        for reason in skipped_plots:
            lines.append(f"- {reason}")

    lines.append("\n## 6. Verified Evidence-Based Insights\n")
    lines.append("The following statements are generated deterministically from Python-calculated results, not calculated by the language model.\n")
    for i, insight in enumerate(deterministic_insights, 1):
        lines.append(f"{i}. {insight}")

    lines.append("\n## 7. AI-Assisted Narrative\n")
    lines.append(f"- **LLM provider:** `{llm_provider}`")
    lines.append(f"- **Model/configuration:** `{llm_model if llm_provider != 'none' else 'disabled'}`")
    lines.append(f"- **Status:** `{llm_status}`")
    lines.append("")
    lines.append(llm_response.strip() or "No LLM response was produced.")
    if llm_status == "ok":
        lines.append("\nThe LLM received a structured summary produced by Python. The prompt explicitly forbids inventing statistics, meanings, units, causes, or causal claims. The deterministic insights above remain the traceable source of quantitative evidence.")
    else:
        lines.append("\nThe Python-generated prompt is still saved in `llm_prompt.txt`; deterministic profiling and verified insights remain available even when the LLM is disabled or unavailable.")

    lines.append("\n## 8. Automated-Analysis Limitations\n")
    lines.append("- Type and role inference is heuristic and can be wrong without a schema or data dictionary.")
    lines.append("- Missing-value detection recognizes values pandas parses as missing; undocumented sentinel codes may be missed.")
    lines.append("- The 1.5×IQR rule flags potential outliers but does not determine whether they are errors.")
    lines.append("- Sensitive-field detection can produce false positives or false negatives because it uses names and simple value patterns.")
    lines.append("- Correlation captures linear association only and does not establish causation.")
    lines.append("- This report does not establish data representativeness, fairness, or suitability for a downstream model.")

    lines.append("\n---\n")
    lines.append("Generated automatically by `src/profiler.py`.")
    return "\n".join(lines) + "\n"


def generate_profile(
    csv_path: str | Path,
    output_dir: str | Path,
    use_llm: bool = True,
    llm_provider: str = "ollama",
    llm_model: str = "qwen2.5:0.5b",
    high_missing_threshold: float = HIGH_MISSING_THRESHOLD,
    max_plots: int = MAX_PLOTS,
    manual_response_file: str | Path | None = None,
) -> dict[str, Any]:
    csv_path = Path(csv_path)
    output_dir = Path(output_dir)
    plots_dir = output_dir / "plots"
    output_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    if csv_path.suffix.lower() != ".csv":
        raise ValueError("Only CSV input is supported.")

    try:
        df = pd.read_csv(csv_path, low_memory=False)
    except UnicodeDecodeError:
        df = pd.read_csv(csv_path, encoding="latin-1", low_memory=False)
    if df.columns.empty:
        raise ValueError("CSV has no columns/header row.")

    summary = build_summary(df, csv_path, high_missing_threshold)
    plots, skipped_plots = create_visualizations(df, summary, plots_dir, max_plots=max_plots)
    summary["visualizations"] = plots
    summary["skipped_visualizations"] = skipped_plots

    deterministic_insights = build_deterministic_insights(summary)
    summary["verified_insights"] = deterministic_insights

    prompt = create_llm_prompt(summary, deterministic_insights)
    (output_dir / "llm_prompt.txt").write_text(prompt, encoding="utf-8")

    provider = llm_provider if use_llm else "none"
    manual_path = Path(manual_response_file) if manual_response_file else None
    llm_status, llm_response = run_llm(prompt, provider, llm_model, manual_path)
    (output_dir / "llm_response.txt").write_text(llm_response + "\n", encoding="utf-8")
    summary["llm"] = {"provider": provider, "model": llm_model, "status": llm_status}

    # Save profiles and structured summary before rendering report.
    pd.DataFrame(summary["column_profiles"]).to_csv(output_dir / "column_profile.csv", index=False)
    with (output_dir / "analysis_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=clean_scalar)

    report = build_report(summary, plots, skipped_plots, deterministic_insights, llm_status, llm_response, provider, llm_model)
    (output_dir / "report.md").write_text(report, encoding="utf-8")

    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reusable automated CSV profiler with optional evidence-grounded LLM narrative.")
    parser.add_argument("csv_path", help="Path to the CSV file to analyze")
    parser.add_argument("output_dir", help="Directory where report and artifacts will be written")
    parser.add_argument("--llm-provider", choices=["ollama", "manual", "none"], default="ollama", help="LLM integration mode")
    parser.add_argument("--model", default="qwen2.5:0.5b", help="Ollama model name or documented manual model label")
    parser.add_argument("--manual-response-file", default=None, help="Text file containing an LLM response for manual mode")
    parser.add_argument("--high-missing-threshold", type=float, default=HIGH_MISSING_THRESHOLD, help="Fraction at/above which a column is flagged for high missingness (default 0.30)")
    parser.add_argument("--max-plots", type=int, default=MAX_PLOTS, help="Maximum number of plots to generate (default 8)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 0 <= args.high_missing_threshold <= 1:
        raise ValueError("--high-missing-threshold must be between 0 and 1.")
    if args.max_plots < 1:
        raise ValueError("--max-plots must be at least 1.")
    summary = generate_profile(
        csv_path=args.csv_path,
        output_dir=args.output_dir,
        use_llm=args.llm_provider != "none",
        llm_provider=args.llm_provider,
        llm_model=args.model,
        high_missing_threshold=args.high_missing_threshold,
        max_plots=args.max_plots,
        manual_response_file=args.manual_response_file,
    )
    print(f"Profile generated for {summary['dataset']['filename']}: {summary['dataset']['rows']} rows x {summary['dataset']['columns']} columns")
    print(f"Output: {Path(args.output_dir).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
