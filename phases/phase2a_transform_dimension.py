"""
phases/phase2a_transform_dimension.py
----------------------------------------
PHASE 2A : TRANSFORM (Dimension Tables)

Responsibility:
1. Clean the raw columns (names, emails, product/category casing,
   dates, currency) into a single `clean_df`.
2. Build each Dimension table from that clean data:
      dim_customer, dim_product, dim_date
3. Assign a surrogate key to every dimension.

Design note - "supports adding new dimensions in the future":
    Every dimension is built through the same private helper,
    `_build_dimension()`, which takes a natural-key column and the
    columns to keep. Adding e.g. `dim_store` later is just one more
    call to that helper - no new pattern to invent.
"""

from __future__ import annotations

import logging
from typing import Dict, List

import pandas as pd

from utils.helper import (
    clean_currency,
    clean_email,
    clean_text,
    generate_surrogate_key,
    normalize_key,
    parse_flexible_date,
    title_case_category,
    title_case_name,
)


class DimensionTransformer:
    """Cleans raw data and produces every Dimension table."""

    def __init__(self, raw_df: pd.DataFrame, logger: logging.Logger) -> None:
        self.raw_df = raw_df
        self.logger = logger
        self.clean_df: pd.DataFrame | None = None

    # -- public API ---------------------------------------------------------

    def transform(self) -> Dict[str, pd.DataFrame]:
        """Run cleaning + build all dimension tables.

        Returns:
            A dict of {table_name: DataFrame}, e.g.
            {'dim_customer': ..., 'dim_product': ..., 'dim_date': ...}
        """
        self.logger.info("PHASE 2A: Cleaning raw data & building dimension tables.")
        self.clean_df = self._clean_raw_columns(self.raw_df)

        dimensions = {
            "dim_customer": self._build_dim_customer(),
            "dim_product": self._build_dim_product(),
            "dim_date": self._build_dim_date(),
        }

        for name, df in dimensions.items():
            self.logger.info(f"{name}: {len(df)} unique rows.")

        self.logger.info("PHASE 2A: Dimension transformation complete.")
        return dimensions

    # -- cleaning -------------------------------------------------------------

    def _clean_raw_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalise every raw column into a consistent, typed form.
        This clean_df is reused by Phase 2B to build the Fact table.
        """
        clean = df.copy()

        clean["order_id"] = clean["Order_ID"].apply(clean_text)
        clean["customer_name"] = clean["Customer_Name"].apply(title_case_name)
        clean["email"] = clean["Email"].apply(clean_email)
        clean["email_key"] = clean["email"].apply(normalize_key)

        clean["product_name"] = clean["Product"].apply(title_case_category)
        clean["product_key"] = clean["product_name"].apply(normalize_key)
        clean["category"] = clean["Category"].apply(title_case_category)

        clean["order_date"] = clean["Order_Date"].apply(parse_flexible_date)
        clean["quantity"] = pd.to_numeric(clean["Quantity"], errors="coerce")
        clean["unit_price"] = clean["Unit_Price"].apply(clean_currency)
        clean["amount"] = clean["Amount"].apply(clean_currency)

        # Impute missing Amount using Quantity * Unit_Price when possible,
        # instead of silently dropping ~23% of rows that only lack this
        # one derived measure.
        needs_impute = clean["amount"].isna() & clean["quantity"].notna() & clean["unit_price"].notna()
        clean.loc[needs_impute, "amount"] = (
            clean.loc[needs_impute, "quantity"] * clean.loc[needs_impute, "unit_price"]
        ).round(2)

        # Drop rows that are unusable even after cleaning (no order id,
        # no date, or no way to resolve amount).
        before = len(clean)
        clean = clean.dropna(subset=["order_id", "order_date", "email_key", "product_key", "amount"])
        dropped = before - len(clean)
        if dropped:
            self.logger.warning(f"Dropped {dropped} unusable row(s) during cleaning.")

        # Remove exact duplicate orders (same order_id re-entered).
        before = len(clean)
        clean = clean.drop_duplicates(subset=["order_id"], keep="first")
        dup_dropped = before - len(clean)
        if dup_dropped:
            self.logger.warning(f"Removed {dup_dropped} duplicate order_id row(s).")

        return clean.reset_index(drop=True)

    # -- generic dimension builder ----------------------------------------------

    def _build_dimension(
        self,
        source_cols: List[str],
        natural_key_col: str,
        id_column: str,
    ) -> pd.DataFrame:
        """Generic reusable routine for building any dimension table.

        Args:
            source_cols: Columns to keep from `clean_df` (context columns).
            natural_key_col: Column used to identify unique real-world
                entities (e.g. 'email_key', 'product_key', 'order_date').
            id_column: Name of the surrogate key to generate.
        """
        dim = (
            self.clean_df[source_cols]
            .drop_duplicates(subset=[natural_key_col], keep="first")
            .sort_values(natural_key_col)
            .reset_index(drop=True)
        )
        dim = generate_surrogate_key(dim, id_column)
        return dim

    # -- individual dimensions -----------------------------------------------

    def _build_dim_customer(self) -> pd.DataFrame:
        dim = self._build_dimension(
            source_cols=["email_key", "customer_name", "email"],
            natural_key_col="email_key",
            id_column="customer_id",
        )
        # Drop the helper matching key; it's not part of the published dimension.
        return dim[["customer_id", "customer_name", "email"]]

    def _build_dim_product(self) -> pd.DataFrame:
        # A product may have a missing Category on some rows but a valid
        # one on others (e.g. one row in the raw data). Backfill within
        # each product group BEFORE de-duplicating, so we never lose a
        # known category just because drop_duplicates happened to keep
        # the row where it was blank.
        working = self.clean_df[["product_key", "product_name", "category"]].copy()
        working["category"] = working.groupby("product_key")["category"].transform(
            lambda s: s.ffill().bfill()
        )
        working["category"] = working["category"].fillna("Uncategorized")

        dim = (
            working.drop_duplicates(subset=["product_key"], keep="first")
            .sort_values("product_key")
            .reset_index(drop=True)
        )
        dim = generate_surrogate_key(dim, "product_id")
        return dim[["product_id", "product_name", "category"]]

    def _build_dim_date(self) -> pd.DataFrame:
        dates = self.clean_df[["order_date"]].drop_duplicates(subset=["order_date"])
        dates = dates.sort_values("order_date").reset_index(drop=True)
        dates = generate_surrogate_key(dates, "date_id")

        dates["date"] = dates["order_date"].dt.strftime("%Y-%m-%d")
        dates["year"] = dates["order_date"].dt.year
        dates["quarter"] = dates["order_date"].dt.quarter
        dates["month"] = dates["order_date"].dt.month
        dates["month_name"] = dates["order_date"].dt.strftime("%B")
        dates["day"] = dates["order_date"].dt.day
        dates["weekday_name"] = dates["order_date"].dt.strftime("%A")

        return dates[
            ["date_id", "date", "year", "quarter", "month", "month_name", "day", "weekday_name"]
        ]
