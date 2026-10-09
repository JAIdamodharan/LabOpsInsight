"""Scripted stand-in for the model, for UI development and offline demos.

It issues real tool calls (real SQL, real sandboxed Python) in the order a good analyst would,
and writes its final answer from the actual query results. Only the *decisions* are scripted.
"""
from types import SimpleNamespace as NS

from .db import Database


def _tu(i, name, **inp):
    return NS(type="tool_use", id=f"demo{i}", name=name, input=inp)


class DemoClient:
    def __init__(self, db: Database, with_python: bool):
        self.db, self.with_python, self.i = db, with_python, 0
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        i, self.i = self.i, self.i + 1
        usage = NS(input_tokens=1200 + 900 * i, output_tokens=150)
        run = lambda sql: self.db.run(sql).rows
        steps = [
            lambda: [_tu(i, "get_schema")],
            lambda: [_tu(i, "run_sql", sql="""SELECT strftime(order_date,'%Y-%m') AS month, round(sum(quantity*unit_price)) AS revenue
FROM orders JOIN order_items USING(order_id)
WHERE status='completed' AND order_date BETWEEN '2025-01-01' AND '2025-05-31'
GROUP BY 1 ORDER BY 1""")],
            lambda: [_tu(i, "run_sql", sql="""SELECT channel, round(sum(CASE WHEN order_date<'2025-03-01' THEN quantity*unit_price END)/28,0) AS feb_rev_per_day,
       round(sum(CASE WHEN order_date>='2025-03-01' THEN quantity*unit_price END)/31,0) AS mar_rev_per_day
FROM orders JOIN order_items USING(order_id)
WHERE status='completed' AND order_date BETWEEN '2025-02-01' AND '2025-03-31'
GROUP BY 1 ORDER BY 1""")],
            lambda: [_tu(i, "run_sql", sql="""SELECT channel, region,
       sum((order_date<'2025-03-01')::int) AS feb_orders, sum((order_date>='2025-03-01')::int) AS mar_orders,
       round(sum((order_date>='2025-03-01')::int)/31.0 - sum((order_date<'2025-03-01')::int)/28.0, 1) AS per_day_change
FROM orders JOIN customers USING(customer_id)
WHERE order_date BETWEEN '2025-02-01' AND '2025-03-31'
GROUP BY 1,2 ORDER BY per_day_change LIMIT 5""")],
        ]
        if self.with_python:
            steps.append(lambda: [_tu(i, "run_python", code="""df = con.sql('''
  SELECT date_trunc('week', order_date) AS week, count(*) AS orders
  FROM orders JOIN customers USING(customer_id)
  WHERE channel='mobile_app' AND region IN ('West','East') AND order_date BETWEEN '2025-01-01' AND '2025-05-31'
  GROUP BY 1 ORDER BY 1''').df()
print(df.tail(12).to_string(index=False))
plt.figure(figsize=(8,3.5)); plt.plot(df.week, df.orders, marker='o')
plt.axvspan(pd.Timestamp('2025-03-01'), pd.Timestamp('2025-03-31'), alpha=.15, color='red')
plt.title('Weekly mobile_app orders, West + East'); plt.ylabel('orders'); plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'mobile_west_east_weekly.png'), dpi=130)""")])
        if i < len(steps):
            return NS(stop_reason="tool_use", content=steps[i](), usage=usage)
        m = dict(run("SELECT strftime(order_date,'%Y-%m'), round(sum(quantity*unit_price)) FROM orders JOIN order_items USING(order_id) WHERE status='completed' AND order_date BETWEEN '2025-02-01' AND '2025-03-31' GROUP BY 1 ORDER BY 1"))
        feb, mar = float(m["2025-02"]), float(m["2025-03"])
        top = run("SELECT channel, region, round(sum((order_date>='2025-03-01')::int)/31.0 - sum((order_date<'2025-03-01')::int)/28.0, 1) d FROM orders JOIN customers USING(customer_id) WHERE order_date BETWEEN '2025-02-01' AND '2025-03-31' GROUP BY 1,2 ORDER BY d LIMIT 2")
        text = (f"**Revenue fell {abs(mar-feb)/feb:.1%} from February to March 2025 (${feb:,.0f} → ${mar:,.0f}), and the loss is concentrated in the mobile_app channel in the {top[0][1]} and {top[1][1]} regions.**\n\n"
                f"- {top[0][0]} / {top[0][1]}: {top[0][2]:+} orders per day vs. February; {top[1][0]} / {top[1][1]}: {top[1][2]:+}.\n"
                "- Web and marketplace revenue per day were flat or up overall, and no other channel/region pair shows a comparable decline.\n\n"
                "**Caveats:** This shows *where* the drop happened, not *why*; the data has no release, outage or campaign log. "
                "A sudden, channel-specific step change like this is consistent with a technical issue in the mobile app, but that is an inference. "
                "Revenue is completed orders only; February has 28 days, so I compared per-day rates.\n\n*(Demo mode: scripted model, real queries.)*")
        return NS(stop_reason="end_turn", content=[NS(type="text", text=text)], usage=usage)
