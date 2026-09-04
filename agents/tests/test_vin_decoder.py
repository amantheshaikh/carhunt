import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock

import tools.vin_decoder as vin_decoder
from tools.vin_decoder import decode_vin, _clean, _VIN_RE


VALID_VIN = "5UX43DP07R9T66468"


def setup_function(_fn):
    # Each test starts with a clean module-level cache
    vin_decoder._cache.clear()


def test_clean_none_and_empty():
    assert _clean(None) is None
    assert _clean("") is None
    assert _clean("   ") is None


def test_clean_null_values_case_insensitive():
    for v in ["Not Applicable", "NULL", "0", "None", "not applicable"]:
        assert _clean(v) is None


def test_clean_strips_whitespace():
    assert _clean("  xDrive30i  ") == "xDrive30i"


def test_vin_regex_rejects_bad_length():
    assert not _VIN_RE.match("SHORT")
    assert not _VIN_RE.match(VALID_VIN + "X")


def test_vin_regex_rejects_forbidden_chars():
    # I, O, Q are not valid VIN characters
    assert not _VIN_RE.match("5UX43DPI7R9T66468")


def test_decode_vin_invalid_format_returns_empty_no_network_call():
    with patch.object(vin_decoder.requests, "get") as mock_get:
        result = decode_vin("not-a-vin")
    assert result == {}
    mock_get.assert_not_called()


def test_decode_vin_empty_string_returns_empty():
    assert decode_vin("") == {}
    assert decode_vin(None) == {}


def test_decode_vin_success_parses_fields():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Results": [
            {"Variable": "Make", "Value": "BMW"},
            {"Variable": "Model", "Value": "X3"},
            {"Variable": "Model Year", "Value": "2024"},
            {"Variable": "Trim", "Value": "xDrive30i"},
            {"Variable": "Drive Type", "Value": "AWD"},
            {"Variable": "Body Class", "Value": "SUV"},
        ]
    }
    with patch.object(vin_decoder.requests, "get", return_value=mock_resp) as mock_get:
        result = decode_vin(VALID_VIN)

    assert result == {
        "make": "BMW",
        "model": "X3",
        "year": "2024",
        "trim": "xDrive30i",
        "drive_type": "AWD",
        "body_class": "SUV",
    }
    mock_get.assert_called_once()
    assert VALID_VIN in mock_get.call_args[0][0]


def test_decode_vin_cleans_null_placeholder_values():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Results": [
            {"Variable": "Make", "Value": "BMW"},
            {"Variable": "Model", "Value": "X3"},
            {"Variable": "Model Year", "Value": "2024"},
            {"Variable": "Trim", "Value": "Not Applicable"},
            {"Variable": "Drive Type", "Value": ""},
            {"Variable": "Body Class", "Value": "0"},
        ]
    }
    with patch.object(vin_decoder.requests, "get", return_value=mock_resp):
        result = decode_vin(VALID_VIN)
    assert result["trim"] is None
    assert result["drive_type"] is None
    assert result["body_class"] is None


def test_decode_vin_non_200_returns_empty():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch.object(vin_decoder.requests, "get", return_value=mock_resp):
        result = decode_vin(VALID_VIN)
    assert result == {}


def test_decode_vin_network_exception_returns_empty():
    with patch.object(vin_decoder.requests, "get", side_effect=ConnectionError("timeout")):
        result = decode_vin(VALID_VIN)
    assert result == {}


def test_decode_vin_caches_result_and_skips_second_network_call():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"Results": [{"Variable": "Make", "Value": "BMW"}]}
    with patch.object(vin_decoder.requests, "get", return_value=mock_resp) as mock_get:
        first = decode_vin(VALID_VIN)
        second = decode_vin(VALID_VIN)
    assert first == second
    mock_get.assert_called_once()  # second call served from cache


def test_decode_vin_lowercase_letters_still_valid():
    lower_vin = VALID_VIN.lower()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"Results": []}
    with patch.object(vin_decoder.requests, "get", return_value=mock_resp) as mock_get:
        decode_vin(lower_vin)
    mock_get.assert_called_once()
