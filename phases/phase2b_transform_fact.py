"""
phases/phase2b_transform_fact.py
------------------------------------
PHASE 2B : TRANSFORM (Fact Table)

Responsibility:
    Map every cleaned transaction row's Natural Keys (email, product
    name, date) to the Surrogate Keys generated in Phase 2A, then keep
    only Foreign Keys + Measures - no free text.
"""

from __future__ import annotations

import logging

import pandas as pd


class FactTransformer:
    """Builds fact_sales by merging clean transactions with dimensions."""

    def __init__(
        self,
        clean_df: pd.DataFrame,
        dim_customer: pd.DataFrame,
        dim_product: pd.DataFrame,
        dim_date: pd.DataFrame,
        logger: logging.Logger,
    ) -> None:
        self.clean_df = clean_df
        self.dim_customer = dim_customer
        self.dim_product = dim_product
        self.dim_date = dim_date
        self.logger = logger

    def transform(self) -> pd.DataFrame:
        """Run the merge + column-selection pipeline and return fact_sales."""
        self.logger.info("PHASE 2B: Building fact_sales table.")

        fact = self.clean_df.copy()
        fact["date"] = fact["order_date"].dt.strftime("%Y-%m-%d")

        fact = self._map_natural_to_surrogate(fact)
        fact = self._select_final_columns(fact)

        self.logger.info(f"fact_sales: {len(fact)} rows ready for load.")
        self.logger.info("PHASE 2B: Fact transformation complete.")
        return fact

    # -- internal steps ---------------------------------------------------

    def _map_natural_to_surrogate(self, fact: pd.DataFrame) -> pd.DataFrame:
        """merge() each natural key onto its surrogate key from the
        matching dimension table."""
        fact = fact.merge(
            self.dim_customer[["customer_id", "email"]],
            left_on="email_key",
            right_on=self.dim_customer["email"].apply(str.lower),
            how="left",
        )
        fact = fact.merge(
            self.dim_product[["product_id", "product_name"]],
            left_on="product_key",
            right_on=self.dim_product["product_name"].apply(str.lower),
            how="left",
        )
        fact = fact.merge(
            self.dim_date[["date_id", "date"]],
            on="date",
            how="left",
        )

        unmatched = fact["customer_id"].isna() | fact["product_id"].isna() | fact["date_id"].isna()
        if unmatched.any():
            self.logger.warning(
                f"{unmatched.sum()} row(s) failed to map to a dimension surrogate key and will be dropped."
            )
            fact = fact[~unmatched]

        return fact

    def _select_final_columns(self, fact: pd.DataFrame) -> pd.DataFrame:
        """Drop every text/context column - fact tables store Foreign
        Keys and Measures only."""
        result = fact[
            ["order_id", "customer_id", "product_id", "date_id", "quantity", "unit_price", "amount"]
        ].copy()
        result["customer_id"] = result["customer_id"].astype(int)
        result["product_id"] = result["product_id"].astype(int)
        result["date_id"] = result["date_id"].astype(int)
        result["quantity"] = result["quantity"].astype(int)
        return result.reset_index(drop=True)
