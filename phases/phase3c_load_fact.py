"""
--------------------------------
PHASE 3C : LOAD - Fact Table

"""

from __future__ import annotations

import logging

import pandas as pd

from utils.database import DatabaseManager


class FactLoader:
    """Validates and loads the fact_sales table into the warehouse."""

    def __init__(self, db_manager: DatabaseManager, logger: logging.Logger) -> None:
        self.db = db_manager
        self.logger = logger

    def load(self, fact_df: pd.DataFrame) -> None:
        self.logger.info("PHASE 3C: Validating and loading fact_sales.")
        self._validate_foreign_keys(fact_df)
        self._validate_duplicates(fact_df)
        self._validate_record_count(fact_df)

        rows = self.db.load_dataframe(fact_df, "fact_sales", if_exists="append")
        self.logger.info(f"fact_sales: {rows} row(s) loaded.")
        self.logger.info("PHASE 3C: Fact load complete.")

    # -- validations --------------------------------------------------------

    def _validate_foreign_keys(self, fact_df: pd.DataFrame) -> None:
        """Confirm every FK in fact_df already exists in its dimension
        table in the warehouse (belt-and-braces on top of the DB's own
        FOREIGN KEY constraint)."""
        checks = {
            "customer_id": "dim_customer",
            "product_id": "dim_product",
            "date_id": "dim_date",
        }
        for fk_col, dim_table in checks.items():
            valid_ids = set(self.db.fetch_df(f"SELECT {fk_col} FROM {dim_table};")[fk_col])
            invalid = ~fact_df[fk_col].isin(valid_ids)
            if invalid.any():
                raise ValueError(
                    f"{invalid.sum()} row(s) reference a {fk_col} missing from {dim_table}."
                )
        self.logger.info("Foreign key validation passed for customer_id, product_id, date_id.")

    def _validate_duplicates(self, fact_df: pd.DataFrame) -> None:
        dup_count = fact_df.duplicated(subset=["order_id"]).sum()
        if dup_count:
            raise ValueError(f"fact_sales contains {dup_count} duplicate order_id value(s).")
        self.logger.info("No duplicate order_id values in fact_sales.")

    def _validate_record_count(self, fact_df: pd.DataFrame) -> None:
        if fact_df.empty:
            raise ValueError("fact_sales is empty - aborting load.")
        self.logger.info(f"fact_sales record count to load: {len(fact_df)}")
