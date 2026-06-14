import os
import psycopg2
import psycopg2.extras

psycopg2.extras.register_uuid()


def get_conn():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def fetch_one(sql: str, params=()) -> dict | None:
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()
    finally:
        conn.close()


def fetch_all(sql: str, params=()) -> list[dict]:
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
    finally:
        conn.close()


def execute(sql: str, params=()) -> None:
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
    finally:
        conn.close()


def execute_values(sql: str, values: list[tuple], template: str | None = None) -> None:
    if not values:
        return
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, sql, values, template=template)
    finally:
        conn.close()


def execute_returning(sql: str, params=()) -> dict | None:
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()
    finally:
        conn.close()


def refresh_self_computed_medians() -> int:
    """
    Compute median price per (make, model, year, mileage_bucket) from both
    live listings and the broader price_training_data corpus, then upsert
    into market_price_cache with source='scraped'.
    Requires at least 2 data points per group.
    Returns number of cache rows written.
    """
    rows = fetch_all(
        """
        WITH combined AS (
            SELECT lower(make) AS make, lower(model) AS model, year, mileage, price
            FROM listings
            WHERE price IS NOT NULL AND mileage IS NOT NULL
              AND status NOT IN ('dismissed')
            UNION ALL
            SELECT lower(make), lower(model), year, mileage, price
            FROM price_training_data
            WHERE price IS NOT NULL AND mileage IS NOT NULL
        )
        SELECT
            make, model, year,
            CASE
                WHEN mileage <= 10000 THEN '0-10k'
                WHEN mileage <= 20000 THEN '10k-20k'
                ELSE '20k-30k'
            END AS mileage_bucket,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price)::int AS median_price,
            COUNT(*) AS sample_size
        FROM combined
        GROUP BY 1, 2, 3, 4
        HAVING COUNT(*) >= 2
        """
    )

    execute_values(
        """INSERT INTO market_price_cache
               (make, model, year, mileage_bucket, median_price, fetched_at)
           VALUES %s
           ON CONFLICT (make, model, year, mileage_bucket)
           DO UPDATE SET
               median_price = EXCLUDED.median_price,
               fetched_at   = now()""",
        [(r["make"], r["model"], r["year"], r["mileage_bucket"], r["median_price"]) for r in rows],
        template="(%s, %s, %s, %s, %s, now())",
    )

    return len(rows)
