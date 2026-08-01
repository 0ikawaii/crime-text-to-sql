"""
Loads the downloaded Parquet files into the Postgres tables created by
sql/schema.sql.

This is intentionally a plain script, not Airflow — per the build order, we
add orchestration later once we understand the data and the manual process
well enough to know what actually needs automating.
"""
import os
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, MetaData, Table

load_dotenv()

RAW_DATA_DIR = Path(__file__).parent.parent / "data" / "raw"
DB_URL = (
    f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
    f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
)


def load_table(parquet_filename: str, table_name: str, conn, metadata: MetaData) -> None:
    path = RAW_DATA_DIR / parquet_filename
    if not path.exists():
        print(f"!! {parquet_filename} not found — run 01_download_data.py first.")
        return
    df = pd.read_parquet(path)
    print(f"Loading {len(df):,} rows from {parquet_filename} into '{table_name}'...")

    # NOTE: deliberately NOT using df.to_sql() here. Under some environments
    # (this surfaced specifically inside the Airflow container, not the local
    # venv) pandas' Connectable-detection logic for to_sql() misfires with
    # 'AttributeError: ... has no attribute cursor', regardless of whether
    # you pass an Engine or a Connection, and regardless of SQLAlchemy
    # version pinning — it looks like two coexisting SQLAlchemy installs with
    # mismatched import identity inside that specific container image.
    #
    # Reflecting the table (already created by schema.sql, with our chosen
    # types/indexes — same reasoning as before, we don't want pandas guessing
    # types) and bulk-inserting via SQLAlchemy Core sidesteps that detection
    # path entirely and is more explicit about what's happening besides.
    table = Table(table_name, metadata, autoload_with=conn)
    records = df.to_dict(orient="records")
    conn.execute(table.insert(), records)
    print(f"  Done: {table_name} now has this batch loaded.\n")


def main() -> None:
    engine = create_engine(DB_URL)
    metadata = MetaData()
    with engine.begin() as conn:
        load_table("crime_district.parquet", "crime_district", conn, metadata)
        load_table("hh_income_district.parquet", "income_district", conn, metadata)
        load_table("population_district.parquet", "population_district", conn, metadata)
    print("All loads attempted. Verify row counts with psql (see README Step 8).")


if __name__ == "__main__":
    main()