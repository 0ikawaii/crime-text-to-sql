"""
Step 8 — Airflow DAG replacing the manual script sequence:
    python scripts/01_download_data.py
    python scripts/02_inspect_data.py   (validation, not just inspection here)
    python scripts/03_load_to_postgres.py

Runs the existing scripts as subprocesses inside the Airflow container
(they're mounted at /opt/airflow/project_scripts). This keeps the actual
download/load logic in exactly one place rather than duplicating it as
native Airflow operators — the DAG's job here is scheduling, retries, and
dependency ordering, not reimplementing the scripts.

Schedule: daily (DOSM data is published periodically, not continuously —
adjust to '@monthly' if you'd rather match their actual publish cadence).
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

SCRIPTS_DIR = "/opt/airflow/project_scripts"

default_args = {
    "owner": "crime-text-to-sql",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="crime_data_pipeline",
    description="Download DOSM crime/income/population data, validate, load into Postgres",
    default_args=default_args,
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["crime-text-to-sql"],
) as dag:

    download = BashOperator(
        task_id="download_data",
        bash_command=f"cd {SCRIPTS_DIR} && python 01_download_data.py",
    )

    validate = BashOperator(
        task_id="validate_data",
        # 02_inspect_data.py prints schema/shape info; a non-zero exit code
        # (e.g. file missing, unreadable parquet) fails this task and blocks
        # the load step below — that's the idempotency/safety win over a
        # plain cron script.
        bash_command=f"cd {SCRIPTS_DIR} && python 02_inspect_data.py",
    )

    load = BashOperator(
        task_id="load_to_postgres",
        bash_command=f"cd {SCRIPTS_DIR} && python 03_load_to_postgres.py",
    )

    download >> validate >> load