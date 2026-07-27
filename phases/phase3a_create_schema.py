"""
-----------------------------------
PHASE 3A : LOAD - Create Warehouse Schema


"""

from __future__ import annotations

import logging

from utils.database import DatabaseManager

# Keeping the DDL as a dict-of-strings (one entry per table) - instead
# of one giant script - makes it trivial to add a new dimension's DDL
# later without touching the others.
_TABLE_DDL: dict[str, str] = {
    "dim_customer": """
        CREATE TABLE IF NOT EXISTS dim_customer (
            customer_id     INTEGER PRIMARY KEY,
            customer_name   TEXT NOT NULL,
            email           TEXT NOT NULL UNIQUE
        );
    """,
    "dim_product": """
        CREATE TABLE IF NOT EXISTS dim_product (
            product_id      INTEGER PRIMARY KEY,
            product_name    TEXT NOT NULL UNIQUE,
            category        TEXT NOT NULL
        );
    """,
    "dim_date": """
        CREATE TABLE IF NOT EXISTS dim_date (
            date_id         INTEGER PRIMARY KEY,
            date            TEXT NOT NULL UNIQUE,
            year            INTEGER NOT NULL,
            quarter         INTEGER NOT NULL,
            month           INTEGER NOT NULL,
            month_name      TEXT NOT NULL,
            day             INTEGER NOT NULL,
            weekday_name    TEXT NOT NULL
        );
    """,
    "fact_sales": """
        CREATE TABLE IF NOT EXISTS fact_sales (
            order_id        TEXT PRIMARY KEY,
            customer_id     INTEGER NOT NULL,
            product_id      INTEGER NOT NULL,
            date_id         INTEGER NOT NULL,
            quantity        INTEGER NOT NULL,
            unit_price      REAL NOT NULL,
            amount          REAL NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES dim_customer (customer_id),
            FOREIGN KEY (product_id)  REFERENCES dim_product (product_id),
            FOREIGN KEY (date_id)     REFERENCES dim_date (date_id)
        );
    """,
}


class SchemaCreator:
    """Creates the warehouse database schema."""

    def __init__(self, db_manager: DatabaseManager, logger: logging.Logger) -> None:
        self.db = db_manager
        self.logger = logger

    def create_all_tables(self) -> None:
        """Create every table. Dimensions are created before the Fact
        table so its FOREIGN KEY references already exist."""
        self.logger.info("PHASE 3A: Creating warehouse schema.")
        # Fresh run -> drop first so re-running the pipeline is idempotent.
        self._drop_all_tables()
        for table_name in ("dim_customer", "dim_product", "dim_date", "fact_sales"):
            self._create_table(table_name)
        self.logger.info("PHASE 3A: Schema creation complete.")

    def _create_table(self, table_name: str) -> None:
        self.db.execute_script(_TABLE_DDL[table_name])
        self.logger.info(f"Table ready: {table_name}")

    def _drop_all_tables(self) -> None:
        # Reverse order so FK dependents (fact_sales) drop before their
        # referenced dimensions.
        drop_script = "\n".join(
            f"DROP TABLE IF EXISTS {t};" for t in ("fact_sales", "dim_customer", "dim_product", "dim_date")
        )
        self.db.execute_script(drop_script)
