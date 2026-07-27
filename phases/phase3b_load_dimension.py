"""
phases/phase3b_load_dimension.py
------------------------------------
PHASE 3B : LOAD - Dimension Tables

Responsibility:
    Load every dimension DataFrame into its already-created SQLite
    table using to_sql(if_exists='append') inside a transaction, so a
    failure on any one dimension rolls back cleanly instead of leaving
    the warehouse half-loaded.
"""

from __future__ import annotations

import logging
from typing import Dict

import pandas as pd

from utils.database import DatabaseManager


class DimensionLoader:
    """Loads Dimension tables into the SQLite warehouse."""

    def __init__(self, db_manager: DatabaseManager, logger: logging.Logger) -> None:
        self.db = db_manager
        self.logger = logger

    def load_all(self, dimensions: Dict[str, pd.DataFrame]) -> None:
        """Load every dimension table.

        Args:
            dimensions: {table_name: DataFrame}, e.g. the output of
                DimensionTransformer.transform().
        """
        self.logger.info("PHASE 3B: Loading dimension tables.")
        for table_name, df in dimensions.items():
            self._load_one(table_name, df)
        self.logger.info("PHASE 3B: Dimension load complete.")

    def _load_one(self, table_name: str, df: pd.DataFrame, if_exists: str = "append") -> None:
        try:
            rows = self.db.load_dataframe(df, table_name, if_exists=if_exists)
            self.logger.info(f"{table_name}: {rows} row(s) loaded.")
        except Exception as exc:
            self.logger.error(f"Failed to load {table_name}: {exc}")
            raise
