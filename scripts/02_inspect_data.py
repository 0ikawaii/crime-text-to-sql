"""
Inspects each downloaded dataset so you know its REAL structure before we
finalize the Postgres schema. Never design a schema from documentation alone —
always check the actual file. Column names, types, and even granularity often
differ slightly from what's described on a dataset's webpage.
"""

import pandas as pd
from pathlib import Path

RAW_DATA_DIR = Path(__file__).parent.parent / "data" / "raw"

FILES = [
    "crime_district.parquet",
    "hh_income_district.parquet",
    "population_district.parquet",
]


def inspect(filename: str) -> None:
    path = RAW_DATA_DIR / filename
    if not path.exists():
        print(f"!! {filename} not found — run 01_download_data.py first.\n")
        return

    df = pd.read_parquet(path)

    print("=" * 70)
    print(filename)
    print("=" * 70)
    print(f"Shape: {df.shape[0]:,} rows x {df.shape[1]} columns\n")

    print("Columns and dtypes:")
    print(df.dtypes, "\n")

    print("First 5 rows:")
    print(df.head(), "\n")

    # For any column that looks categorical (object dtype, low cardinality),
    # show its unique values — this is exactly the kind of thing you need to
    # know for schema design (e.g. is "category" a fixed small set of values,
    # or free text?).
    for col in df.columns:
        if df[col].dtype == "object" and df[col].nunique() <= 30:
            print(f"Unique values in '{col}' ({df[col].nunique()}):")
            print(sorted(df[col].dropna().unique().tolist()), "\n")

    print()


def main() -> None:
    for filename in FILES:
        inspect(filename)


if __name__ == "__main__":
    main()
