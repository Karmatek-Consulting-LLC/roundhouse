"""One-off DB-vs-model drift audit. Run inside the api container:

    docker exec -i <api-container> python -m app.schema_audit

Reports columns the model expects that aren't in Postgres (will cause
UndefinedColumn errors) and columns in Postgres that aren't on any model
(harmless, but flagged for cleanup).
"""
from __future__ import annotations

from sqlalchemy import inspect

from app import models  # noqa: F401 — populates Base.metadata
from app.db import _engine
from app.models import Base


def main() -> None:
    insp = inspect(_engine)
    db_tables = set(insp.get_table_names())

    missing_tables: list[str] = []
    missing_cols: list[tuple[str, str, str]] = []  # (table, column, type)
    extra_cols: list[tuple[str, str]] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in db_tables:
            missing_tables.append(table.name)
            continue
        db_cols = {c["name"]: c for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in db_cols:
                missing_cols.append((table.name, col.name, str(col.type)))
        for db_col_name in db_cols:
            if db_col_name not in table.columns:
                extra_cols.append((table.name, db_col_name))

    print("=" * 60)
    print("MISSING TABLES (model defines them; Postgres does not):")
    print("=" * 60)
    for t in missing_tables:
        print(f"  - {t}")
    if not missing_tables:
        print("  (none)")

    print()
    print("=" * 60)
    print("MISSING COLUMNS (model expects them; Postgres lacks them):")
    print("=" * 60)
    for table, col, type_ in missing_cols:
        print(f"  - {table}.{col}  ({type_})")
    if not missing_cols:
        print("  (none)")

    print()
    print("=" * 60)
    print("EXTRA COLUMNS (in Postgres but no longer on the model):")
    print("=" * 60)
    for table, col in extra_cols:
        print(f"  - {table}.{col}")
    if not extra_cols:
        print("  (none)")


if __name__ == "__main__":
    main()
