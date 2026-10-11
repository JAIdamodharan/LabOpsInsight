"""A small example workbook so users can try the upload flow (and see the generic data review find real problems)."""
import datetime as dt
import io
import random

from openpyxl import Workbook


def build_example_workbook() -> bytes:
    rnd = random.Random(5)
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["Order ID", "Order Date", "Region", "Product", "Units", "Unit Price", "Discount %", "Status", "Notes"])
    products = {"Widget": 12.5, "Gadget": 24.0, "Gizmo": 49.9, "Doohickey": 7.25}
    rows = []
    for i in range(1, 401):
        prod = rnd.choice(list(products))
        region = rnd.choices(["North", "South", "East", "West"], [.3, .3, .2, .2])[0]
        month_boost = 1.5 if (i * 7) % 12 in (10, 11) else 1.0
        units = max(1, int(rnd.gauss(8 * month_boost, 3)))
        rows.append([i, dt.datetime(2025, 1, 1) + dt.timedelta(days=int(i * 0.45)), region, prod, units, round(products[prod] * rnd.uniform(.95, 1.05), 2),
                     str(rnd.choice([0, 5, 10, 15])), rnd.choices(["Shipped", "Returned", "Cancelled"], [.88, .07, .05])[0], "gift order" if i % 9 == 0 else None])
    rows[10][2] = "north "          # inconsistent spelling
    rows[20][4] = -2                # negative units
    rows[30][5] = 4990.0            # price typo (x100)
    rows[40][6] = "N/A"             # text in a numeric-looking column
    rows.append(rows[3][:])         # two exact duplicate rows
    rows.append(rows[3][:])
    for r in rows:
        ws.append(r)
    t = wb.create_sheet("Targets")
    t.append(["Region", "Month", "Target Revenue"])
    for region in ["North", "South", "East", "West"]:
        for m in range(1, 7):
            t.append([region, dt.datetime(2025, m, 1), 4000 + 500 * m + rnd.randint(-300, 300)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
