-- ============================================================================
-- DRAFT SCHEMA — Phase 1
--
-- This is based on DOSM's PUBLISHED DOCUMENTATION for each dataset, not the
-- actual file contents. Run scripts/02_inspect_data.py first and compare its
-- output against this file. Column names/types below will likely need small
-- adjustments (e.g. exact column naming, whether values are integers or
-- floats) — that's expected, not a mistake.
-- ============================================================================

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'llm_readonly') THEN
    CREATE ROLE llm_readonly LOGIN PASSWORD 'llm_pass_dev_only';
  END IF;
END $$;
ALTER ROLE llm_readonly SET statement_timeout = '5s';

CREATE EXTENSION IF NOT EXISTS vector;

-- Crimes by district, category, and type (Royal Malaysian Police data via DOSM)
CREATE TABLE IF NOT EXISTS crime_district (
    id          SERIAL PRIMARY KEY,
    date        DATE NOT NULL,
    state       VARCHAR(50) NOT NULL,      -- one of 16 states, or 'malaysia' for national total
    district    VARCHAR(100) NOT NULL,     -- POLICE district — note: not the same boundaries as administrative district
    category    VARCHAR(50) NOT NULL,      -- e.g. 'assault', 'property', etc.
    type        VARCHAR(100) NOT NULL,     -- specific crime type within category
    crimes      INTEGER NOT NULL           -- count of crimes (convicted cases, per DOSM methodology)
);

-- Indexes: we will query this table constantly by district and by date range,
-- so both need an index. Composite index on (state, district) speeds up the
-- "crime by district" queries that will be your most common pattern.
CREATE INDEX IF NOT EXISTS idx_crime_district_location ON crime_district (state, district);
CREATE INDEX IF NOT EXISTS idx_crime_district_date ON crime_district (date);
CREATE INDEX IF NOT EXISTS idx_crime_district_category ON crime_district (category);


-- Household income by ADMINISTRATIVE district (different boundaries from
-- police districts above — this is exactly the kind of mismatch your RAG
-- glossary layer will later need to help the LLM understand)
CREATE TABLE IF NOT EXISTS income_district (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    state           VARCHAR(50) NOT NULL,
    district        VARCHAR(100) NOT NULL,   -- ADMINISTRATIVE district
    income_mean     NUMERIC(10, 2),
    income_median   NUMERIC(10, 2)
);

CREATE INDEX IF NOT EXISTS idx_income_district_location ON income_district (state, district);
CREATE INDEX IF NOT EXISTS idx_income_district_date ON income_district (date);


-- Population by administrative district, sex, age group, ethnicity
CREATE TABLE IF NOT EXISTS population_district (
    id          SERIAL PRIMARY KEY,
    date        DATE NOT NULL,
    state       VARCHAR(50) NOT NULL,
    district    VARCHAR(100) NOT NULL,
    sex         VARCHAR(20),
    age		VARCHAR(20),
    ethnicity   VARCHAR(30),
    population  NUMERIC(12, 1)   -- DOSM population figures are sometimes fractional (intercensal estimates)
);

CREATE INDEX IF NOT EXISTS idx_population_district_location ON population_district (state, district);
CREATE INDEX IF NOT EXISTS idx_population_district_date ON population_district (date);

-- NOTE ON JOINING: crime_district uses POLICE districts, income/population use
-- ADMINISTRATIVE districts. These do not always match 1:1. We will need a
-- district crosswalk/lookup table before any query joins across all three
-- tables reliably. This is a Phase 2 problem — flagged here so it's not a
-- surprise later.

-- ============================================================================
-- RAG LAYER — Step 7
--
-- Two retrieval use cases (see build guide):
--   1. Few-shot example retrieval: validated (question -> SQL) pairs. For each
--      new question, retrieve the most similar past examples and inject them
--      into the prompt. Highest-impact addition to text-to-SQL accuracy.
--   2. Schema/domain glossary retrieval: short explanations of ambiguous terms
--      (e.g. "police district" vs "administrative district") so Claude
--      disambiguates instead of guessing.
--
-- Embeddings are 384-dim, produced locally by sentence-transformers'
-- all-MiniLM-L6-v2 model (free, no API cost). If you ever switch embedding
-- models, the vector(384) dimension below must match the new model's output.
-- ============================================================================

CREATE TABLE IF NOT EXISTS few_shot_examples (
    id         SERIAL PRIMARY KEY,
    question   TEXT NOT NULL,
    sql        TEXT NOT NULL,
    embedding  vector(384) NOT NULL
);

-- HNSW index for fast approximate nearest-neighbor search on embeddings.
-- vector_cosine_ops = cosine similarity, the standard choice for sentence
-- embeddings (magnitude doesn't matter, only direction/similarity does).
CREATE INDEX IF NOT EXISTS idx_few_shot_embedding
    ON few_shot_examples USING hnsw (embedding vector_cosine_ops);


CREATE TABLE IF NOT EXISTS schema_glossary (
    id           SERIAL PRIMARY KEY,
    term         TEXT NOT NULL,
    explanation  TEXT NOT NULL,
    embedding    vector(384) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_glossary_embedding
    ON schema_glossary USING hnsw (embedding vector_cosine_ops);

-- Read-only role needs SELECT on these too (the loader/embedding script uses
-- crime_user to INSERT; the running app only ever needs to SELECT from them).
GRANT SELECT ON few_shot_examples, schema_glossary TO llm_readonly;

GRANT SELECT ON crime_district, income_district, population_district TO llm_readonly;
