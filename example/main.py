# A complete program that uses vsql:
#
#   python example/main.py

import datetime
import decimal

import vsql

with vsql.connect(":memory:") as conn:
    cur = conn.cursor()
    cur.execute("""CREATE TABLE products (
        id INTEGER NOT NULL,
        name VARCHAR(50) NOT NULL,
        price NUMERIC(10, 2),
        added DATE)""")
    cur.executemany(
        "INSERT INTO products (id, name, price, added) VALUES (?, ?, ?, ?)",
        [
            (1, "Bolt", decimal.Decimal("0.25"), datetime.date(2024, 1, 2)),
            (2, "Nut", decimal.Decimal("0.10"), datetime.date(2024, 3, 4)),
            (3, "Washer", None, None),
        ],
    )
    conn.commit()

    cur.execute("SELECT name, price, added FROM products WHERE id > ? ORDER BY id", [1])
    print([d[0] for d in cur.description])
    for name, price, added in cur:
        print(name, price, added)

    try:
        cur.execute("SELECT * FROM nope")
    except vsql.ProgrammingError as e:
        print(f"{e.sqlstate}: {e.message}")
