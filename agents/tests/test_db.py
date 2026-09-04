import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock

import tools.db as db


def _mock_conn_with_cursor():
    """Build a MagicMock conn whose `with conn: with conn.cursor() as cur:` chain works."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_conn.__enter__.return_value = mock_conn
    return mock_conn, mock_cur


def test_fetch_one_returns_row_and_closes_conn():
    mock_conn, mock_cur = _mock_conn_with_cursor()
    mock_cur.fetchone.return_value = {"id": 1}
    with patch.object(db, "get_conn", return_value=mock_conn):
        result = db.fetch_one("SELECT 1", (1,))
    assert result == {"id": 1}
    mock_cur.execute.assert_called_once_with("SELECT 1", (1,))
    mock_conn.close.assert_called_once()


def test_fetch_one_closes_conn_even_on_exception():
    mock_conn, mock_cur = _mock_conn_with_cursor()
    mock_cur.execute.side_effect = RuntimeError("boom")
    with patch.object(db, "get_conn", return_value=mock_conn):
        try:
            db.fetch_one("SELECT 1")
        except RuntimeError:
            pass
    mock_conn.close.assert_called_once()


def test_fetch_all_returns_rows():
    mock_conn, mock_cur = _mock_conn_with_cursor()
    mock_cur.fetchall.return_value = [{"id": 1}, {"id": 2}]
    with patch.object(db, "get_conn", return_value=mock_conn):
        result = db.fetch_all("SELECT * FROM t")
    assert result == [{"id": 1}, {"id": 2}]
    mock_conn.close.assert_called_once()


def test_execute_runs_and_closes():
    mock_conn, mock_cur = _mock_conn_with_cursor()
    with patch.object(db, "get_conn", return_value=mock_conn):
        result = db.execute("UPDATE t SET x = 1", (1,))
    assert result is None
    mock_cur.execute.assert_called_once_with("UPDATE t SET x = 1", (1,))
    mock_conn.close.assert_called_once()


def test_execute_returning_returns_row():
    mock_conn, mock_cur = _mock_conn_with_cursor()
    mock_cur.fetchone.return_value = {"id": 42}
    with patch.object(db, "get_conn", return_value=mock_conn):
        result = db.execute_returning("INSERT INTO t (x) VALUES (%s) RETURNING id", (1,))
    assert result == {"id": 42}


def test_execute_values_noop_on_empty_list():
    with patch.object(db, "get_conn") as mock_get_conn:
        db.execute_values("INSERT INTO t (x) VALUES %s", [])
    mock_get_conn.assert_not_called()


def test_execute_values_calls_psycopg2_execute_values():
    mock_conn, mock_cur = _mock_conn_with_cursor()
    with patch.object(db, "get_conn", return_value=mock_conn), \
         patch.object(db.psycopg2.extras, "execute_values") as mock_ev:
        db.execute_values("INSERT INTO t (x) VALUES %s", [(1,), (2,)], template="(%s)")
    mock_ev.assert_called_once()
    args, kwargs = mock_ev.call_args
    assert args[0] is mock_cur
    assert args[1] == "INSERT INTO t (x) VALUES %s"
    assert args[2] == [(1,), (2,)]
    assert kwargs["template"] == "(%s)"
    mock_conn.close.assert_called_once()


def test_refresh_self_computed_medians_upserts_and_returns_count():
    rows = [
        {"make": "bmw", "model": "x3", "year": 2023, "mileage_bucket": "0-10k", "median_price": 40000, "sample_size": 3},
        {"make": "audi", "model": "q5", "year": 2024, "mileage_bucket": "10k-20k", "median_price": 38000, "sample_size": 2},
    ]
    with patch.object(db, "fetch_all", return_value=rows) as mock_fetch_all, \
         patch.object(db, "execute_values") as mock_execute_values:
        count = db.refresh_self_computed_medians()

    assert count == 2
    mock_fetch_all.assert_called_once()
    mock_execute_values.assert_called_once()
    _, values_arg = mock_execute_values.call_args[0][0], mock_execute_values.call_args[0][1]
    assert values_arg == [
        ("bmw", "x3", 2023, "0-10k", 40000),
        ("audi", "q5", 2024, "10k-20k", 38000),
    ]


def test_refresh_self_computed_medians_no_rows():
    with patch.object(db, "fetch_all", return_value=[]), \
         patch.object(db, "execute_values") as mock_execute_values:
        count = db.refresh_self_computed_medians()
    assert count == 0
    mock_execute_values.assert_called_once_with(
        mock_execute_values.call_args[0][0], [], template=mock_execute_values.call_args[1]["template"]
    )
