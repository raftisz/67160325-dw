# Omnichannel Retail ETL Pipeline

An idempotent, incremental ETL pipeline that extracts customer / product /
order data from an Excel workbook, cleans and validates it, and loads a
Star Schema into a SQLite data warehouse.

## 1. Installation

```bash
pip install pandas numpy openpyxl
```

Python 3.10+ is required (the script uses `list[int]` style generics and
`from __future__ import annotations`).

## 2. How to run

Place `pipeline.py` and `Python_Data_Pipeline_Lab_Dataset__1_.xlsx` in the
same folder, then:

```bash
python pipeline.py
```

This runs the built-in demonstration in `main()`, which calls
`run_pipeline()` **four times** to prove idempotency and incremental
loading:

1. `run_pipeline(batches=[1])` — first load of batch 1
2. `run_pipeline(batches=[1])` — **same** batch 1 again → 0 new rows
   loaded (idempotency proof)
3. `run_pipeline(batches=[2])` — incremental load of batch 2
4. `run_pipeline(batches=[3])` — incremental load of batch 3

Every call is a full **Extract → Transform → Validate → Load** cycle
through `run_pipeline(config: PipelineConfig)`, the single orchestration
entry point required by the assignment. You can also call it yourself for
any subset of batches:

```python
from pipeline import PipelineConfig, run_pipeline

run_pipeline(PipelineConfig(
    input_path="Python_Data_Pipeline_Lab_Dataset__1_.xlsx",
    output_db="retail_dw.db",
    batches=[1, 2, 3],
))
```

The source Excel workbook is **only ever read** (`pandas.read_excel`); the
pipeline never writes back to it.

## 3. Output files

| File | Description |
|---|---|
| `pipeline.py` | Full source code, runnable end-to-end |
| `retail_dw.db` | SQLite Star Schema warehouse after all 3 batches |
| `quarantine.csv` | Every rejected row with `reason_code`, de-duplicated across re-runs |
| `pipeline_run_log.csv` | One row per `run_pipeline()` call — the run history / watermark evidence |
| `pipeline.log` | Full execution log (extract/validate/dedup/load timings) |
| `README.md` | This file |

## 4. Star Schema

Grain of `fact_sales`: **one validated order-product line item per
`order_id`.**

```
                    ┌───────────────┐
                    │   dim_date    │
                    │ date_key (PK) │
                    │ full_date     │
                    │ day/month/    │
                    │ quarter/year  │
                    └───────┬───────┘
                            │
┌───────────────┐   ┌───────┴────────┐   ┌────────────────┐
│  dim_customer  │   │   fact_sales   │   │   dim_product   │
│ customer_key PK│───┤ order_id (PK)  ├───│ product_key PK  │
│ customer_id UQ │   │ date_key FK    │   │ product_id  UQ  │
│ customer_name  │   │ customer_key FK│   │ product_name    │
│ province       │   │ product_key FK │   │ category        │
│ segment        │   │ quantity       │   │ active_flag     │
└────────────────┘   │ unit_price     │   └─────────────────┘
                      │ discount_pct  │
                      │ gross_amount  │
                      │ net_amount    │
                      │ payment_method│
                      │ sales_channel │
                      │ source_batch  │
                      │ updated_at    │
                      └────────────────┘

pipeline_run_log: run_id (PK), batch, started_at, ended_at, rows_read,
rows_valid, rows_rejected, rows_duplicated, rows_loaded, net_sales_total, status
```

- `order_id` is the **PRIMARY KEY** of `fact_sales` (prevents duplicate
  facts). `customer_id`, `product_id` and `full_date`/`date_key` are all
  **UNIQUE** in their dimension tables.
- Dimensions are loaded with `INSERT ... ON CONFLICT DO UPDATE`
  (upsert) — safe to re-run.
- `fact_sales` is loaded with `INSERT ... ON CONFLICT(order_id) DO UPDATE
  ... WHERE excluded.updated_at > fact_sales.updated_at` — an
  **update-if-newer** upsert. This is what makes the pipeline both
  idempotent (re-running the same batch changes nothing, since
  `updated_at` hasn't changed) and incremental (a legitimately newer
  version of an already-loaded order **does** get applied). The dataset
  actually exercises this: order `O000831` appears in both batch 2
  (`updated_at = 2026-04-22`) and batch 3 (`updated_at = 2026-03-17`);
  because batch 2 is loaded first, the older batch-3 copy is correctly
  ignored.
- Every fact/dimension load happens inside its own SQLite transaction
  (`with conn:` block), so a failure loading one batch cannot corrupt or
  roll back data that was already committed for previous batches.

## 5. Data cleaning & validation rules implemented

| Rule | Reason code |
|---|---|
| `order_datetime` not parseable (e.g. `31/02/2026`, `not-a-date`) | `INVALID_DATETIME` |
| `customer_id` missing | `MISSING_CUSTOMER_ID` |
| `customer_id` not present in `customers` | `CUSTOMER_NOT_FOUND` |
| `product_id` missing | `MISSING_PRODUCT_ID` |
| `product_id` not present in `products` | `PRODUCT_NOT_FOUND` |
| `product_id` exists but `active_flag = 'N'` | `PRODUCT_INACTIVE` |
| `quantity` non-numeric (e.g. `"three"`), ≤ 0, or non-integer | `INVALID_QUANTITY` |
| `quantity` > 20 (data-dictionary upper bound) | `QUANTITY_OUT_OF_RANGE` |
| `unit_price` missing | `MISSING_UNIT_PRICE` |
| `unit_price` unparsable / ≤ 0 (after stripping a `THB ` prefix) | `INVALID_UNIT_PRICE` |
| `discount_pct` outside `[0, 100]` | `INVALID_DISCOUNT` |

A row can carry multiple reason codes (joined with `;`) if it fails more
than one rule.

**Normalization mappings** (rationale: source mixes casing and a synonym):

- `payment_method`: `cash→Cash`, `credit card→Credit Card`,
  `promptpay→PromptPay`, `bank transfer→Bank Transfer` (case-insensitive).
- `sales_channel`: `store→Store`, `online→Online`,
  `marketplace→Marketplace`, and **`e-commerce→Online`** per the
  data dictionary's explicit rule ("Map E-Commerce to Online").

**Deduplication**: within the rows that already passed validation,
`order_id` is deduplicated keeping the record with the latest
`updated_at` (`quality_rule` in `data_dictionary`). Validation happens
**before** deduplication, so for every batch:

```
rows_read == rows_valid + rows_rejected      (exact identity, see pipeline.log)
rows_loaded <= rows_valid - rows_duplicated
```

(`rows_loaded` can additionally fall below `rows_valid - rows_duplicated`
when a cross-batch order_id collision loses to an already-loaded, newer
`updated_at` — see the `O000831` example above.)

`gross_amount = quantity * unit_price` and
`net_amount = gross_amount * (1 - discount_pct / 100)` are computed only
after a row is confirmed valid.

## 6. Idempotency & incremental-loading evidence

From `pipeline_run_log.csv` (also see `pipeline.log` for full detail):

| run_id | batch | rows_read | rows_valid | rows_rejected | rows_duplicated | rows_loaded | status |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 420 | 376 | 44 | 0 | **376** | SUCCESS |
| 2 | 1 (rerun) | 420 | 376 | 44 | 0 | **0** | SUCCESS |
| 3 | 2 | 424 | 369 | 55 | 1 | 368 | SUCCESS |
| 4 | 3 | 424 | 372 | 52 | 3 | 368 | SUCCESS |

Run 2 re-processes the exact same batch 1 and loads **zero** new/changed
rows — `SELECT COUNT(*) FROM fact_sales` does not grow — proving
idempotency. Runs 3 and 4 only add rows for `order_id`s not already
present (or present with an older `updated_at`), proving incremental
loading driven by the `updated_at` watermark recorded in
`pipeline_run_log`.

Final state after all 4 runs (asserted at the end of `main()`):

- `fact_sales` row count: **1,111** (376 + 368 + 368; batch-1 rerun added 0)
- Duplicate `order_id` groups in `fact_sales`: **0**
- Fact rows with a dangling dimension FK: **0**
- Fact rows with negative `quantity` / `unit_price` / `net_amount`: **0**
- Total net sales loaded: **≈ 2,720,914.72 THB**

## 7. Failure isolation

`process_batch()` wraps each batch's Extract→Transform→Validate→Load in
its own `try/except`. If a batch fails (bad file, corrupt sheet, etc.):

- The failure is logged and written to `pipeline_run_log` with
  `status = "FAILED: <reason>"` and zeroed KPI counters.
- No exception propagates to already-completed batches — because every
  batch's `fact_sales` writes happen in their own committed transaction,
  data from prior successful batches is never rolled back or lost.
- A single bad **row** never fails the batch: only `error_mode =
  "fail_fast"` (opt-in, off by default) turns a quarantined row into a
  hard failure. The default `error_mode = "quarantine"` always keeps the
  pipeline running.

## 8. Requirement checklist

| Requirement | Status | Location |
|---|---|---|
| `PipelineConfig` `@dataclass` (input path, output db, batch list, error mode) | ✅ Done | `pipeline.py` → `PipelineConfig` |
| Extract customers/products/orders with Pandas | ✅ Done | `extract_customers`, `extract_products`, `extract_orders_batch` |
| try/except + logging (batch name, row count, start/end time) | ✅ Done | `extract_sheet`, `process_batch` |
| Read every batch | ✅ Done | `run_pipeline` loop over `config.batches` |
| Never edit source file | ✅ Done | read-only `pd.read_excel` calls only |
| Safe date/number conversion (`errors="coerce"`) | ✅ Done | `safe_datetime`, `safe_numeric`, `clean_currency_string` |
| Normalize `payment_method` / `sales_channel` with documented mapping | ✅ Done | `PAYMENT_METHOD_MAP`, `SALES_CHANNEL_MAP`, `normalize_category` |
| Validate `quantity>0`, `unit_price>0`, `discount_pct` 0–100 | ✅ Done | `clean_and_validate_orders` |
| Validate `customer_id`/`product_id` referential integrity | ✅ Done | `clean_and_validate_orders` (`CUSTOMER_NOT_FOUND`, `PRODUCT_NOT_FOUND`) |
| Deduplicate by `order_id`, keep latest `updated_at` | ✅ Done | `clean_and_validate_orders` dedup step |
| `gross_amount` / `net_amount` | ✅ Done | `clean_and_validate_orders` |
| Clean vs quarantine split with `reason_code` | ✅ Done | `clean_and_validate_orders`, `QuarantineSink` |
| SQLite, ≥4 tables (star schema) | ✅ Done | `SCHEMA_SQL` — `dim_customer`, `dim_product`, `dim_date`, `fact_sales` |
| Grain = one validated line item per `order_id` | ✅ Done | `fact_sales.order_id PRIMARY KEY` |
| Minimum columns per table | ✅ Done | `SCHEMA_SQL` |
| Primary Key / Unique constraints | ✅ Done | `SCHEMA_SQL` |
| Transaction + upsert / insert-ignore | ✅ Done | `upsert_dim_customer`, `upsert_dim_product`, `ensure_dim_dates`, `load_fact_sales` (`with conn:` + `ON CONFLICT`) |
| Quarantine records with reason | ✅ Done | `quarantine.csv` |
| Idempotency (rerun batch_1, fact count unchanged) | ✅ Done | `main()` runs 1 & 2; see §6 |
| Incremental loading (only new/newer rows) | ✅ Done | `load_fact_sales` `ON CONFLICT ... WHERE excluded.updated_at > ...` |
| `pipeline_run_log` / watermark table | ✅ Done | `pipeline_run_log` table + `write_run_log` |
| Evidence of ≥4 runs (batch1, batch1 again, batch2, batch3) | ✅ Done | `main()`; `pipeline_run_log.csv` |
| `run_pipeline(config)` orchestration (extract→transform→validate→load) | ✅ Done | `run_pipeline` |
| Row failure → quarantine; batch/file failure → logged `failed`, no data loss | ✅ Done | `process_batch` try/except |
| KPI summary (read/valid/rejected/duplicated/loaded/net sales) | ✅ Done | `print_kpi_summary` |
| Reflection 5–8 lines | ✅ Done | §9 below |
| Clean/modular code, functions, comments, type hints, error handling, readable logging, meaningful names | ✅ Done | throughout `pipeline.py` |
| Deliverables: `pipeline.py`, `retail_dw.db`, `quarantine.csv`, `pipeline_run_log.csv`, `README.md` | ✅ Done | see §3 |

## 9. Reflection — why Availability matters more than Strictness in production

In a production pipeline, one bad row should never be allowed to take down
the entire nightly load — that is why every order is validated
independently and routed to `quarantine.csv` instead of raising an
exception that aborts the whole batch. A pipeline that halts on the first
malformed date or missing customer ID stops delivering *any* value to
downstream dashboards, even though 90%+ of the batch was perfectly fine.
Strict, all-or-nothing validation optimizes for correctness of a single
run at the cost of the business simply not having numbers to look at.
Availability-first design accepts that real-world data is messy by
default, and treats data quality as an ongoing, measurable KPI (rows
rejected, duplicated, loaded) rather than a pass/fail gate. Quarantining
with a `reason_code` also keeps the failure *actionable* — an analyst can
fix the source system or re-submit a corrected row later, and the
watermark-based upsert (`updated_at`) will pick it up automatically on
the next run without any manual reprocessing. Strictness still matters,
but it belongs inside the quarantine/alerting layer, not as a trigger
that stops the pipeline from running at all.
