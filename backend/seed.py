import os
import time

import psycopg2

from rules import weigh


def connect():
    last = None
    for _ in range(30):
        try:
            return psycopg2.connect(os.environ["DATABASE_URL"])
        except psycopg2.OperationalError as exc:
            last = exc
            time.sleep(1)
    raise last


def main():
    conn = connect()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS cuppings (
            id serial PRIMARY KEY,
            lot text NOT NULL,
            aroma double precision NOT NULL,
            taste double precision NOT NULL,
            liquor double precision NOT NULL,
            score double precision NOT NULL,
            verdict text NOT NULL,
            note text NOT NULL,
            created_by text NOT NULL
        )"""
    )
    # 已存在的旧库补列：勾选原文随审评行存档
    cur.execute(
        "ALTER TABLE cuppings ADD COLUMN IF NOT EXISTS selected_terms text[] NOT NULL DEFAULT '{}'"
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS aroma_terms (
            id serial PRIMARY KEY,
            term text NOT NULL UNIQUE,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS app_settings (
            key text PRIMARY KEY,
            value text NOT NULL
        )"""
    )
    cur.execute(
        "INSERT INTO app_settings (key, value) VALUES ('min_terms', '2') "
        "ON CONFLICT (key) DO NOTHING"
    )
    cur.execute("SELECT COUNT(*) FROM aroma_terms")
    if cur.fetchone()[0] == 0:
        for term in ("板栗", "花香"):
            cur.execute(
                "INSERT INTO aroma_terms (term, created_by) VALUES (%s, %s)",
                (term, "taster"),
            )
    cur.execute("SELECT COUNT(*) FROM cuppings")
    if cur.fetchone()[0] == 0:
        for lot, aroma, taste, liquor in (("春茶-A", 8, 8, 7), ("夏茶-C", 5, 4, 6)):
            verdict, note, score = weigh(aroma, taste, liquor)
            cur.execute(
                """INSERT INTO cuppings (lot, aroma, taste, liquor, score, verdict, note, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (lot, aroma, taste, liquor, score, verdict, note, "taster"),
            )
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
