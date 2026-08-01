"""
Quick no-API-cost test of the sqlglot validation and LIMIT enforcement
from 04_text_to_sql.py. Run this from the crime-text-to-sql project root:

    python scripts/test_safety.py
"""

import sqlglot
import sqlglot.expressions as exp


def validate_sql(sql: str) -> bool:
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


def enforce_limit(sql: str, max_rows: int = 1000) -> str:
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


TEST_CASES = [
    ("DROP TABLE crime_district;", False),
    ("DELETE FROM crime_district WHERE state = 'Selangor';", False),
    ("SELECT * FROM crime_district; DROP TABLE crime_district;", False),
    ("SELECT state, SUM(crimes) FROM crime_district GROUP BY state", True),
]

if __name__ == "__main__":
    print("=== sqlglot validation tests ===\n")
    for sql, expected in TEST_CASES:
        result = validate_sql(sql)
        status = "PASS" if result == expected else "FAIL"
        print(f"[{status}] expected={expected} got={result}  sql={sql!r}\n")

    print("=== LIMIT enforcement tests ===\n")
    print(enforce_limit("SELECT * FROM population_district"))
    print()
    print(enforce_limit("SELECT * FROM population_district LIMIT 50"))
    print()
    print(enforce_limit("SELECT * FROM population_district LIMIT 5000"))