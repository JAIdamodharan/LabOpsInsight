"""Generate a synthetic e-commerce DuckDB with a planted, known revenue drop.

Ground truth (used by the eval harness):
  In March 2025 the `mobile_app` channel lost ~65% of its orders in the `West`
  region (a bad app release), which drags total March revenue down ~15-20%.
  Other channels/regions are unaffected, apart from normal seasonality noise.
"""
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb

SEED = 7
REGIONS = ["West", "East", "Central", "South"]
CHANNELS = ["web", "mobile_app", "marketplace"]
CATEGORIES = {
    "Electronics": (80, 900),
    "Home": (15, 250),
    "Apparel": (12, 140),
    "Beauty": (6, 70),
    "Sports": (10, 300),
}
START, END = date(2024, 10, 1), date(2025, 6, 30)


def build(db_path: Path) -> None:
    rnd = random.Random(SEED)
    tmp = db_path.parent / "_tmp"
    tmp.mkdir(exist_ok=True)

    customers = []
    for cid in range(1, 4001):
        customers.append((cid, rnd.choices(REGIONS, [0.3, 0.3, 0.2, 0.2])[0],
                          START - timedelta(days=rnd.randint(0, 700))))
    products = []
    pid = 1
    for cat, (lo, hi) in CATEGORIES.items():
        for _ in range(40):
            products.append((pid, cat, round(rnd.uniform(lo, hi), 2)))
            pid += 1
    cust_region = {c[0]: c[1] for c in customers}

    orders, items = [], []
    oid = 1
    day = START
    while day <= END:
        base = 140 + (25 if day.weekday() >= 5 else 0) + (40 if day.month in (11, 12) else 0)
        for _ in range(int(base * rnd.uniform(0.9, 1.1))):
            cid = rnd.randint(1, 4000)
            channel = rnd.choices(CHANNELS, [0.45, 0.35, 0.2])[0]
            region = cust_region[cid]
            # planted incident
            if (channel == "mobile_app" and region == "West"
                    and date(2025, 3, 1) <= day <= date(2025, 3, 31) and rnd.random() < 0.65):
                continue
            status = rnd.choices(["completed", "cancelled", "returned"], [0.9, 0.06, 0.04])[0]
            orders.append((oid, cid, day.isoformat(), channel, status))
            for _ in range(rnd.choices([1, 2, 3, 4], [0.5, 0.3, 0.15, 0.05])[0]):
                p = rnd.choice(products)
                items.append((oid, p[0], rnd.choices([1, 2, 3], [0.8, 0.15, 0.05])[0], p[2]))
            oid += 1
        day += timedelta(days=1)

    for name, header, rows in [
        ("customers", ["customer_id", "region", "signup_date"], customers),
        ("products", ["product_id", "category", "price"], products),
        ("orders", ["order_id", "customer_id", "order_date", "channel", "status"], orders),
        ("order_items", ["order_id", "product_id", "quantity", "unit_price"], items),
    ]:
        with open(tmp / f"{name}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    db_path.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE customers(customer_id INTEGER PRIMARY KEY, region VARCHAR, signup_date DATE)")
    con.execute("CREATE TABLE products(product_id INTEGER PRIMARY KEY, category VARCHAR, price DECIMAL(10,2))")
    con.execute("CREATE TABLE orders(order_id INTEGER PRIMARY KEY, customer_id INTEGER, order_date DATE, channel VARCHAR, status VARCHAR)")
    con.execute("CREATE TABLE order_items(order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price DECIMAL(10,2))")
    for name in ["customers", "products", "orders", "order_items"]:
        con.execute(f"COPY {name} FROM '{tmp / (name + '.csv')}' (HEADER)")
    con.close()
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()
    print(f"wrote {db_path}: {len(orders)} orders, {len(items)} line items")


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "data/shop.duckdb"))
