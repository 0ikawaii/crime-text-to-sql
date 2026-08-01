"""
Step 7 — RAG layer seeding.

Populates few_shot_examples and schema_glossary with hand-curated entries,
embedding each one locally with sentence-transformers (all-MiniLM-L6-v2,
384-dim, free, no API cost). Run once to seed; re-run any time you add
more validated examples or glossary terms (it clears and re-inserts, so
this is idempotent).

Run with:
    python scripts/05_seed_rag.py
"""

import os

from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# ---------------------------------------------------------------------------
# Few-shot examples: validated (question -> SQL) pairs. These are the exact
# hand-written queries already verified by hand earlier in this project —
# that's not a coincidence, it's the point: manual SQL fluency feeds directly
# into your RAG examples.
# ---------------------------------------------------------------------------
FEW_SHOT_EXAMPLES = [
    {
        "question": "total crimes per state, excluding the national total",
        "sql": (
            "SELECT state, SUM(crimes) AS total_crimes FROM crime_district "
            "WHERE state != 'Malaysia' AND district = 'All' AND type = 'all' "
            "GROUP BY state ORDER BY total_crimes DESC;"
        ),
    },
    {
        "question": "top 5 districts by crime count",
        "sql": (
            "SELECT district, SUM(crimes) AS total_crimes FROM crime_district "
            "WHERE district != 'All' GROUP BY district "
            "ORDER BY total_crimes DESC LIMIT 5;"
        ),
    },
    {
        "question": "which state has the highest total crimes across all years",
        "sql": (
            "SELECT state, SUM(crimes) AS total_crimes FROM crime_district "
            "WHERE state != 'Malaysia' AND district = 'All' "
            "GROUP BY state ORDER BY total_crimes DESC LIMIT 1;"
        ),
    },
    {
        "question": "crimes broken down by category, assault vs property",
        "sql": (
            "SELECT category, SUM(crimes) AS total_crimes FROM crime_district "
            "WHERE state != 'Malaysia' AND district != 'All' AND type = 'all' "
            "GROUP BY category ORDER BY total_crimes DESC;"
        ),
    },
    {
        "question": "total crimes per year to see the trend over time",
        "sql": (
            "SELECT EXTRACT(YEAR FROM date) AS year, SUM(crimes) AS total_crimes "
            "FROM crime_district WHERE state != 'Malaysia' AND district = 'All' "
            "AND type = 'all' GROUP BY year ORDER BY year;"
        ),
    },
]

# ---------------------------------------------------------------------------
# Schema glossary: short explanations of ambiguous or DOSM-specific terms,
# so Claude disambiguates instead of guessing.
# ---------------------------------------------------------------------------
GLOSSARY = [
    {
        "term": "police district vs administrative district",
        "explanation": (
            "crime_district uses POLICE districts (Royal Malaysian Police "
            "reporting boundaries). income_district and population_district use "
            "ADMINISTRATIVE districts (government/DOSM boundaries). These do NOT "
            "map 1:1 — the same district name can cover different physical areas "
            "in each table. There is currently no crosswalk table, so joins across "
            "crime_district and the other two tables by district name should be "
            "treated as approximate, not exact."
        ),
    },
    {
        "term": "'Malaysia' and 'All' sentinel values in crime_district",
        "explanation": (
            "In crime_district, state = 'Malaysia' marks a NATIONAL aggregate row "
            "(sum across all states). district = 'All' marks a STATE-level total "
            "row (sum across all districts in that state). type = 'all' marks a "
            "CATEGORY-level total row. Including these in a SUM alongside the "
            "detail rows causes double-counting. Filter them out unless the "
            "question specifically asks for a total/aggregate at that level."
        ),
    },
    {
        "term": "'overall' sentinel value in population_district",
        "explanation": (
            "In population_district, age = 'overall' means the row already sums "
            "all age bands; ethnicity = 'overall' means the row already sums all "
            "ethnicities. Same double-counting risk as the crime_district sentinel "
            "values — filter these out when summing across the detail rows."
        ),
    },
    {
        "term": "population units",
        "explanation": (
            "The population column in population_district is reported in "
            "THOUSANDS, not raw headcount. A value of 495.3 means approximately "
            "495,300 people. Multiply by 1000 if the question asks for an actual "
            "headcount rather than a thousands figure."
        ),
    },
    {
        "term": "date granularity",
        "explanation": (
            "All three tables store yearly snapshots as a DATE with month/day "
            "fixed to January 1st (e.g. '2023-01-01' represents the year 2023, "
            "not a specific day). Use EXTRACT(YEAR FROM date) for year-based "
            "grouping or filtering."
        ),
    },
    {
        "term": "year coverage differs by table",
        "explanation": (
            "crime_district covers 2016-2023. income_district only has three "
            "sparse years: 2019, 2022, 2024. population_district covers "
            "2020-2025. Any query joining across tables by year will only match "
            "on overlapping years — be explicit about this rather than assuming "
            "full coverage."
        ),
    },
]


def main():
    print("Loading local embedding model (all-MiniLM-L6-v2)...")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    engine = create_engine(
        f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )

    with engine.begin() as conn:
        print("Clearing existing RAG tables (idempotent reseed)...")
        conn.execute(text("DELETE FROM few_shot_examples"))
        conn.execute(text("DELETE FROM schema_glossary"))

        print(f"Embedding and inserting {len(FEW_SHOT_EXAMPLES)} few-shot examples...")
        for ex in FEW_SHOT_EXAMPLES:
            embedding = model.encode(ex["question"]).tolist()
            conn.execute(
                text(
                    "INSERT INTO few_shot_examples (question, sql, embedding) "
                    "VALUES (:question, :sql, :embedding)"
                ),
                {"question": ex["question"], "sql": ex["sql"], "embedding": str(embedding)},
            )

        print(f"Embedding and inserting {len(GLOSSARY)} glossary terms...")
        for g in GLOSSARY:
            # Embed term + explanation together so retrieval matches on meaning,
            # not just the short term string.
            embedding = model.encode(f"{g['term']}: {g['explanation']}").tolist()
            conn.execute(
                text(
                    "INSERT INTO schema_glossary (term, explanation, embedding) "
                    "VALUES (:term, :explanation, :embedding)"
                ),
                {
                    "term": g["term"],
                    "explanation": g["explanation"],
                    "embedding": str(embedding),
                },
            )

    print("Done. RAG tables seeded.")


if __name__ == "__main__":
    main()