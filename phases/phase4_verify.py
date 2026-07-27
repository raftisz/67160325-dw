"""

----------------------------
PHASE 4 : VERIFICATION

"""

from __future__ import annotations

import logging
from typing import Dict

import pandas as pd

from utils.database import DatabaseManager

# One entry per report - easy to extend with new business questions.
_QUERIES: Dict[str, str] = {
    "table_row_counts": """
        SELECT 'dim_customer' AS table_name, COUNT(*) AS row_count FROM dim_customer
        UNION ALL
        SELECT 'dim_product', COUNT(*) FROM dim_product
        UNION ALL
        SELECT 'dim_date', COUNT(*) FROM dim_date
        UNION ALL
        SELECT 'fact_sales', COUNT(*) FROM fact_sales;
    """,
    "total_sales": """
        SELECT ROUND(SUM(amount), 2) AS total_sales,
               SUM(quantity)          AS total_quantity,
               COUNT(*)               AS total_orders
        FROM fact_sales;
    """,
    "sales_by_product": """
        SELECT p.product_name,
               p.category,
               SUM(f.quantity)        AS total_quantity,
               ROUND(SUM(f.amount),2) AS total_sales
        FROM fact_sales f
        JOIN dim_product p ON f.product_id = p.product_id
        GROUP BY p.product_name, p.category
        ORDER BY total_sales DESC;
    """,
    "sales_by_month": """
        SELECT d.year,
               d.month,
               d.month_name,
               ROUND(SUM(f.amount),2) AS total_sales
        FROM fact_sales f
        JOIN dim_date d ON f.date_id = d.date_id
        GROUP BY d.year, d.month, d.month_name
        ORDER BY d.year, d.month;
    """,
    "top_customer": """
        SELECT c.customer_name,
               c.email,
               ROUND(SUM(f.amount), 2) AS total_sales
        FROM fact_sales f
        JOIN dim_customer c ON f.customer_id = c.customer_id
        GROUP BY c.customer_name, c.email
        ORDER BY total_sales DESC
        LIMIT 10;
    """,
    "top_product": """
        SELECT p.product_name,
               ROUND(SUM(f.amount), 2) AS total_sales
        FROM fact_sales f
        JOIN dim_product p ON f.product_id = p.product_id
        GROUP BY p.product_name
        ORDER BY total_sales DESC
        LIMIT 10;
    """,
}


class Verifier:
    """Runs verification / reporting queries against the warehouse."""

    def __init__(self, db_manager: DatabaseManager, logger: logging.Logger) -> None:
        self.db = db_manager
        self.logger = logger

    def run_all(self) -> Dict[str, pd.DataFrame]:
        """Execute every verification query and log the results.

        Returns:
            {report_name: DataFrame} for programmatic use (e.g. tests,
            further reporting, notebooks).
        """
        self.logger.info("PHASE 4: Running verification queries.")
        results: Dict[str, pd.DataFrame] = {}
        for name, sql in _QUERIES.items():
            df = self.db.fetch_df(sql)
            results[name] = df
            self.logger.info(f"--- {name} ---\n{df.to_string(index=False)}")
        self.logger.info("PHASE 4: Verification complete.")
        return results
