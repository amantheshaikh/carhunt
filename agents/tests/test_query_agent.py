import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from query_agent import run, _query_db


def test_query_db_sql_escaping():
    with patch("query_agent.fetch_all") as mock_fetch:
        mock_fetch.return_value = [{"col": "val"}]
        # Test normal query
        _query_db("SELECT * FROM listings")
        mock_fetch.assert_called_with("SELECT * FROM listings")
        
        # Test wildcard query (should double %)
        _query_db("SELECT * FROM listings WHERE trim ILIKE '%convenience%'")
        mock_fetch.assert_called_with("SELECT * FROM listings WHERE trim ILIKE '%%convenience%%'")


def test_query_db_forbidden_keywords():
    res = _query_db("DROP TABLE listings")
    assert "forbidden" in res or "Only SELECT" in res


def test_run_with_single_question():
    with patch("query_agent.client.chat.completions.create") as mock_create:
        mock_msg = MagicMock()
        mock_msg.content = "Here are the listings."
        mock_msg.tool_calls = None
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_choice.finish_reason = "stop"
        mock_create.return_value.choices = [mock_choice]
        
        result = run("show cars")
        assert result["answer"] == "Here are the listings."
        
        messages_called = mock_create.call_args[1]["messages"]
        assert len(messages_called) == 3
        assert messages_called[0]["role"] == "system"
        assert messages_called[1]["role"] == "user"
        assert messages_called[1]["content"] == "show cars"
        assert messages_called[2]["role"] == "assistant"
        assert messages_called[2]["content"] == "Here are the listings."


def test_run_with_history():
    with patch("query_agent.client.chat.completions.create") as mock_create:
        mock_msg = MagicMock()
        mock_msg.content = "It is cheaper."
        mock_msg.tool_calls = None
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_choice.finish_reason = "stop"
        mock_create.return_value.choices = [mock_choice]
        
        history = [
            {"role": "user", "content": "which cars have technology package?"},
            {"role": "assistant", "content": "Here are 4 active BMW X1s: ..."},
            {"role": "user", "content": "which of those is the cheapest?"}
        ]
        result = run(history)
        assert result["answer"] == "It is cheaper."
        
        messages_called = mock_create.call_args[1]["messages"]
        assert len(messages_called) == 5  # system + 3 history turns + 1 final assistant turn
        assert messages_called[0]["role"] == "system"
        assert messages_called[1]["role"] == "user"
        assert messages_called[1]["content"] == "which cars have technology package?"
        assert messages_called[2]["role"] == "assistant"
        assert messages_called[3]["role"] == "user"
        assert messages_called[3]["content"] == "which of those is the cheapest?"
        assert messages_called[4]["role"] == "assistant"
        assert messages_called[4]["content"] == "It is cheaper."
