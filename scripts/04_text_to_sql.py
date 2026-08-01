"""
Step 3 + 4 — Core text-to-SQL loop, with SQL safety hardening.

Flow: natural-language question -> Claude API (schema-grounded prompt)
      -> generated SQL -> sqlglot validation (read-only SELECT only)
      -> LIMIT enforcement -> execute as a dedicated read-only Postgres
      role with a statement timeout -> print results.

Safety layers (defense in depth, not just one check):
  1. sqlglot AST validation — rejects anything that isn't a single SELECT.
  2. Hard row LIMIT — injected/overridden if missing or too large.
  3. Dedicated read-only DB role (llm_readonly) — can't write even if the
     above checks were bypassed.
  4. Statement timeout (5s) — set both on the role and the session.

No RAG yet — that's a later step.
"""

import os
import sys

import anthropic
import sqlglot
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Step 4 — SQL safety hardening: the LLM's queries run under a dedicated
# read-only Postgres role (llm_readonly), NOT the crime_user superuser used
# by the loader scripts. This role can only SELECT — no INSERT/UPDATE/DELETE/
# DROP is possible even if sqlglot's check were somehow bypassed. It also has
# a 5s statement_timeout set at the role level (see the CREATE ROLE step).
LLM_DB_USER = os.getenv("LLM_DB_USER", "llm_readonly")
LLM_DB_PASSWORD = os.getenv("LLM_DB_PASSWORD")

MAX_ROWS = 1000  # hard cap injected into any query that lacks its own LIMIT

MODEL = "claude-sonnet-4-6"  # Sonnet 5 API model string, per the build guide's recommendation

# ---------------------------------------------------------------------------
# Schema-grounding prompt — written by hand, per the guide's whole point:
# you see exactly what goes into the model and can iterate on it directly.
# ---------------------------------------------------------------------------
SCHEMA_PROMPT = """You are a PostgreSQL expert. Convert the user's natural-language
question into a single, valid, READ-ONLY PostgreSQL SELECT query.

Return ONLY the SQL query. No markdown code fences, no explanation, no preamble.

## Schema

### crime_district
- id SERIAL PRIMARY KEY
- date DATE            -- yearly snapshot, e.g. '2023-01-01'; always Jan 1 of that year
- state VARCHAR(50)     -- one of 14 Malaysian states, OR the literal value 'Malaysia'
                        --   which is a NATIONAL AGGREGATE row, not a real state
- district VARCHAR(100) -- POLICE district (not the same boundaries as administrative
                        --   districts used in income_district / population_district).
                        --   The literal value 'All' means this row is a STATE-LEVEL
                        --   TOTAL, not a real district.
- category VARCHAR(50)  -- 'assault' or 'property'
- type VARCHAR(100)     -- specific crime type within category, e.g. 'murder',
                        --   'theft_vehicle_motorcar'. The literal value 'all' means
                        --   this row is a CATEGORY-LEVEL TOTAL across all types.
- crimes INTEGER        -- count of crimes

IMPORTANT: to avoid double-counting, most real per-district, per-type analysis queries
should filter WHERE state != 'Malaysia' AND district != 'All' AND type != 'all'
(adjust depending on what level of aggregation the question actually wants).

### income_district
- id SERIAL PRIMARY KEY
- date DATE             -- yearly snapshot; only years 2019, 2022, 2024 exist
- state VARCHAR(50)
- district VARCHAR(100)  -- ADMINISTRATIVE district (different boundaries from the
                         --   POLICE districts in crime_district — do not assume a
                         --   district name in one table maps cleanly to the other)
- income_mean NUMERIC(10,2)
- income_median NUMERIC(10,2)

### population_district
- id SERIAL PRIMARY KEY
- date DATE              -- yearly snapshot; years 2020-2025
- state VARCHAR(50)
- district VARCHAR(100)   -- ADMINISTRATIVE district (same boundary system as
                          --   income_district)
- sex VARCHAR(20)         -- 'both', 'female', 'male'
- age VARCHAR(20)         -- age band like '25-29', or the literal 'overall' meaning
                          --   all ages summed
- ethnicity VARCHAR(30)   -- ethnicity, or the literal 'overall' meaning all
                          --   ethnicities summed
- population NUMERIC(12,1) -- reported in THOUSANDS (e.g. 495.3 = approx 495,300 people)

## Rules
- Only generate SELECT statements. Never INSERT, UPDATE, DELETE, DROP, ALTER, etc.
- Always alias aggregate columns (e.g. SUM(crimes) AS total_crimes) for readable output.
- When the question implies totals/averages, be explicit about which sentinel rows
  ('Malaysia', 'All', 'all', 'overall') to include or exclude — don't silently
  double-count.
- If the question is ambiguous about time range, default to the most recent
  available year for that table rather than summing across all years.
"""


def generate_sql(question: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=SCHEMA_PROMPT,
        messages=[{"role": "user", "content": question}],
    )
    sql = response.content[0].text.strip()
    # strip markdown fences if the model adds them despite instructions
    if sql.startswith("```"):
        sql = sql.strip("`")
        if sql.lower().startswith("sql"):
            sql = sql[3:].strip()
    return sql


def validate_sql(sql: str) -> bool:
    """Parse with sqlglot and confirm it's a single read-only SELECT statement."""
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception as e:
        print(f"  sqlglot could not parse the query: {e}")
        return False

    if len(statements) != 1:
        print(f"  Rejected: expected exactly 1 statement, got {len(statements)}.")
        return False

    stmt = statements[0]
    if stmt is None or stmt.key.lower() != "select":
        print(f"  Rejected: statement is not a SELECT (got '{stmt.key if stmt else None}').")
        return False

    return True


def enforce_limit(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Cap the query's row count. If it already has a LIMIT <= max_rows, leave
    it alone. If it has no LIMIT, or one larger than max_rows, override it."""
    parsed = sqlglot.parse_one(sql, read="postgres")

    existing_limit = parsed.args.get("limit")
    if existing_limit is not None:
        try:
            current_value = int(existing_limit.expression.this)
            if current_value <= max_rows:
                return parsed.sql(dialect="postgres")
        except (AttributeError, ValueError):
            pass  # non-literal LIMIT expression; fall through and override

    parsed.set("limit", sqlglot.exp.Limit(expression=sqlglot.exp.Literal.number(max_rows)))
    return parsed.sql(dialect="postgres")


def run_query(sql: str):
    # Dedicated read-only role — see LLM_DB_USER/LLM_DB_PASSWORD above.
    engine = create_engine(
        f"postgresql+psycopg2://{LLM_DB_USER}:{LLM_DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    with engine.connect() as conn:
        # Belt-and-suspenders: also set a statement timeout at the session
        # level, in case this connection ever runs under a role without one.
        conn.execute(text("SET statement_timeout = '5s'"))
        result = conn.execute(text(sql))
        rows = result.fetchall()
        columns = result.keys()
    return columns, rows


def main():
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    question = " ".join(sys.argv[1:]) or input("Ask a question about the crime data: ")

    print(f"\nQuestion: {question}")
    print("\nGenerating SQL...")
    sql = generate_sql(question)
    print(f"\nGenerated SQL:\n{sql}\n")

    print("Validating with sqlglot...")
    if not validate_sql(sql):
        print("Query rejected. Aborting — not executing unsafe or unparseable SQL.")
        sys.exit(1)
    print("  Valid read-only SELECT.")

    sql = enforce_limit(sql)
    print(f"\nFinal SQL (after LIMIT enforcement):\n{sql}\n")

    print("Executing (as read-only role, 5s timeout)...")
    columns, rows = run_query(sql)

    print(f"\nResults ({len(rows)} rows):")
    print(" | ".join(columns))
    for row in rows[:20]:
        print(" | ".join(str(v) for v in row))
    if len(rows) > 20:
        print(f"... ({len(rows) - 20} more rows not shown)")


if __name__ == "__main__":
    main()