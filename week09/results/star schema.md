# โน้ต: สร้างตาราง Star Schema ด้วย SQL (DuckDB)

> โน้ตทบทวนก่อนสอบ เขียนเพิ่มทีหลัง ไม่ใช่ส่วนหนึ่งของงาน week09 ที่ส่งตรวจ
> (รายการไฟล์ที่ส่งตรวจอยู่ใน `README_SUBMIT.md`)
> ตัวอย่างใช้ข้อมูลร้านกาแฟ Coast Café: `orders`, `customers`, `products`

---

## ขั้นที่ 0 — อ่านข้อมูลก่อนเขียนโค้ด

ถามทีละคอลัมน์ว่า **"เอามาบวกหรือเฉลี่ยได้ไหม"**

| ถ้าเป็น... | จัดเป็น | ตัวอย่าง |
|---|---|---|
| ตัวเลขที่เกิดจากธุรกรรมแต่ละครั้ง | **Fact (measure)** | quantity, unit_price, discount_pct |
| คำบรรยาย ใคร / อะไร / เมื่อไหร่ / ที่ไหน / ช่องทางไหน | **Dimension** | customer_name, category, order_date, channel |
| เลขอ้างอิงของธุรกรรม | เก็บไว้ใน fact (degenerate dim) | order_id |

แล้วเขียน 4 ข้อนี้ก่อนลงมือ (Kimball 4 ขั้น)
1. **Business process:** การขาย → `fact_sales`
2. **Grain:** 1 แถว = สินค้า 1 รายการ ในคำสั่งซื้อ 1 ใบ
3. **Dimensions:** dim_customer, dim_product, dim_date, dim_channel
4. **Facts:** quantity, unit_price, discount_pct, net_sales, cost_amount, profit

---

## ขั้นที่ 1 — ชนิดข้อมูลที่ใช้ (DuckDB)

| ชนิด | ใช้กับ | ตัวอย่าง |
|---|---|---|
| `INTEGER` | จำนวนเต็ม, key | quantity, customer_key |
| `DECIMAL(12,2)` | เงิน (รวม 12 หลัก ทศนิยม 2 หลัก) | unit_price, net_sales |
| `VARCHAR` | ข้อความ (`TEXT` ก็ได้ เหมือนกัน) | customer_name |
| `DATE` | วันที่ | full_date |
| `BOOLEAN` | จริง/เท็จ | is_weekend |

⚠️ อย่าใช้ `REAL` กับเงินใน DuckDB เพราะละเอียดแค่ ~7 หลัก (1234567.89 จะเหลือ 1234567.9)

---

## ขั้นที่ 2 — สร้างโครงตาราง (ตารางเปล่า)

**สร้าง dim ก่อน fact เสมอ** เพราะ fact อ้างถึง dim (`REFERENCES`)

```sql
CREATE TABLE dim_customer (
  customer_key  INTEGER PRIMARY KEY,      -- surrogate key (เลขรัน)
  customer_id   VARCHAR NOT NULL,         -- natural key (รหัสเดิมจากระบบต้นทาง)
  customer_name VARCHAR,
  province      VARCHAR,
  segment       VARCHAR
);

CREATE TABLE dim_product (
  product_key  INTEGER PRIMARY KEY,
  product_id   VARCHAR NOT NULL,
  product_name VARCHAR,
  category     VARCHAR,
  list_price   DECIMAL(10,2),
  unit_cost    DECIMAL(10,2)
);

CREATE TABLE dim_date (
  date_key    INTEGER PRIMARY KEY,         -- รูปแบบ YYYYMMDD เช่น 20260715
  full_date   DATE NOT NULL,
  day         INTEGER,
  month       INTEGER,
  month_name  VARCHAR,
  quarter     INTEGER,
  year        INTEGER,
  day_of_week VARCHAR,
  is_weekend  BOOLEAN
);

CREATE TABLE dim_channel (
  channel_key  INTEGER PRIMARY KEY,
  channel_name VARCHAR NOT NULL
);

CREATE TABLE fact_sales (
  order_id     VARCHAR PRIMARY KEY,
  date_key     INTEGER NOT NULL REFERENCES dim_date(date_key),
  customer_key INTEGER REFERENCES dim_customer(customer_key),   -- ข้อมูลจริงมีแถวที่ไม่มีลูกค้า เลยไม่ใส่ NOT NULL
  product_key  INTEGER REFERENCES dim_product(product_key),
  channel_key  INTEGER NOT NULL REFERENCES dim_channel(channel_key),
  quantity     INTEGER NOT NULL CHECK (quantity > 0),
  unit_price   DECIMAL(10,2) NOT NULL CHECK (unit_price >= 0),
  discount_pct DECIMAL(5,2)  NOT NULL CHECK (discount_pct BETWEEN 0 AND 100),
  net_sales    DECIMAL(12,2),
  cost_amount  DECIMAL(12,2),
  profit       DECIMAL(12,2)
);

SHOW TABLES;
```

### ความหมายของแต่ละคำ

| คำ | หมายถึง |
|---|---|
| `PRIMARY KEY` | ห้ามซ้ำ ห้ามว่าง |
| `REFERENCES dim_x(key)` | FK — ค่าต้องมีอยู่จริงในตาราง dim |
| `NOT NULL` | ห้ามว่าง |
| `CHECK (เงื่อนไข)` | ถ้ามีค่า ค่าต้องตรงเงื่อนไข (**แต่ไม่กันค่าว่าง** — อยากกันทั้งคู่ต้องใส่ `NOT NULL CHECK (...)`) |

### `CREATE TABLE` แบบต่างๆ

| เขียน | ถ้ามีตารางชื่อนี้อยู่แล้ว |
|---|---|
| `CREATE TABLE x (...)` | error "already exists" |
| `CREATE TABLE IF NOT EXISTS x (...)` | ข้ามไปเฉยๆ (ตารางเก่าที่ผิดยังอยู่) |
| `CREATE OR REPLACE TABLE x (...)` | ลบของเดิมแล้วสร้างใหม่ ← **แนะนำตอนสอบ** แก้แล้วรันซ้ำได้เลย |

ถ้ามี FK ผูกอยู่แล้ว replace ไม่ได้ → ลบ fact ก่อน แล้วค่อยลบ dim:
```sql
DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS dim_customer;
```

---

## ขั้นที่ 3 — สร้างตารางพร้อมใส่ข้อมูลจาก CSV (ใช้เมื่อโจทย์ให้ไฟล์ข้อมูลมา)

ใช้ `CREATE OR REPLACE TABLE ... AS SELECT` สร้างตารางและเติมข้อมูลในคำสั่งเดียว

```sql
-- โหลด CSV (ถ้าอาจารย์ให้ 00_setup.sql มา ใช้ .read 00_setup.sql แทน)
CREATE OR REPLACE TABLE orders    AS SELECT * FROM read_csv_auto('orders.csv');
CREATE OR REPLACE TABLE customers AS SELECT * FROM read_csv_auto('customers.csv');
CREATE OR REPLACE TABLE products  AS SELECT * FROM read_csv_auto('products.csv');

-- Dimension: ดึงคอลัมน์บรรยาย + เพิ่มเลขรันเป็น surrogate key
CREATE OR REPLACE TABLE dim_customer AS
SELECT ROW_NUMBER() OVER (ORDER BY customer_id) AS customer_key,
       customer_id, customer_name, province, segment
FROM customers;

CREATE OR REPLACE TABLE dim_product AS
SELECT ROW_NUMBER() OVER (ORDER BY product_id) AS product_key,
       product_id, product_name, category, list_price, unit_cost
FROM products;

CREATE OR REPLACE TABLE dim_channel AS
SELECT ROW_NUMBER() OVER (ORDER BY channel) AS channel_key,
       channel AS channel_name
FROM (SELECT DISTINCT channel FROM orders);

-- dim_date: สร้างทุกวันในช่วงข้อมูล (แม้วันที่ไม่มีขาย)
CREATE OR REPLACE TABLE dim_date AS
SELECT CAST(strftime(d, '%Y%m%d') AS INTEGER) AS date_key,
       CAST(d AS DATE)       AS full_date,
       day(d)                AS day,
       month(d)              AS month,
       monthname(d)          AS month_name,
       quarter(d)            AS quarter,
       year(d)               AS year,
       dayname(d)            AS day_of_week,
       dayofweek(d) IN (0,6) AS is_weekend
FROM range(DATE '2026-07-01', DATE '2026-10-01', INTERVAL 1 DAY) t(d);

-- Fact: เอาธุรกรรมมา JOIN กับ dim เพื่อเปลี่ยนรหัสเป็น key + คำนวณ measure
-- (ตั้งชื่อ fact_sales_star เพราะ 00_setup.sql ของอาจารย์มี fact_sales อยู่แล้ว)
CREATE OR REPLACE TABLE fact_sales_star AS
SELECT o.order_id,
       CAST(strftime(o.order_date, '%Y%m%d') AS INTEGER)       AS date_key,
       c.customer_key, p.product_key, ch.channel_key,
       o.quantity, o.unit_price, o.discount_pct,
       o.quantity * o.unit_price * (1 - o.discount_pct/100.0)   AS net_sales,
       o.quantity * p.unit_cost                                 AS cost_amount,
       o.quantity * o.unit_price * (1 - o.discount_pct/100.0)
         - o.quantity * p.unit_cost                             AS profit
FROM orders o
LEFT JOIN dim_customer c  ON o.customer_id = c.customer_id
LEFT JOIN dim_product  p  ON o.product_id  = p.product_id
LEFT JOIN dim_channel  ch ON o.channel     = ch.channel_name
WHERE o.status = 'Completed';
```

> ใช้ `LEFT JOIN` เพื่อไม่ให้แถวหายเวลาข้อมูลไม่ครบ (เช่น customer_id ว่าง หรือ product_id = P99 ที่ไม่มีในตาราง products)
> แถวพวกนั้นจะได้ key = NULL → เขียนในรายงานว่า "ควรเพิ่มแถว Unknown (key = -1) ใน dim"

---

## ขั้นที่ 4 — ตรวจผล (แคปหน้าจอส่วนนี้แนบรายงาน)

```sql
-- จำนวนแถวต้องสมเหตุสมผล: fact ต้องเท่ากับจำนวน order ที่ Completed (ไม่หาย ไม่ซ้ำ)
SELECT 'dim_customer' AS tbl, COUNT(*) AS n FROM dim_customer
UNION ALL SELECT 'dim_product',     COUNT(*) FROM dim_product
UNION ALL SELECT 'dim_channel',     COUNT(*) FROM dim_channel
UNION ALL SELECT 'dim_date',        COUNT(*) FROM dim_date
UNION ALL SELECT 'fact_sales_star', COUNT(*) FROM fact_sales_star;

-- ทดสอบว่า schema ตอบคำถามธุรกิจได้: รายได้ตามช่องทาง
SELECT ch.channel_name,
       COUNT(*)                   AS orders,
       ROUND(SUM(f.net_sales), 2) AS revenue
FROM fact_sales_star f
JOIN dim_channel ch ON f.channel_key = ch.channel_key
GROUP BY ch.channel_name
ORDER BY revenue DESC;
```

---

## วาดรูป Star Schema (dbdiagram.io)

โค้ดนี้คือ **DBML** ไม่ใช่ SQL → ใช้วาดรูปอย่างเดียว เอาไปรันใน DuckDB ไม่ได้
วางใน dbdiagram.io → Export → PNG (หรือ Export → PostgreSQL เพื่อให้เว็บแปลงเป็น `CREATE TABLE`)

```
Table fact_sales {
  order_id varchar [pk]
  date_key int [ref: > dim_date.date_key]
  customer_key int [ref: > dim_customer.customer_key]
  product_key int [ref: > dim_product.product_key]
  channel_key int [ref: > dim_channel.channel_key]
  quantity int
  unit_price decimal
  discount_pct decimal
  net_sales decimal
  cost_amount decimal
  profit decimal
}
Table dim_customer {
  customer_key int [pk]
  customer_id varchar
  customer_name varchar
  province varchar
  segment varchar
}
Table dim_product {
  product_key int [pk]
  product_id varchar
  product_name varchar
  category varchar
  list_price decimal
  unit_cost decimal
}
Table dim_date {
  date_key int [pk]
  full_date date
  day int
  month int
  month_name varchar
  quarter int
  year int
  day_of_week varchar
  is_weekend boolean
}
Table dim_channel {
  channel_key int [pk]
  channel_name varchar
}
```

| DBML | SQL (DuckDB) |
|---|---|
| `int` | `INTEGER` |
| `decimal` | `DECIMAL(12,2)` |
| `[pk]` | `PRIMARY KEY` |
| `[ref: > dim_x.key]` | `REFERENCES dim_x(key)` |

---

## เอาโค้ดเก่า (Week7 / SQLite) มาใช้กับ DuckDB

- ลบ `AUTOINCREMENT` (DuckDB ไม่รองรับ)
- เปลี่ยน `REAL` → `DECIMAL(12,2)` สำหรับเงิน
- **เปลี่ยนชื่อตาราง + คอลัมน์ + measure + CHECK ให้ตรงกับข้อมูลโจทย์ใหม่** — ใช้ได้แค่ "โครง" ห้ามก๊อปคอลัมน์ที่โจทย์ไม่มี

ตัวอย่างโจทย์โรงแรม: `dim_guest`, `dim_room`, `dim_date`, `dim_channel` + `fact_booking (nights, room_rate, total_amount)`

---

## เช็กลิสต์ก่อนส่ง

- [ ] เขียน **Grain** แล้ว
- [ ] fact มีแค่ key + ตัวเลข (ไม่มีข้อความอย่าง customer_name)
- [ ] มี `dim_date`
- [ ] ระบุ PK / FK และเส้น 1:N (dim = 1, fact = N)
- [ ] dim ไม่ลากเส้นหากันเอง (ถ้าลาก = Snowflake ไม่ใช่ Star)
- [ ] ระบุ measure ว่า Additive / Non-additive (unit_price, discount_pct = non-additive)
- [ ] จำนวนแถว fact ตรงกับข้อมูลต้นทาง
- [ ] เขียนเงื่อนไขกรอง เช่น เฉพาะ `status = 'Completed'`
