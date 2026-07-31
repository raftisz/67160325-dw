"""
etl_pipeline.py
================
ETL Pipeline: retail_logs.csv  ->  retail_warehouse.db (SQLite, Star Schema)

Star Schema:
    Fact_Sales            (fact table)
    Dim_Location           (Store_Code, Branch, Province, Region)
    Dim_Product            (Product_Name, Category)
    Dim_Date               (Sale_Date broken into Year/Month/Day/Quarter/Weekday)

Run:
    python3 etl_pipeline.py
"""

import pandas as pd
import sqlite3
import os
from datetime import datetime

SOURCE_CSV = "retail_logs.csv"
DB_FILE = "retail_warehouse.db"


# ---------------------------------------------------------------------------
# 1. EXTRACT
# ---------------------------------------------------------------------------
def extract(path: str) -> pd.DataFrame:
    print(f"[EXTRACT] Reading {path} ...")
    df = pd.read_csv(path, dtype=str)  # read everything as string first, cast later
    print(f"[EXTRACT] {len(df)} raw rows loaded.")
    return df


# ---------------------------------------------------------------------------
# 2. TRANSFORM
# ---------------------------------------------------------------------------
def clean_text(series: pd.Series) -> pd.Series:
    """Strip whitespace and normalize casing to Title Case for consistency."""
    return series.str.strip().str.replace(r"\s+", " ", regex=True).str.title()


def parse_dates(series: pd.Series) -> pd.Series:
    """The source data mixes several date formats:
       YYYY-MM-DD, DD-Mon-YYYY, DD/MM/YYYY.
       Try each format in turn until one parses successfully."""
    formats = ["%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y"]

    def _parse(value):
        value = value.strip()
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return pd.NaT

    return series.apply(_parse)


def transform(df: pd.DataFrame):
    print("[TRANSFORM] Cleaning and standardizing data ...")

    df = df.copy()

    # --- Basic cleanup: trim whitespace on every text column -----------------
    text_cols = ["Sale_ID", "Store_Code", "Branch", "Province", "Region",
                 "Product_Name", "Category", "Sale_Date"]
    for col in text_cols:
        df[col] = df[col].astype(str).str.strip()

    # --- Standardize categorical text (Branch/Province/Region/Category/Product) ---
    for col in ["Branch", "Province", "Region", "Category", "Product_Name"]:
        df[col] = clean_text(df[col])

    # --- Numeric columns -------------------------------------------------------
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    df["Unit_Price"] = pd.to_numeric(df["Unit_Price"], errors="coerce")
    df["Discount_Percent"] = pd.to_numeric(df["Discount_Percent"], errors="coerce")
    df["Discount_Percent"] = df["Discount_Percent"].fillna(0)  # assume no discount if missing

    # --- Parse messy date formats ----------------------------------------------
    df["Sale_Date"] = parse_dates(df["Sale_Date"])

    # --- Drop exact duplicate rows (same Sale_ID + identical values) -----------
    before = len(df)
    df = df.drop_duplicates(subset=["Sale_ID"], keep="first")
    print(f"[TRANSFORM] Removed {before - len(df)} duplicate row(s).")

    # --- Fill missing Region using Store_Code -> Region mapping (from other rows) ---
    region_map = (
        df.dropna(subset=["Region"])
        .drop_duplicates("Store_Code")
        .set_index("Store_Code")["Region"]
    )
    df["Region"] = df.apply(
        lambda r: region_map.get(r["Store_Code"], r["Region"]) if pd.isna(r["Region"]) or r["Region"] == ""
        else r["Region"],
        axis=1,
    )

    # Drop rows where critical fields still failed to parse
    before = len(df)
    df = df.dropna(subset=["Sale_Date", "Quantity", "Unit_Price"])
    print(f"[TRANSFORM] Dropped {before - len(df)} row(s) with unparsable core fields.")

    # --- Derived measure: total amount after discount ---------------------------
    df["Total_Amount"] = (
        df["Quantity"] * df["Unit_Price"] * (1 - df["Discount_Percent"] / 100)
    ).round(2)

    # =============================================================
    # Build DIMENSION TABLES
    # =============================================================

    # --- Dim_Location: one row per Store_Code (Branch/Province/Region are 1:1 with it) ---
    dim_location = (
        df[["Store_Code", "Branch", "Province", "Region"]]
        .drop_duplicates(subset=["Store_Code"])
        .sort_values("Store_Code")
        .reset_index(drop=True)
    )
    dim_location.insert(0, "Location_ID", range(1, len(dim_location) + 1))

    # --- Dim_Product: one row per Product_Name (Category is 1:1 with it) ---
    dim_product = (
        df[["Product_Name", "Category"]]
        .drop_duplicates(subset=["Product_Name"])
        .sort_values("Product_Name")
        .reset_index(drop=True)
    )
    dim_product.insert(0, "Product_ID", range(1, len(dim_product) + 1))

    # --- Dim_Date: one row per unique calendar date ---
    unique_dates = sorted(df["Sale_Date"].unique())
    dim_date = pd.DataFrame({"Sale_Date": unique_dates})
    dim_date["Date_ID"] = range(1, len(dim_date) + 1)
    dim_date["Year"] = pd.to_datetime(dim_date["Sale_Date"]).dt.year
    dim_date["Month"] = pd.to_datetime(dim_date["Sale_Date"]).dt.month
    dim_date["Day"] = pd.to_datetime(dim_date["Sale_Date"]).dt.day
    dim_date["Quarter"] = pd.to_datetime(dim_date["Sale_Date"]).dt.quarter
    dim_date["Weekday"] = pd.to_datetime(dim_date["Sale_Date"]).dt.day_name()
    dim_date = dim_date[["Date_ID", "Sale_Date", "Year", "Month", "Day", "Quarter", "Weekday"]]
    dim_date["Sale_Date"] = dim_date["Sale_Date"].dt.strftime("%Y-%m-%d")

    # =============================================================
    # Build FACT TABLE
    # =============================================================
    fact = df.merge(dim_location[["Store_Code", "Location_ID"]], on="Store_Code", how="left")
    fact = fact.merge(dim_product[["Product_Name", "Product_ID"]], on="Product_Name", how="left")

    fact["Sale_Date_str"] = fact["Sale_Date"].dt.strftime("%Y-%m-%d")
    date_lookup = dim_date.set_index("Sale_Date")["Date_ID"]
    fact["Date_ID"] = fact["Sale_Date_str"].map(date_lookup)

    fact_sales = fact[[
        "Sale_ID", "Location_ID", "Product_ID", "Date_ID",
        "Quantity", "Unit_Price", "Discount_Percent", "Total_Amount"
    ]].reset_index(drop=True)

    print(f"[TRANSFORM] Final fact rows: {len(fact_sales)}")
    print(f"[TRANSFORM] Dim_Location: {len(dim_location)} rows")
    print(f"[TRANSFORM] Dim_Product : {len(dim_product)} rows")
    print(f"[TRANSFORM] Dim_Date    : {len(dim_date)} rows")

    return fact_sales, dim_location, dim_product, dim_date


# ---------------------------------------------------------------------------
# 3. LOAD
# ---------------------------------------------------------------------------
def load(fact_sales, dim_location, dim_product, dim_date, db_path: str):
    print(f"[LOAD] Writing tables to {db_path} ...")

    if os.path.exists(db_path):
        os.remove(db_path)  # start fresh each run

    conn = sqlite3.connect(db_path)
    try:
        dim_location.to_sql("Dim_Location", conn, index=False, if_exists="replace")
        dim_product.to_sql("Dim_Product", conn, index=False, if_exists="replace")
        dim_date.to_sql("Dim_Date", conn, index=False, if_exists="replace")
        fact_sales.to_sql("Fact_Sales", conn, index=False, if_exists="replace")

        # Set up primary/foreign keys for data integrity (SQLite allows adding
        # constraints only at table-creation time, so we rebuild Fact_Sales
        # with explicit keys)
        conn.execute("DROP TABLE IF EXISTS Fact_Sales_tmp")
        conn.execute("""
            CREATE TABLE Fact_Sales_tmp (
                Sale_ID TEXT PRIMARY KEY,
                Location_ID INTEGER,
                Product_ID INTEGER,
                Date_ID INTEGER,
                Quantity INTEGER,
                Unit_Price REAL,
                Discount_Percent REAL,
                Total_Amount REAL,
                FOREIGN KEY (Location_ID) REFERENCES Dim_Location(Location_ID),
                FOREIGN KEY (Product_ID) REFERENCES Dim_Product(Product_ID),
                FOREIGN KEY (Date_ID) REFERENCES Dim_Date(Date_ID)
            )
        """)
        conn.execute("INSERT INTO Fact_Sales_tmp SELECT * FROM Fact_Sales")
        conn.execute("DROP TABLE Fact_Sales")
        conn.execute("ALTER TABLE Fact_Sales_tmp RENAME TO Fact_Sales")
        conn.commit()

        # Quick sanity check
        cur = conn.cursor()
        for tbl in ["Dim_Location", "Dim_Product", "Dim_Date", "Fact_Sales"]:
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            print(f"[LOAD] {tbl}: {cur.fetchone()[0]} rows")
    finally:
        conn.close()

    print("[LOAD] Done.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    raw_df = extract(SOURCE_CSV)
    fact_sales, dim_location, dim_product, dim_date = transform(raw_df)
    load(fact_sales, dim_location, dim_product, dim_date, DB_FILE)
    print("\nETL pipeline completed successfully.")
