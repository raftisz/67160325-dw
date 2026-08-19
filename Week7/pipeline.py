"""
================================================================================
 Omnichannel Retail ETL Pipeline
 Python Data Pipeline Engineering Lab

 Builds an Idempotent, Incremental ETL pipeline that:
   1. Extracts customers / products / orders (batch_1..3) from the source
      Excel workbook (READ-ONLY - the source file is never modified).
   2. Cleans & validates every order row, calculates gross/net amount,
      and routes bad rows to a quarantine file with a reason_code.
   3. Loads a Star Schema (dim_customer, dim_product, dim_date, fact_sales)
      into a SQLite database using upsert semantics.
   4. Is safe to re-run (idempotent) and supports incremental loading via
      an updated_at watermark recorded in pipeline_run_log.
   5. Exposes run_pipeline(config) as a single orchestration entry point
      and prints a KPI summary after every run.
================================================================================
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ==============================================================================
# LOGGING SETUP
# ==============================================================================
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pipeline.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("retail_etl")


# ==============================================================================
# TASK 1 - PIPELINE CONFIGURATION
# ==============================================================================
@dataclass
class PipelineConfig:
    """Central configuration object for a pipeline run.

    Attributes:
        input_path:      Path to the source Excel workbook (READ-ONLY).
        output_db:       Path to the target SQLite database file.
        batches:         Which orders_batch_N sheets to process, in order.
        error_mode:      "quarantine" (default) keeps the pipeline running and
                          routes bad rows to quarantine; "fail_fast" raises on
                          the first invalid row (useful for strict debugging).
        quarantine_csv:  Output path for the cumulative quarantine file.
        run_log_csv:     Output path for the cumulative pipeline_run_log export.
        max_quantity:    Upper bound for a valid quantity (data_dictionary rule).
    """

    input_path: str
    output_db: str
    batches: list[int] = field(default_factory=lambda: [1, 2, 3])
    error_mode: str = "quarantine"          # "quarantine" | "fail_fast"
    quarantine_csv: str = "quarantine.csv"
    run_log_csv: str = "pipeline_run_log.csv"
    max_quantity: int = 20


# ==============================================================================
# TASK 1 - EXTRACT
# ==============================================================================
def extract_sheet(input_path: str, sheet_name: str) -> pd.DataFrame:
    """Extract a single worksheet with logging + error handling.

    The source workbook is opened in read-only fashion (pandas.read_excel
    never writes back to the file), satisfying the "never edit source data"
    rule.
    """
    start = time.time()
    logger.info(f"[EXTRACT] start sheet='{sheet_name}'")
    try:
        df = pd.read_excel(input_path, sheet_name=sheet_name, dtype=str)
        duration = time.time() - start
        logger.info(
            f"[EXTRACT] done  sheet='{sheet_name}' rows={len(df)} "
            f"duration={duration:.3f}s"
        )
        return df
    except Exception as exc:  # noqa: BLE001 - we want to log & re-raise any failure
        duration = time.time() - start
        logger.error(
            f"[EXTRACT] FAILED sheet='{sheet_name}' duration={duration:.3f}s "
            f"error={exc}"
        )
        raise


def extract_customers(input_path: str) -> pd.DataFrame:
    return extract_sheet(input_path, "customers")


def extract_products(input_path: str) -> pd.DataFrame:
    return extract_sheet(input_path, "products")


def extract_orders_batch(input_path: str, batch_num: int) -> pd.DataFrame:
    return extract_sheet(input_path, f"orders_batch_{batch_num}")


# ==============================================================================
# TASK 2 - TRANSFORM HELPERS (safe type conversion + normalization)
# ==============================================================================
def safe_numeric(series: pd.Series) -> pd.Series:
    """Coerce a series to numeric; unparsable values become NaN (errors='coerce')."""
    return pd.to_numeric(series, errors="coerce")


def clean_currency_string(series: pd.Series) -> pd.Series:
    """Strip a leading currency label such as 'THB' and surrounding whitespace
    before numeric coercion, e.g. 'THB 979.4' -> 979.4.

    This is applied to unit_price, which mixes plain numbers with
    THB-prefixed strings in the raw source data.
    """
    cleaned = (
        series.astype(str)
        .str.replace(r"(?i)^\s*THB\s*", "", regex=True)
        .str.strip()
    )
    cleaned = cleaned.replace({"nan": np.nan, "": np.nan, "None": np.nan})
    return safe_numeric(cleaned)


def safe_datetime(series: pd.Series) -> pd.Series:
    """Coerce a series to datetime; unparsable / impossible dates (e.g. 31/02)
    become NaT (errors='coerce')."""
    return pd.to_datetime(series, errors="coerce")


# --- Categorical normalization -------------------------------------------------
# Mapping rationale: source data mixes casing ("cash" vs "Cash" vs "credit
# card") and uses a synonym ("E-Commerce") for an already-approved sales
# channel label ("Online"), per the assignment's data_dictionary rule
# "Map E-Commerce to Online" and "Normalize case and approved labels".
PAYMENT_METHOD_MAP = {
    "cash": "Cash",
    "credit card": "Credit Card",
    "promptpay": "PromptPay",
    "bank transfer": "Bank Transfer",
}

SALES_CHANNEL_MAP = {
    "store": "Store",
    "online": "Online",
    "marketplace": "Marketplace",
    "e-commerce": "Online",  # explicit data_dictionary rule
}


def normalize_category(series: pd.Series, mapping: dict[str, str]) -> pd.Series:
    """Lower-case + strip, then map to the approved label. Anything outside
    the known mapping is title-cased and passed through (so it is still
    visible for a validation rule to catch rather than silently dropped)."""
    lowered = series.astype(str).str.strip().str.lower()
    mapped = lowered.map(mapping)
    fallback = series.astype(str).str.strip().str.title()
    return mapped.fillna(fallback)


# ==============================================================================
# TASK 2 - TRANSFORM + VALIDATE
# ==============================================================================
QUARANTINE_COLUMNS = [
    "order_id",
    "source_batch",
    "reason_code",
    "customer_id",
    "product_id",
    "order_datetime",
    "quantity",
    "unit_price",
    "discount_pct",
    "payment_method",
    "sales_channel",
    "updated_at",
]


def clean_and_validate_orders(
    raw_orders: pd.DataFrame,
    customers: pd.DataFrame,
    products: pd.DataFrame,
    batch_num: int,
    max_quantity: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean, normalize and validate one batch of raw order rows.

    Returns:
        (valid_df, quarantine_df)

    Design note (important for the "read = valid + rejected" acceptance
    test): validation happens BEFORE deduplication, against every row that
    was read from the source. Deduplication is applied afterwards, only to
    the rows that already passed validation, and is reported separately as
    "duplicated" so the identity
        rows_read == rows_valid + rows_rejected
    holds exactly, with rows_duplicated tracked on top of rows_valid.
    """
    df = raw_orders.copy()
    df["source_batch"] = batch_num
    df["_row_no"] = range(len(df))

    known_customers = set(customers["customer_id"].dropna())
    products_indexed = products.set_index("product_id")
    known_products = set(products_indexed.index)
    inactive_products = set(
        products_indexed[products_indexed["active_flag"].str.upper() == "N"].index
    )

    # --- Safe type conversion -------------------------------------------------
    df["order_datetime_parsed"] = safe_datetime(df["order_datetime"])
    df["updated_at_parsed"] = safe_datetime(df["updated_at"])
    df["quantity_parsed"] = safe_numeric(df["quantity"])
    df["unit_price_parsed"] = clean_currency_string(df["unit_price"])
    df["discount_pct_parsed"] = safe_numeric(df["discount_pct"])

    # --- Normalization ----------------------------------------------------------
    df["payment_method_norm"] = normalize_category(
        df["payment_method"], PAYMENT_METHOD_MAP
    )
    df["sales_channel_norm"] = normalize_category(
        df["sales_channel"], SALES_CHANNEL_MAP
    )

    # --- Row-level validation: collect reason codes ----------------------------
    reasons = [[] for _ in range(len(df))]

    def flag(mask: pd.Series, code: str) -> None:
        for idx in df.index[mask.fillna(False)]:
            reasons[df.index.get_loc(idx)].append(code)

    flag(df["order_datetime_parsed"].isna(), "INVALID_DATETIME")

    flag(df["customer_id"].isna(), "MISSING_CUSTOMER_ID")
    known_cust_mask = df["customer_id"].notna() & ~df["customer_id"].isin(
        known_customers
    )
    flag(known_cust_mask, "CUSTOMER_NOT_FOUND")

    flag(df["product_id"].isna(), "MISSING_PRODUCT_ID")
    known_prod_mask = df["product_id"].notna() & ~df["product_id"].isin(
        known_products
    )
    flag(known_prod_mask, "PRODUCT_NOT_FOUND")
    inactive_mask = df["product_id"].isin(inactive_products)
    flag(inactive_mask, "PRODUCT_INACTIVE")

    qty = df["quantity_parsed"]
    flag(qty.isna(), "INVALID_QUANTITY")
    flag(qty.notna() & (qty <= 0), "INVALID_QUANTITY")
    flag(qty.notna() & (qty > max_quantity), "QUANTITY_OUT_OF_RANGE")
    flag(qty.notna() & (qty % 1 != 0), "INVALID_QUANTITY")

    price = df["unit_price_parsed"]
    flag(df["unit_price"].isna(), "MISSING_UNIT_PRICE")
    flag(df["unit_price"].notna() & price.isna(), "INVALID_UNIT_PRICE")
    flag(price.notna() & (price <= 0), "INVALID_UNIT_PRICE")

    disc = df["discount_pct_parsed"]
    flag(disc.isna(), "INVALID_DISCOUNT")
    flag(disc.notna() & ((disc < 0) | (disc > 100)), "INVALID_DISCOUNT")

    df["reason_code"] = [";".join(sorted(set(r))) if r else "" for r in reasons]
    df["is_valid"] = df["reason_code"] == ""

    rows_read = len(df)

    valid_df = df[df["is_valid"]].copy()
    quarantine_df = df[~df["is_valid"]].copy()

    logger.info(
        f"[VALIDATE] batch={batch_num} rows_read={rows_read} "
        f"valid={len(valid_df)} rejected={len(quarantine_df)} "
        f"(identity check: {len(valid_df) + len(quarantine_df) == rows_read})"
    )

    # --- Deduplication (only on rows that already passed validation) -----------
    before_dedup = len(valid_df)
    valid_df = valid_df.sort_values("updated_at_parsed", ascending=False)
    valid_df = valid_df.drop_duplicates(subset="order_id", keep="first")
    duplicates_removed = before_dedup - len(valid_df)
    logger.info(
        f"[DEDUP] batch={batch_num} before={before_dedup} "
        f"after={len(valid_df)} duplicates_removed={duplicates_removed}"
    )

    # --- Derived measures (calculated only after validation, per spec) ---------
    valid_df["quantity"] = valid_df["quantity_parsed"].astype(int)
    valid_df["unit_price"] = valid_df["unit_price_parsed"].astype(float)
    valid_df["discount_pct"] = valid_df["discount_pct_parsed"].astype(float)
    valid_df["gross_amount"] = valid_df["quantity"] * valid_df["unit_price"]
    valid_df["net_amount"] = valid_df["gross_amount"] * (
        1 - valid_df["discount_pct"] / 100.0
    )
    valid_df["payment_method"] = valid_df["payment_method_norm"]
    valid_df["sales_channel"] = valid_df["sales_channel_norm"]
    valid_df["order_datetime"] = valid_df["order_datetime_parsed"]
    valid_df["updated_at"] = valid_df["updated_at_parsed"]

    clean_cols = [
        "order_id",
        "order_datetime",
        "customer_id",
        "product_id",
        "quantity",
        "unit_price",
        "discount_pct",
        "payment_method",
        "sales_channel",
        "updated_at",
        "source_batch",
        "gross_amount",
        "net_amount",
    ]
    valid_df = valid_df[clean_cols].reset_index(drop=True)

    quarantine_df = quarantine_df.rename(
        columns={
            "quantity": "quantity",
            "unit_price": "unit_price",
        }
    )
    quarantine_df = quarantine_df[
        [c for c in QUARANTINE_COLUMNS if c in quarantine_df.columns]
    ].reset_index(drop=True)

    # attach duplicate count as pipeline metadata via DataFrame attrs
    valid_df.attrs["rows_read"] = rows_read
    valid_df.attrs["rows_valid_pre_dedup"] = before_dedup
    valid_df.attrs["rows_rejected"] = len(quarantine_df)
    valid_df.attrs["rows_duplicated"] = duplicates_removed

    return valid_df, quarantine_df


# ==============================================================================
# TASK 3 - STAR SCHEMA / SQLITE LOAD
# ==============================================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dim_customer (
    customer_key   INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id    TEXT NOT NULL UNIQUE,
    customer_name  TEXT,
    province       TEXT,
    segment        TEXT
);

CREATE TABLE IF NOT EXISTS dim_product (
    product_key    INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     TEXT NOT NULL UNIQUE,
    product_name   TEXT,
    category       TEXT,
    active_flag    TEXT
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_key   INTEGER PRIMARY KEY,
    full_date  TEXT NOT NULL UNIQUE,
    day        INTEGER,
    month      INTEGER,
    quarter    INTEGER,
    year       INTEGER
);

CREATE TABLE IF NOT EXISTS fact_sales (
    order_id        TEXT PRIMARY KEY,
    date_key        INTEGER NOT NULL REFERENCES dim_date(date_key),
    customer_key    INTEGER NOT NULL REFERENCES dim_customer(customer_key),
    product_key     INTEGER NOT NULL REFERENCES dim_product(product_key),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price      REAL NOT NULL CHECK (unit_price > 0),
    discount_pct    REAL NOT NULL CHECK (discount_pct BETWEEN 0 AND 100),
    gross_amount    REAL NOT NULL CHECK (gross_amount >= 0),
    net_amount      REAL NOT NULL CHECK (net_amount >= 0),
    payment_method  TEXT,
    sales_channel   TEXT,
    source_batch    INTEGER,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_run_log (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch           INTEGER,
    started_at      TEXT,
    ended_at        TEXT,
    rows_read       INTEGER,
    rows_valid      INTEGER,
    rows_rejected   INTEGER,
    rows_duplicated INTEGER,
    rows_loaded     INTEGER,
    net_sales_total REAL,
    status          TEXT
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    logger.info("[LOAD] creating star schema (idempotent CREATE TABLE IF NOT EXISTS)")
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def upsert_dim_customer(conn: sqlite3.Connection, customers: pd.DataFrame) -> None:
    """Idempotent load of the customer dimension.

    Uses INSERT ... ON CONFLICT(customer_id) DO UPDATE so re-running with the
    same (or refreshed) source data never creates duplicate dimension rows.
    """
    rows = customers[
        ["customer_id", "customer_name", "province", "segment"]
    ].dropna(subset=["customer_id"])
    sql = """
        INSERT INTO dim_customer (customer_id, customer_name, province, segment)
        VALUES (:customer_id, :customer_name, :province, :segment)
        ON CONFLICT(customer_id) DO UPDATE SET
            customer_name = excluded.customer_name,
            province = excluded.province,
            segment = excluded.segment;
    """
    with conn:
        conn.executemany(sql, rows.to_dict("records"))
    logger.info(f"[LOAD] dim_customer upserted rows={len(rows)}")


def upsert_dim_product(conn: sqlite3.Connection, products: pd.DataFrame) -> None:
    rows = products[
        ["product_id", "product_name", "category", "active_flag"]
    ].dropna(subset=["product_id"])
    sql = """
        INSERT INTO dim_product (product_id, product_name, category, active_flag)
        VALUES (:product_id, :product_name, :category, :active_flag)
        ON CONFLICT(product_id) DO UPDATE SET
            product_name = excluded.product_name,
            category = excluded.category,
            active_flag = excluded.active_flag;
    """
    with conn:
        conn.executemany(sql, rows.to_dict("records"))
    logger.info(f"[LOAD] dim_product upserted rows={len(rows)}")


def ensure_dim_dates(conn: sqlite3.Connection, dates: pd.Series) -> None:
    """Insert any dates that are not yet present in dim_date (idempotent)."""
    unique_dates = pd.to_datetime(dates.dropna().unique())
    if len(unique_dates) == 0:
        return
    rows = []
    for d in unique_dates:
        date_key = int(d.strftime("%Y%m%d"))
        rows.append(
            {
                "date_key": date_key,
                "full_date": d.strftime("%Y-%m-%d"),
                "day": d.day,
                "month": d.month,
                "quarter": (d.month - 1) // 3 + 1,
                "year": d.year,
            }
        )
    sql = """
        INSERT INTO dim_date (date_key, full_date, day, month, quarter, year)
        VALUES (:date_key, :full_date, :day, :month, :quarter, :year)
        ON CONFLICT(date_key) DO NOTHING;
    """
    with conn:
        conn.executemany(sql, rows)
    logger.info(f"[LOAD] dim_date ensured for {len(rows)} distinct dates")


def load_fact_sales(conn: sqlite3.Connection, valid_orders: pd.DataFrame) -> int:
    """Upsert clean order rows into fact_sales.

    Idempotency + incremental loading strategy:
      - order_id is the PRIMARY KEY (grain = one validated line item / order).
      - ON CONFLICT(order_id) DO UPDATE ... WHERE excluded.updated_at >
        fact_sales.updated_at ensures:
          * Re-running the exact same batch again touches ZERO rows (the
            WHERE condition is false because updated_at hasn't changed),
            so the fact row count never grows -> idempotent.
          * A later batch that carries a NEWER updated_at for an
            already-loaded order_id (a legitimate late-arriving correction)
            DOES get applied -> incremental / watermark-aware loading.
      - SQLite's `conn.total_changes` counter only increases for rows that
        are actually written (insert OR the WHERE-qualified update), so the
        delta gives us an exact "rows_loaded" count for the KPI summary.
    """
    if valid_orders.empty:
        return 0

    ck = pd.read_sql("SELECT customer_id, customer_key FROM dim_customer", conn)
    pk = pd.read_sql("SELECT product_id, product_key FROM dim_product", conn)

    merged = valid_orders.merge(ck, on="customer_id", how="inner").merge(
        pk, on="product_id", how="inner"
    )
    # rows that fail to merge here would indicate a race condition between
    # validation and load; log loudly if it ever happens instead of silently
    # dropping data.
    if len(merged) != len(valid_orders):
        missing = len(valid_orders) - len(merged)
        logger.warning(
            f"[LOAD] {missing} validated row(s) could not be mapped to "
            "dim_customer/dim_product keys at load time and were skipped"
        )

    ensure_dim_dates(conn, merged["order_datetime"])
    merged["date_key"] = pd.to_datetime(merged["order_datetime"]).dt.strftime(
        "%Y%m%d"
    ).astype(int)

    sql = """
        INSERT INTO fact_sales (
            order_id, date_key, customer_key, product_key, quantity,
            unit_price, discount_pct, gross_amount, net_amount,
            payment_method, sales_channel, source_batch, updated_at
        ) VALUES (
            :order_id, :date_key, :customer_key, :product_key, :quantity,
            :unit_price, :discount_pct, :gross_amount, :net_amount,
            :payment_method, :sales_channel, :source_batch, :updated_at
        )
        ON CONFLICT(order_id) DO UPDATE SET
            date_key = excluded.date_key,
            customer_key = excluded.customer_key,
            product_key = excluded.product_key,
            quantity = excluded.quantity,
            unit_price = excluded.unit_price,
            discount_pct = excluded.discount_pct,
            gross_amount = excluded.gross_amount,
            net_amount = excluded.net_amount,
            payment_method = excluded.payment_method,
            sales_channel = excluded.sales_channel,
            source_batch = excluded.source_batch,
            updated_at = excluded.updated_at
        WHERE excluded.updated_at > fact_sales.updated_at;
    """
    records = merged.copy()
    records["order_datetime"] = records["order_datetime"].astype(str)
    records["updated_at"] = records["updated_at"].astype(str)
    payload = records[
        [
            "order_id", "date_key", "customer_key", "product_key", "quantity",
            "unit_price", "discount_pct", "gross_amount", "net_amount",
            "payment_method", "sales_channel", "source_batch", "updated_at",
        ]
    ].to_dict("records")

    before = conn.total_changes
    try:
        with conn:  # single transaction for the whole batch
            conn.executemany(sql, payload)
    except sqlite3.Error as exc:
        logger.error(f"[LOAD] transaction FAILED, rolled back automatically: {exc}")
        raise
    after = conn.total_changes
    rows_loaded = after - before
    logger.info(f"[LOAD] fact_sales upsert applied rows_loaded={rows_loaded}")
    return rows_loaded


# ==============================================================================
# TASK 4 - RUN LOG / WATERMARK
# ==============================================================================
def write_run_log(
    conn: sqlite3.Connection,
    batch: int,
    started_at: str,
    ended_at: str,
    rows_read: int,
    rows_valid: int,
    rows_rejected: int,
    rows_duplicated: int,
    rows_loaded: int,
    net_sales_total: float,
    status: str,
) -> None:
    sql = """
        INSERT INTO pipeline_run_log (
            batch, started_at, ended_at, rows_read, rows_valid, rows_rejected,
            rows_duplicated, rows_loaded, net_sales_total, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    with conn:
        conn.execute(
            sql,
            (
                batch, started_at, ended_at, rows_read, rows_valid, rows_rejected,
                rows_duplicated, rows_loaded, net_sales_total, status,
            ),
        )


# ==============================================================================
# QUARANTINE SINK (deduplicated across re-runs so re-running a batch does not
# duplicate quarantine entries -> the quarantine file itself stays idempotent)
# ==============================================================================
class QuarantineSink:
    def __init__(self) -> None:
        self._records: dict[tuple, dict] = {}

    def add(self, quarantine_df: pd.DataFrame) -> None:
        for rec in quarantine_df.to_dict("records"):
            key = (rec.get("order_id"), rec.get("source_batch"), rec.get("_row_no"))
            self._records[key] = rec

    def to_dataframe(self) -> pd.DataFrame:
        if not self._records:
            return pd.DataFrame(columns=QUARANTINE_COLUMNS)
        df = pd.DataFrame(list(self._records.values()))
        cols = [c for c in QUARANTINE_COLUMNS if c in df.columns]
        return df[cols]

    def save(self, path: str) -> None:
        self.to_dataframe().to_csv(path, index=False)
        logger.info(f"[QUARANTINE] wrote {len(self._records)} unique rows -> {path}")


# ==============================================================================
# TASK 5 - ORCHESTRATION
# ==============================================================================
def process_batch(
    conn: sqlite3.Connection,
    config: PipelineConfig,
    customers: pd.DataFrame,
    products: pd.DataFrame,
    quarantine_sink: QuarantineSink,
    batch_num: int,
) -> dict:
    """Extract -> Transform -> Validate -> Load for a single batch, with a
    pipeline_run_log entry written regardless of success or failure so that
    a broken batch never destroys already-loaded data from prior batches."""
    started_at = pd.Timestamp.now().isoformat()
    logger.info(f"===== RUN START batch={batch_num} =====")
    try:
        raw_orders = extract_orders_batch(config.input_path, batch_num)
        valid_df, quarantine_df = clean_and_validate_orders(
            raw_orders, customers, products, batch_num, config.max_quantity
        )

        if config.error_mode == "fail_fast" and not quarantine_df.empty:
            raise ValueError(
                f"fail_fast mode: {len(quarantine_df)} invalid rows in "
                f"batch {batch_num}"
            )

        quarantine_sink.add(quarantine_df)

        rows_loaded = load_fact_sales(conn, valid_df)
        net_sales_total = float(valid_df["net_amount"].sum()) if len(valid_df) else 0.0

        status = "SUCCESS"
    except Exception as exc:  # noqa: BLE001 - batch-level isolation
        logger.error(f"[RUN] batch={batch_num} FAILED: {exc}")
        ended_at = pd.Timestamp.now().isoformat()
        write_run_log(
            conn, batch_num, started_at, ended_at,
            rows_read=0, rows_valid=0, rows_rejected=0, rows_duplicated=0,
            rows_loaded=0, net_sales_total=0.0, status=f"FAILED: {exc}",
        )
        logger.info(f"===== RUN END (FAILED) batch={batch_num} =====")
        # Previously loaded batches remain untouched because each batch's
        # fact load runs in its own transaction (see load_fact_sales).
        return {
            "batch": batch_num, "status": "FAILED", "rows_read": 0,
            "rows_valid": 0, "rows_rejected": 0, "rows_duplicated": 0,
            "rows_loaded": 0, "net_sales_total": 0.0,
        }

    ended_at = pd.Timestamp.now().isoformat()
    rows_read = valid_df.attrs.get("rows_read", 0)
    rows_rejected = valid_df.attrs.get("rows_rejected", len(quarantine_df))
    rows_duplicated = valid_df.attrs.get("rows_duplicated", 0)
    rows_valid_pre_dedup = valid_df.attrs.get("rows_valid_pre_dedup", len(valid_df))

    write_run_log(
        conn, batch_num, started_at, ended_at,
        rows_read=rows_read,
        rows_valid=rows_valid_pre_dedup,
        rows_rejected=rows_rejected,
        rows_duplicated=rows_duplicated,
        rows_loaded=rows_loaded,
        net_sales_total=net_sales_total,
        status=status,
    )
    logger.info(
        f"[KPI] batch={batch_num} read={rows_read} valid={rows_valid_pre_dedup} "
        f"rejected={rows_rejected} duplicated={rows_duplicated} "
        f"loaded={rows_loaded} net_sales={net_sales_total:,.2f}"
    )
    logger.info(f"===== RUN END ({status}) batch={batch_num} =====")

    return {
        "batch": batch_num, "status": status, "rows_read": rows_read,
        "rows_valid": rows_valid_pre_dedup, "rows_rejected": rows_rejected,
        "rows_duplicated": rows_duplicated, "rows_loaded": rows_loaded,
        "net_sales_total": net_sales_total,
    }


def run_pipeline(config: PipelineConfig) -> list[dict]:
    """Single orchestration entry point: extract -> transform -> validate ->
    load, for every batch listed in config.batches, against config.output_db.

    Returns a list of per-batch KPI dicts (one per batch processed in this
    call) which the caller can print / aggregate / persist.
    """
    conn = get_connection(config.output_db)
    create_schema(conn)

    customers = extract_customers(config.input_path)
    products = extract_products(config.input_path)
    upsert_dim_customer(conn, customers)
    upsert_dim_product(conn, products)

    quarantine_sink = QuarantineSink()
    # re-hydrate any previously written quarantine file so repeated
    # run_pipeline() calls keep a cumulative, de-duplicated quarantine list
    if Path(config.quarantine_csv).exists():
        try:
            quarantine_sink.add(pd.read_csv(config.quarantine_csv))
        except Exception:  # noqa: BLE001 - best-effort warm start
            pass

    results = []
    for batch_num in config.batches:
        result = process_batch(
            conn, config, customers, products, quarantine_sink, batch_num
        )
        results.append(result)

    quarantine_sink.save(config.quarantine_csv)
    export_run_log_csv(conn, config.run_log_csv)
    conn.close()

    print_kpi_summary(results)
    return results


def export_run_log_csv(conn: sqlite3.Connection, path: str) -> None:
    df = pd.read_sql("SELECT * FROM pipeline_run_log ORDER BY run_id", conn)
    df.to_csv(path, index=False)
    logger.info(f"[EXPORT] pipeline_run_log -> {path} ({len(df)} run(s) total)")


def print_kpi_summary(results: list[dict]) -> None:
    logger.info("----- KPI SUMMARY (this run_pipeline() call) -----")
    total_read = sum(r["rows_read"] for r in results)
    total_valid = sum(r["rows_valid"] for r in results)
    total_rejected = sum(r["rows_rejected"] for r in results)
    total_duplicated = sum(r["rows_duplicated"] for r in results)
    total_loaded = sum(r["rows_loaded"] for r in results)
    total_net_sales = sum(r["net_sales_total"] for r in results)
    logger.info(
        f"read={total_read} valid={total_valid} rejected={total_rejected} "
        f"duplicated={total_duplicated} loaded={total_loaded} "
        f"net_sales={total_net_sales:,.2f}"
    )


# ==============================================================================
# DEMONSTRATION MAIN — proves idempotency + incremental loading with >= 4 runs
# ==============================================================================
def main() -> None:
    input_path = "Python_Data_Pipeline_Lab_Dataset__1_.xlsx"
    output_db = "retail_dw.db"

    # start clean so the demonstration below is reproducible
    Path(output_db).unlink(missing_ok=True)
    Path("quarantine.csv").unlink(missing_ok=True)
    Path("pipeline_run_log.csv").unlink(missing_ok=True)

    logger.info("################ RUN 1: batch_1 (first load) ################")
    run_pipeline(PipelineConfig(input_path=input_path, output_db=output_db, batches=[1]))

    logger.info("################ RUN 2: batch_1 AGAIN (idempotency check) ################")
    run_pipeline(PipelineConfig(input_path=input_path, output_db=output_db, batches=[1]))

    logger.info("################ RUN 3: batch_2 (incremental load) ################")
    run_pipeline(PipelineConfig(input_path=input_path, output_db=output_db, batches=[2]))

    logger.info("################ RUN 4: batch_3 (incremental load) ################")
    run_pipeline(PipelineConfig(input_path=input_path, output_db=output_db, batches=[3]))

    # ---- Final verification / acceptance-test style assertions ----
    conn = get_connection(output_db)
    fact_count_after_run4 = conn.execute("SELECT COUNT(*) FROM fact_sales").fetchone()[0]
    dup_order_ids = conn.execute(
        "SELECT COUNT(*) FROM (SELECT order_id FROM fact_sales GROUP BY order_id HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    orphan_fk = conn.execute(
        """
        SELECT COUNT(*) FROM fact_sales f
        LEFT JOIN dim_customer c ON f.customer_key = c.customer_key
        LEFT JOIN dim_product p ON f.product_key = p.product_key
        LEFT JOIN dim_date d ON f.date_key = d.date_key
        WHERE c.customer_key IS NULL OR p.product_key IS NULL OR d.date_key IS NULL
        """
    ).fetchone()[0]
    negative_values = conn.execute(
        "SELECT COUNT(*) FROM fact_sales WHERE quantity < 0 OR unit_price < 0 OR net_amount < 0"
    ).fetchone()[0]
    net_sales_total = conn.execute("SELECT SUM(net_amount) FROM fact_sales").fetchone()[0]
    total_orders = conn.execute("SELECT COUNT(*) FROM fact_sales").fetchone()[0]

    logger.info("================ FINAL ACCEPTANCE CHECKS ================")
    logger.info(f"fact_sales row count (after 3 unique batches loaded): {fact_count_after_run4}")
    logger.info(f"duplicate order_id groups in fact_sales (must be 0): {dup_order_ids}")
    logger.info(f"fact rows with a dangling dimension FK (must be 0): {orphan_fk}")
    logger.info(f"fact rows with negative qty/price/net_amount (must be 0): {negative_values}")
    logger.info(f"TOTAL net sales across all loaded orders: {net_sales_total:,.2f} THB")
    logger.info(f"TOTAL orders loaded into fact_sales: {total_orders}")
    conn.close()


if __name__ == "__main__":
    main()
