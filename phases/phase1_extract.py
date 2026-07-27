"""
phases/phase1_extract.py
--------------------------
PHASE 1 : EXTRACT


"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd


class Extractor:
    """Reads a raw CSV file into a DataFrame and reports data quality."""

    def __init__(self, file_path: Path, logger: logging.Logger) -> None:
        self.file_path = file_path
        self.logger = logger
        self.raw_df: pd.DataFrame | None = None

    def extract(self) -> pd.DataFrame:
        """Run the full extraction pipeline and return the raw DataFrame."""
        self.logger.info(f"PHASE 1: Extracting data from {self.file_path}")
        self.raw_df = self._load_csv()
        self._check_shape(self.raw_df)
        self._check_dtypes(self.raw_df)
        self._check_missing_values(self.raw_df)
        self._check_duplicates(self.raw_df)
        self._summary(self.raw_df)
        self.logger.info("PHASE 1: Extraction complete.")
        return self.raw_df

    # -- internal steps ---------------------------------------------------

    def _load_csv(self) -> pd.DataFrame:
        if not self.file_path.exists():
            raise FileNotFoundError(f"Raw CSV not found: {self.file_path}")
        try:
            df = pd.read_csv(self.file_path)
        except Exception as exc:
            self.logger.error(f"Failed to read CSV: {exc}")
            raise
        self.logger.info(f"Loaded {len(df)} rows / {len(df.columns)} columns.")
        return df

    def _check_shape(self, df: pd.DataFrame) -> None:
        self.logger.info(f"Shape: {df.shape}")

    def _check_dtypes(self, df: pd.DataFrame) -> None:
        self.logger.info("Data types:\n" + df.dtypes.to_string())

    def _check_missing_values(self, df: pd.DataFrame) -> None:
        missing = df.isnull().sum()
        missing = missing[missing > 0]
        if missing.empty:
            self.logger.info("No missing values detected.")
        else:
            self.logger.warning("Missing values per column:\n" + missing.to_string())

    def _check_duplicates(self, df: pd.DataFrame) -> None:
        dup_count = df.duplicated().sum()
        if dup_count:
            self.logger.warning(f"Found {dup_count} fully duplicated rows.")
        else:
            self.logger.info("No fully duplicated rows detected.")

    def _summary(self, df: pd.DataFrame) -> None:
        self.logger.info("Descriptive summary (numeric columns):\n" + df.describe(include="all").to_string())
        self.logger.info("Preview (head):\n" + df.head().to_string())
