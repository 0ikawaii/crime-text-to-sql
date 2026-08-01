# Crime Analytics Text-to-SQL — Phase 1: Data Foundation

This is step 1 of the build order: get real DOSM data onto your machine, understand
its actual structure, and load it into PostgreSQL. Nothing LLM-related yet.

## Prerequisites

You need three things installed on your machine:
1. **Python 3.10+** — check with `python3 --version`
2. **Docker Desktop** — check with `docker --version`. We'll use Docker to run
   Postgres, so you don't have to install/configure Postgres natively on your OS.
3. A terminal you're comfortable in.

## Step-by-step



This reads `docker-compose.yml` and starts a Postgres 16 container in the background
(`-d` = detached). It creates a database called `crime_analytics` with a user/password
defined in the compose file (change these before you go further than a local demo).

Verify it's running:

```bash
docker ps
```

You should see a container named something like `crime-text-to-sql-db-1` with status "Up".

**Why Docker instead of installing Postgres directly?** Your project already plans to
containerize everything with Docker Compose later (step 9 in the build order). Starting
with Postgres in Docker now means you learn the tool from day one, and there's nothing
installed on your OS to conflict with or clean up later.

### Step 2 — Set up your Python environment

```bash
cd crime-text-to-sql
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

A virtual environment keeps this project's Python packages isolated from everything
else on your machine — standard practice, not optional.

### Step 3 — Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and confirm the values match what's in `docker-compose.yml` (they should,
by default — you only need to change this if you edit the compose file).

**Why a `.env` file?** Never hardcode database credentials in your scripts. This pattern
(load config from environment variables) is how real applications handle secrets, and
`.env` is gitignored so you never accidentally commit credentials.

   ### Step 4 — Download the real datasets

   ```bash
   python scripts/01_download_data.py
   ```

   This fetches three Parquet files directly from DOSM's open data storage and saves them
   to `data/raw/`. Read the script — it's short and commented. Confirm the files downloaded:

   ```bash
   ls -la data/raw/
   ```

### Step 5 — Inspect the data before designing anything

```bash
python scripts/02_inspect_data.py
```

This prints, for each dataset: column names and types, row count, first few rows, and
unique values for categorical columns. **Read this output carefully and share it with
me** — the schema in `sql/schema.sql` is a draft based on DOSM's published documentation,
not the actual file contents. We will very likely need to adjust column names/types
once you see the real data. This is normal and is exactly why this step exists before
Step 6.

### Step 6 — Create the database schema

```bash
docker exec -i crime-text-to-sql-db-1 psql -U crime_user -d crime_analytics < sql/schema.sql
```

This runs `schema.sql` against your running Postgres container, creating the three
tables. Open `sql/schema.sql` and read through it — comments explain each design choice.

### Step 7 — Load the data into Postgres

```bash
python scripts/03_load_to_postgres.py
```

This reads the local Parquet files and inserts them into the tables you just created.

### Step 8 — Verify

```bash
docker exec -it crime-text-to-sql-db-1 psql -U crime_user -d crime_analytics
```

Then inside the `psql` prompt, try:

```sql
\dt                                    -- list tables
SELECT COUNT(*) FROM crime_district;
SELECT * FROM crime_district LIMIT 5;
```

If you see real rows, Phase 1 is done. Don't move on until you can write a few `SELECT`
queries yourself — e.g. "total crimes per state" or "top 5 districts by crime count" —
comfortably by hand. That fluency is what makes it possible to evaluate whether the LLM
gets it right, later.

## What's next (not yet)

Phase 2 is writing manual SQL queries by hand against this data (no LLM) to build your
own intuition before Claude is ever in the loop. We'll do that once you confirm Phase 1
is working end-to-end.
