"""
Shared core logic for the text-to-SQL pipeline: schema-grounded prompt,
Claude API call, sqlglot validation, LIMIT enforcement, and query execution
against a dedicated read-only Postgres role.

Imported by both scripts/04_text_to_sql.py (CLI) and app.py (FastAPI),
so the safety logic and prompt live in exactly one place.
"""

import os

import anthropic
import sqlglot
import sqlglot.expressions as exp
from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# Loaded once at import time — sentence-transformers model load is the
# expensive part (~1-2s), so we pay that cost once per process, not per
# question. Same model used in scripts/05_seed_rag.py; must match, since
# retrieval compares against embeddings produced by that script.
_embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

TOP_K_EXAMPLES = 3
TOP_K_GLOSSARY = 3

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Dedicated read-only role (Step 4) — see sql/schema.sql / README for the
# CREATE ROLE statement. Never use the crime_user superuser here.
LLM_DB_USER = os.getenv("LLM_DB_USER", "llm_readonly")
LLM_DB_PASSWORD = os.getenv("LLM_DB_PASSWORD")

MODEL = "claude-sonnet-4-6"  # Sonnet 5 API model string
MAX_ROWS = 1000  # hard cap injected into any query that lacks its own LIMIT

# ---------------------------------------------------------------------------
# Schema-grounding prompt — written by hand, per the build guide's whole
# point: you see exactly what goes into the model and can iterate on it.
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


class SQLRejectedError(Exception):
    """Raised when generated SQL fails sqlglot validation."""


def retrieve_context(question: str) -> tuple[list[dict], list[dict]]:
    """Embed the question locally, then pull the top-k most similar few-shot
    examples and glossary entries from Postgres via pgvector cosine distance
    (`<=>` operator). No API cost — embedding runs on this machine's CPU."""
    embedding = _embedding_model.encode(question).tolist()
    embedding_str = str(embedding)

    engine = create_engine(
        f"postgresql+psycopg2://{LLM_DB_USER}:{LLM_DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    with engine.connect() as conn:
        examples = conn.execute(
            text(
                "SELECT question, sql FROM few_shot_examples "
                "ORDER BY embedding <=> :embedding LIMIT :k"
            ),
            {"embedding": embedding_str, "k": TOP_K_EXAMPLES},
        ).mappings().all()

        glossary = conn.execute(
            text(
                "SELECT term, explanation FROM schema_glossary "
                "ORDER BY embedding <=> :embedding LIMIT :k"
            ),
            {"embedding": embedding_str, "k": TOP_K_GLOSSARY},
        ).mappings().all()

    return [dict(e) for e in examples], [dict(g) for g in glossary]


def build_prompt_with_context(examples: list[dict], glossary: list[dict]) -> str:
    """Append retrieved few-shot examples and glossary entries to the base
    schema-grounding prompt. Kept separate from SCHEMA_PROMPT so the static
    base prompt stays easy to read/diff on its own."""
    parts = [SCHEMA_PROMPT]

    if glossary:
        parts.append("\n## Relevant domain notes for this question\n")
        for g in glossary:
            parts.append(f"- **{g['term']}**: {g['explanation']}")

    if examples:
        parts.append("\n## Similar validated examples (question -> correct SQL)\n")
        for e in examples:
            parts.append(f"Q: {e['question']}\nSQL: {e['sql']}\n")

    return "\n".join(parts)


def generate_sql(question: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")

    examples, glossary = retrieve_context(question)
    prompt = build_prompt_with_context(examples, glossary)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=prompt,
        messages=[{"role": "user", "content": question}],
    )
    sql = response.content[0].text.strip()
    if sql.startswith("```"):
        sql = sql.strip("`")
        if sql.lower().startswith("sql"):
            sql = sql[3:].strip()
    return sql


def validate_sql(sql: str) -> tuple[bool, str]:
    """Returns (is_valid, reason). reason is '' when valid."""
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception as e:
        return False, f"sqlglot could not parse the query: {e}"

    if len(statements) != 1:
        return False, f"expected exactly 1 statement, got {len(statements)}"

    stmt = statements[0]
    if stmt is None or stmt.key.lower() != "select":
        return False, f"statement is not a SELECT (got '{stmt.key if stmt else None}')"

    return True, ""


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
            pass

    parsed.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    return parsed.sql(dialect="postgres")


def run_query(sql: str):
    # Dedicated read-only role — never the crime_user superuser.
    engine = create_engine(
        f"postgresql+psycopg2://{LLM_DB_USER}:{LLM_DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    with engine.connect() as conn:
        conn.execute(text("SET statement_timeout = '5s'"))
        result = conn.execute(text(sql))
        rows = result.fetchall()
        columns = list(result.keys())
    return columns, rows


def answer_question(question: str) -> dict:
    """Full pipeline: question -> retrieve RAG context -> SQL -> validate ->
    limit -> execute. Returns a dict suitable for JSON serialization. Raises
    SQLRejectedError if validation fails (caller decides how to surface
    that, e.g. HTTP 400)."""
    examples, glossary = retrieve_context(question)
    prompt = build_prompt_with_context(examples, glossary)

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=prompt,
        messages=[{"role": "user", "content": question}],
    )
    sql = response.content[0].text.strip()
    if sql.startswith("```"):
        sql = sql.strip("`")
        if sql.lower().startswith("sql"):
            sql = sql[3:].strip()

    is_valid, reason = validate_sql(sql)
    if not is_valid:
        raise SQLRejectedError(f"Generated SQL rejected: {reason}\nSQL was: {sql}")

    final_sql = enforce_limit(sql)
    columns, rows = run_query(final_sql)

    return {
        "question": question,
        "sql": final_sql,
        "columns": columns,
        "rows": [list(row) for row in rows],
        "row_count": len(rows),
        "retrieved_examples": [e["question"] for e in examples],
        "retrieved_glossary_terms": [g["term"] for g in glossary],
    }