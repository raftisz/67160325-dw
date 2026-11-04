# โน้ต: ออกแบบ Star Schema (เตรียมสอบ Data Warehouse)

> โน้ตทบทวนก่อนสอบ เขียนเพิ่มทีหลัง ไม่ใช่ส่วนหนึ่งของงาน week09 ที่ส่งตรวจ
> (รายการไฟล์ที่ส่งตรวจอยู่ใน `README_SUBMIT.md`)
>
> **แนวข้อสอบ:** ได้ข้อมูล 2 หน้า A4 → ออกแบบ Star Schema ที่เหมาะสม
> **ส่วนที่ 1** = ออกแบบ (ใช้สอบจริง) · **ภาคผนวก** = SQL สร้างตาราง (เผื่อไว้ ไม่ต้องใช้ในข้อสอบ)

---

# ส่วนที่ 1 — ออกแบบ Star Schema

## 1. ขั้นตอน (Kimball 4 ขั้น) — ทำตามลำดับนี้ทุกครั้ง

1. **Business process** — เหตุการณ์ที่เกิดซ้ำๆ คืออะไร → ตั้งชื่อ `fact_<เหตุการณ์>`
   (ขาย → fact_sales · จองห้อง → fact_booking · เข้ารักษา → fact_visit)
2. **Grain** — 1 แถวใน fact หมายถึงอะไร → **ต้องเขียนเป็นประโยคทุกครั้ง**
   เลือกระดับที่ละเอียดที่สุดที่ข้อมูลมี (รวมทีหลังได้ แต่แตกกลับไม่ได้)
3. **Dimensions** — ตอบคำถาม 5W1H
4. **Facts (measures)** — ตัวเลขที่เอามาบวก/เฉลี่ยได้

## 2. แยก Fact / Dimension จากข้อมูล 2 หน้า

ไล่ทีละคอลัมน์แล้วถาม **"เอามาบวกหรือเฉลี่ยได้ไหม และเกิดขึ้นทุกครั้งที่มีธุรกรรมไหม"**

| ถ้าเป็น... | จัดเป็น | ตัวอย่าง |
|---|---|---|
| ตัวเลขที่เกิดจากธุรกรรมแต่ละครั้ง | **Fact (measure)** | quantity, amount, nights |
| คำบรรยาย ใคร / อะไร / ที่ไหน / ช่องทางไหน | **Dimension** | customer_name, category, province, channel |
| ตัวเลขประจำตัวสิ่งของ (ไม่เปลี่ยนตามบิล) | **Dimension** | list_price, unit_cost, room_floor |
| วันที่ / เวลา | **dim_date** (แยกเสมอ) | order_date, check_in_date |
| เลขอ้างอิงธุรกรรม | เก็บใน fact (**degenerate dimension**) | order_id, invoice_no |
| สถานะ (Completed / Cancelled) | ใช้กรอง หรือทำ dim_status | status |

**5W1H → Dimension**

| คำถาม | Dimension | ตัวอย่าง attribute |
|---|---|---|
| ใคร | dim_customer / dim_guest / dim_patient | name, gender, province, segment |
| อะไร | dim_product / dim_room / dim_service | name, category, brand, price |
| เมื่อไหร่ | dim_date | day, month, month_name, quarter, year, day_of_week, is_weekend |
| ที่ไหน | dim_store / dim_branch / dim_location | branch, province, region |
| อย่างไร / ช่องทาง | dim_channel / dim_payment | channel_name, payment_method |
| ใครเป็นคนขาย | dim_employee | name, position |

## 3. Key และความสัมพันธ์

- ทุก dim มี **PK = surrogate key** (เลขรัน 1, 2, 3 …) เช่น `customer_key`
  และเก็บรหัสเดิมไว้เป็น **natural key** เช่น `customer_id`
- dim_date ใช้ key แบบ **YYYYMMDD** เช่น 20260715
- fact มี **FK ชี้ไปทุก dim** · PK ของ fact = เลขบิล (หรือ เลขบิล + ลำดับบรรทัด)
- ความสัมพันธ์ **dim 1 : N fact** (ลูกค้า 1 คน มีได้หลายรายการขาย)
- **dim ห้ามลากเส้นหากันเอง** → ถ้าลาก = Snowflake ไม่ใช่ Star

**ทำไมใช้ surrogate key:** รหัสต้นทางเปลี่ยนก็ไม่กระทบ, เก็บประวัติได้ (SCD Type 2 เช่นลูกค้าย้ายจังหวัด), JOIN ด้วยตัวเลขเร็วกว่า

## 4. Measure — ระบุประเภทด้วย (ได้คะแนนเพิ่ม)

| ประเภท | ความหมาย | ตัวอย่าง |
|---|---|---|
| **Additive** | บวกได้ทุกมิติ | quantity, sales_amount, cost, profit |
| **Semi-additive** | บวกได้บางมิติ (บวกข้ามเวลาไม่ได้) | ยอดคงเหลือ, stock คงคลัง |
| **Non-additive** | บวกไม่ได้ ต้องใช้เฉลี่ย/คำนวณใหม่ | unit_price, discount_pct, อัตรากำไร % |

measure ที่คำนวณเพิ่มได้ (derived): `net = qty × price × (1 − disc/100)` · `cost = qty × unit_cost` · `profit = net − cost`

## 5. ตัวอย่างทำจริง — Coast Café

**ข้อมูล:** orders (order_id, order_date, customer_id, product_id, quantity, unit_price, discount_pct, channel, status) ·
customers (customer_id, customer_name, province, segment) · products (product_id, product_name, category, list_price, unit_cost)

1. Process = การขาย → `fact_sales`
2. Grain = **1 แถว = สินค้า 1 รายการ ในคำสั่งซื้อ 1 ใบ (เฉพาะ status = Completed)**
3. Dim = dim_customer, dim_product, dim_date, dim_channel
4. Fact = quantity, unit_price, discount_pct, net_sales, cost_amount, profit

```
                 dim_date
                 PK date_key
                 full_date, day, month, quarter, year, day_of_week, is_weekend
                      | 1
                      | N
dim_customer   1    fact_sales                     N   1   dim_product
PK customer_key ---- PK order_id                     ---- PK product_key
customer_id          FK date_key                          product_id
customer_name        FK customer_key                      product_name
province             FK product_key                       category
segment              FK channel_key                       list_price, unit_cost
                     quantity        (additive)
                     unit_price      (non-additive)
                     discount_pct    (non-additive)
                     net_sales       (additive)
                     cost_amount     (additive)
                     profit          (additive)
                      | N
                      | 1
                 dim_channel
                 PK channel_key
                 channel_name (Store / Delivery / Online)
```

**คำอธิบาย (3–5 บรรทัด):**
Star Schema นี้ออกแบบสำหรับกระบวนการขาย มี fact_sales เป็นศูนย์กลาง grain คือ 1 แถวต่อสินค้า 1 รายการในคำสั่งซื้อ
เก็บ measure ได้แก่ quantity, net_sales, cost และ profit ล้อมรอบด้วย 4 dimension คือ ลูกค้า สินค้า วันที่ และช่องทาง
เชื่อมกับ fact ผ่าน surrogate key แบบ 1:N ทำให้วิเคราะห์ยอดขายตามกลุ่มลูกค้า ประเภทสินค้า ช่วงเวลา และช่องทางได้
นับรายได้เฉพาะ status = Completed

**ตรวจว่าตอบคำถามธุรกิจได้:**

| คำถาม | Fact | Dim |
|---|---|---|
| ช่องทางไหนรายได้ดีสุด | SUM(net_sales) | dim_channel |
| สินค้าไหนควรผลักดัน | SUM(profit) | dim_product |
| ลูกค้ากลุ่มไหนควรได้แคมเปญ | SUM(net_sales), COUNT | dim_customer |
| ยอดตามเดือน / วันหยุด | SUM(net_sales) | dim_date |

## 6. ตัวอย่างที่ 2 — โรงแรม (เปลี่ยนแค่เนื้อใน โครงเหมือนเดิม)

- Process = การจองห้องพัก → `fact_booking`
- Grain = 1 แถว = การจอง 1 ห้อง ใน 1 booking
- Dim = dim_guest (ใคร), dim_room (อะไร), dim_date (วันเช็กอิน), dim_channel (Walk-in / Agoda / Booking.com)
- Fact = nights, room_rate (non-additive), total_amount, discount

## 7. Star vs Snowflake (เผื่อถาม)

| | Star | Snowflake |
|---|---|---|
| โครง | dim ต่อ fact ชั้นเดียว | dim แตกย่อยอีกชั้น (เช่น dim_product → dim_category) |
| ข้อดี | JOIN น้อย query ง่าย เร็ว เหมาะ BI | ข้อมูลไม่ซ้ำซ้อน ประหยัดพื้นที่ |
| ข้อเสีย | ข้อมูลใน dim ซ้ำบ้าง | JOIN เยอะ ช้ากว่า |

## 8. ข้อผิดพลาดที่เสียคะแนนบ่อย

1. ไม่เขียน Grain
2. ใส่ข้อความ (เช่น customer_name) ไว้ใน fact → fact มีแค่ key + ตัวเลข
3. ไม่มี dim_date / เก็บวันที่เป็นข้อความใน fact อย่างเดียว
4. ไม่ระบุ PK / FK หรือไม่มีเส้น 1:N
5. ลากเส้นระหว่าง dim กันเอง
6. ก๊อปคอลัมน์ที่ข้อมูลโจทย์ไม่มี

## 9. เช็กลิสต์ก่อนส่ง

- [ ] เขียน **Grain** แล้ว
- [ ] fact มีแค่ key + ตัวเลข
- [ ] มี `dim_date`
- [ ] ระบุ PK / FK และเส้น 1:N (dim = 1, fact = N)
- [ ] dim ไม่ลากเส้นหากันเอง
- [ ] ระบุ measure ว่า Additive / Non-additive
- [ ] เขียนเงื่อนไขกรอง (ถ้ามี) เช่น เฉพาะ Completed
- [ ] คำอธิบาย 3–5 บรรทัด + ตารางคำถามธุรกิจ

---

# ภาคผนวก — สร้างตารางด้วย SQL (DuckDB) · ไม่ต้องใช้ในข้อสอบออกแบบ

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

