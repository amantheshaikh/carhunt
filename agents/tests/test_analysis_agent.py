import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import analysis_agent


def _listing(id="11111111-1111-1111-1111-111111111111", price=30000, drivetrain="unknown"):
    return {"id": id, "price": price, "drivetrain": drivetrain, "status": "new", "make": "BMW", "model": "X3"}


def test_run_scores_listings_with_blended_price():
    rows = [_listing()]
    with patch.object(analysis_agent, "fetch_all", return_value=rows), \
         patch.object(analysis_agent, "resolve_drivetrain", return_value="unknown"), \
         patch.object(analysis_agent, "predict_fair_price", return_value=(30000, 2000.0)), \
         patch.object(analysis_agent, "predict_kbb_fair_price", return_value=32000), \
         patch.object(analysis_agent, "score_listing", return_value=55.0), \
         patch.object(analysis_agent, "explain_listing", return_value={"base": 1}), \
         patch.object(analysis_agent, "execute_values") as mock_ev, \
         patch.object(analysis_agent, "execute") as mock_execute, \
         patch.object(analysis_agent, "update_kbb_market_cache", return_value=1):
        result = analysis_agent.run()

    assert result == {"scored": 1}
    mock_ev.assert_called_once()
    score_rows = mock_ev.call_args[0][1]
    assert len(score_rows) == 1
    lid, score, blended_fair_price, kbb_price, market_source, shap_json = score_rows[0]
    assert score == 55.0
    assert blended_fair_price == round(0.70 * 30000 + 0.30 * 32000)
    assert market_source == "stacked_ml_kbb_blend"
    # to_activate should include this listing
    mock_execute.assert_called_once()
    activate_ids = mock_execute.call_args[0][1][0]
    assert lid in activate_ids


def test_run_uses_ml_only_when_kbb_missing():
    rows = [_listing()]
    with patch.object(analysis_agent, "fetch_all", return_value=rows), \
         patch.object(analysis_agent, "resolve_drivetrain", return_value="unknown"), \
         patch.object(analysis_agent, "predict_fair_price", return_value=(28000, 1500.0)), \
         patch.object(analysis_agent, "predict_kbb_fair_price", return_value=None), \
         patch.object(analysis_agent, "score_listing", return_value=60.0), \
         patch.object(analysis_agent, "explain_listing", return_value=None), \
         patch.object(analysis_agent, "execute_values") as mock_ev, \
         patch.object(analysis_agent, "execute"), \
         patch.object(analysis_agent, "update_kbb_market_cache", return_value=0):
        analysis_agent.run()

    score_rows = mock_ev.call_args[0][1]
    _, _, blended_fair_price, kbb_price, market_source, _ = score_rows[0]
    assert blended_fair_price == 28000
    assert kbb_price is None
    assert market_source == "ml_model"


def test_run_uses_kbb_only_when_ml_missing():
    rows = [_listing()]
    with patch.object(analysis_agent, "fetch_all", return_value=rows), \
         patch.object(analysis_agent, "resolve_drivetrain", return_value="unknown"), \
         patch.object(analysis_agent, "predict_fair_price", return_value=(None, None)), \
         patch.object(analysis_agent, "predict_kbb_fair_price", return_value=31000), \
         patch.object(analysis_agent, "score_listing", return_value=48.0), \
         patch.object(analysis_agent, "explain_listing", return_value=None), \
         patch.object(analysis_agent, "execute_values") as mock_ev, \
         patch.object(analysis_agent, "execute"), \
         patch.object(analysis_agent, "update_kbb_market_cache", return_value=0):
        analysis_agent.run()

    score_rows = mock_ev.call_args[0][1]
    _, _, blended_fair_price, kbb_price, market_source, _ = score_rows[0]
    assert blended_fair_price == 31000
    assert market_source == "kbb"


def test_run_activates_but_does_not_score_when_no_valuation():
    rows = [_listing()]
    with patch.object(analysis_agent, "fetch_all", return_value=rows), \
         patch.object(analysis_agent, "resolve_drivetrain", return_value="unknown"), \
         patch.object(analysis_agent, "predict_fair_price", return_value=(None, None)), \
         patch.object(analysis_agent, "predict_kbb_fair_price", return_value=None), \
         patch.object(analysis_agent, "execute_values") as mock_ev, \
         patch.object(analysis_agent, "execute") as mock_execute, \
         patch.object(analysis_agent, "update_kbb_market_cache", return_value=0):
        result = analysis_agent.run()

    assert result == {"scored": 0}
    score_rows = mock_ev.call_args[0][1]
    assert score_rows == []
    activate_ids = mock_execute.call_args[0][1][0]
    assert len(activate_ids) == 1


def test_run_activates_but_does_not_score_when_score_is_none():
    rows = [_listing()]
    with patch.object(analysis_agent, "fetch_all", return_value=rows), \
         patch.object(analysis_agent, "resolve_drivetrain", return_value="unknown"), \
         patch.object(analysis_agent, "predict_fair_price", return_value=(30000, 1000.0)), \
         patch.object(analysis_agent, "predict_kbb_fair_price", return_value=None), \
         patch.object(analysis_agent, "score_listing", return_value=None), \
         patch.object(analysis_agent, "execute_values") as mock_ev, \
         patch.object(analysis_agent, "execute"), \
         patch.object(analysis_agent, "update_kbb_market_cache", return_value=0):
        result = analysis_agent.run()

    assert result == {"scored": 0}
    assert mock_ev.call_args[0][1] == []


def test_run_applies_resolved_drivetrain_override():
    rows = [_listing(drivetrain="fwd")]
    with patch.object(analysis_agent, "fetch_all", return_value=rows), \
         patch.object(analysis_agent, "resolve_drivetrain", return_value="awd"), \
         patch.object(analysis_agent, "predict_fair_price", return_value=(30000, 1000.0)), \
         patch.object(analysis_agent, "predict_kbb_fair_price", return_value=None), \
         patch.object(analysis_agent, "score_listing", return_value=50.0), \
         patch.object(analysis_agent, "explain_listing", return_value=None), \
         patch.object(analysis_agent, "execute_values") as mock_ev, \
         patch.object(analysis_agent, "execute") as mock_execute, \
         patch.object(analysis_agent, "update_kbb_market_cache", return_value=0):
        analysis_agent.run()

    # execute_values() is called twice: once for score upsert, once for drivetrain backfill
    assert mock_ev.call_count == 2
    drivetrain_sql = mock_ev.call_args_list[1][0][0]
    assert "SET drivetrain" in drivetrain_sql
    # execute() is called once, for activation
    mock_execute.assert_called_once()


def test_run_kbb_cache_failure_is_non_fatal():
    rows = [_listing()]
    with patch.object(analysis_agent, "fetch_all", return_value=rows), \
         patch.object(analysis_agent, "resolve_drivetrain", return_value="unknown"), \
         patch.object(analysis_agent, "predict_fair_price", return_value=(30000, 1000.0)), \
         patch.object(analysis_agent, "predict_kbb_fair_price", return_value=None), \
         patch.object(analysis_agent, "score_listing", return_value=50.0), \
         patch.object(analysis_agent, "explain_listing", return_value=None), \
         patch.object(analysis_agent, "execute_values"), \
         patch.object(analysis_agent, "execute"), \
         patch.object(analysis_agent, "update_kbb_market_cache", side_effect=RuntimeError("db down")):
        result = analysis_agent.run()  # should not raise

    assert result == {"scored": 1}


def test_run_no_listings_returns_zero_scored():
    with patch.object(analysis_agent, "fetch_all", return_value=[]), \
         patch.object(analysis_agent, "execute_values") as mock_ev, \
         patch.object(analysis_agent, "execute") as mock_execute, \
         patch.object(analysis_agent, "update_kbb_market_cache", return_value=0):
        result = analysis_agent.run()

    assert result == {"scored": 0}
    assert mock_ev.call_args[0][1] == []
    mock_execute.assert_not_called()
