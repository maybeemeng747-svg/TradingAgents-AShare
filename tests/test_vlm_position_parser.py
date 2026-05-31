"""Tests for VLM-based position image parsing."""
import json
from unittest.mock import patch


def test_parse_position_image_returns_positions():
    """VLM parser extracts positions from mock LLM response."""
    from api.services.vlm_position_parser import parse_position_image

    mock_llm_response = json.dumps([
        {"symbol": "600519", "name": "贵州茅台", "current_position": 100, "average_cost": 1750.0, "market_value": 180000.0},
        {"symbol": "000001", "name": "平安银行", "current_position": 5000, "average_cost": 12.5, "market_value": 62500.0},
    ])

    with patch("api.services.vlm_position_parser.call_vlm") as mock_vlm:
        mock_vlm.return_value = mock_llm_response
        result = parse_position_image(b"fake_image_bytes", "image/png")

    assert len(result) == 2
    assert result[0]["symbol"] == "600519"
    assert result[0]["name"] == "贵州茅台"
    assert result[0]["current_position"] == 100
    assert result[0]["average_cost"] == 1750.0
    assert result[1]["symbol"] == "000001"


def test_parse_position_image_empty_response():
    from api.services.vlm_position_parser import parse_position_image

    with patch("api.services.vlm_position_parser.call_vlm") as mock_vlm:
        mock_vlm.return_value = "[]"
        result = parse_position_image(b"fake_image_bytes", "image/png")

    assert result == []


def test_parse_response_handles_markdown_fences():
    from api.services.vlm_position_parser import _parse_response

    raw = '```json\n[{"symbol": "600519", "name": "贵州茅台", "current_position": 100}]\n```'
    result = _parse_response(raw)
    assert len(result) == 1
    assert result[0]["symbol"] == "600519"


def test_parse_response_handles_invalid_json():
    from api.services.vlm_position_parser import _parse_response

    result = _parse_response("This is not JSON at all")
    assert result == []


def test_parse_response_skips_items_without_symbol():
    from api.services.vlm_position_parser import _parse_response

    raw = json.dumps([
        {"symbol": "600519", "name": "茅台"},
        {"name": "无代码"},
        {"symbol": "", "name": "空代码"},
    ])
    result = _parse_response(raw)
    assert len(result) == 1
    assert result[0]["symbol"] == "600519"


# [VLM-001] watchlist_table_parser — watchlist table tests

def test_parse_watchlist_table_returns_items():
    from api.services.vlm_position_parser import parse_watchlist_table_image

    mock_llm_response = json.dumps([
        {
            "symbol": "002371",
            "name": "北方华创",
            "business": "半导体刻蚀、沉积等关键设备",
            "sector": "半导体设备",
            "bullish_score": 9.3,
            "consensus": 92,
        },
        {
            "symbol": "688981",
            "name": "中芯国际",
            "business": "晶圆代工",
            "sector": "半导体制造",
            "bullish_score": 8.5,
            "consensus": 88,
        },
    ])

    with patch("api.services.vlm_position_parser.call_vlm") as mock_vlm:
        mock_vlm.return_value = mock_llm_response
        result = parse_watchlist_table_image(b"fake_image_bytes", "image/png")

    assert len(result) == 2
    assert result[0]["symbol"] == "002371"
    assert result[0]["name"] == "北方华创"
    assert result[0]["business"] == "半导体刻蚀、沉积等关键设备"
    assert result[0]["sector"] == "半导体设备"
    assert result[0]["bullish_score"] == 9.3
    assert result[0]["consensus"] == 92
    assert "半导体设备" in result[0]["notes"]
    assert "利好9.3" in result[0]["notes"]
    assert "共识92" in result[0]["notes"]


def test_parse_watchlist_table_strips_suffix():
    from api.services.vlm_position_parser import parse_watchlist_table_image

    mock_llm_response = json.dumps([
        {
            "symbol": "002371.SZ",
            "name": "北方华创",
            "business": "半导体设备",
            "sector": "半导体设备",
            "bullish_score": 9.0,
            "consensus": 90,
        },
    ])

    with patch("api.services.vlm_position_parser.call_vlm") as mock_vlm:
        mock_vlm.return_value = mock_llm_response
        result = parse_watchlist_table_image(b"fake_image_bytes", "image/png")

    assert len(result) == 1
    assert result[0]["symbol"] == "002371"


def test_parse_watchlist_table_empty_response():
    from api.services.vlm_position_parser import parse_watchlist_table_image

    with patch("api.services.vlm_position_parser.call_vlm") as mock_vlm:
        mock_vlm.return_value = "[]"
        result = parse_watchlist_table_image(b"fake_image_bytes", "image/png")

    assert result == []


def test_parse_watchlist_table_handles_markdown_fences():
    from api.services.vlm_position_parser import parse_watchlist_table_image

    raw = '```json\n[{"symbol": "002371", "name": "北方华创", "business": "半导体", "sector": "半导体设备", "bullish_score": 9.0, "consensus": 90}]\n```'

    with patch("api.services.vlm_position_parser.call_vlm") as mock_vlm:
        mock_vlm.return_value = raw
        result = parse_watchlist_table_image(b"fake_image_bytes", "image/png")

    assert len(result) == 1
    assert result[0]["symbol"] == "002371"


def test_parse_watchlist_table_invalid_json():
    from api.services.vlm_position_parser import parse_watchlist_table_image

    with patch("api.services.vlm_position_parser.call_vlm") as mock_vlm:
        mock_vlm.return_value = "not json"
        result = parse_watchlist_table_image(b"fake_image_bytes", "image/png")

    assert result == []


def test_parse_watchlist_table_skips_items_without_symbol():
    from api.services.vlm_position_parser import parse_watchlist_table_image

    raw = json.dumps([
        {"name": "无代码"},
        {"symbol": "", "name": "空代码"},
    ])

    with patch("api.services.vlm_position_parser.call_vlm") as mock_vlm:
        mock_vlm.return_value = raw
        result = parse_watchlist_table_image(b"fake_image_bytes", "image/png")

    assert len(result) == 0


def test_build_watchlist_notes_format():
    from api.services.vlm_position_parser import _build_watchlist_notes

    notes = _build_watchlist_notes("半导体设备", "刻蚀/沉积设备", 9.3, 92)
    assert notes == "半导体设备｜刻蚀/沉积设备｜利好9.3｜共识92"


def test_build_watchlist_notes_partial():
    from api.services.vlm_position_parser import _build_watchlist_notes

    notes = _build_watchlist_notes("半导体设备", None, None, None)
    assert notes == "半导体设备"


def test_build_watchlist_notes_empty():
    from api.services.vlm_position_parser import _build_watchlist_notes

    notes = _build_watchlist_notes(None, None, None, None)
    assert notes == ""


def test_extract_6digit_code():
    from api.services.vlm_position_parser import _extract_6digit_code

    assert _extract_6digit_code("002371.SZ") == "002371"
    assert _extract_6digit_code("688981.SH") == "688981"
    assert _extract_6digit_code("600519") == "600519"
    assert _extract_6digit_code("1234567") == "123456"


def test_parse_watchlist_table_handles_null_scores():
    from api.services.vlm_position_parser import parse_watchlist_table_image

    mock_llm_response = json.dumps([
        {
            "symbol": "000001",
            "name": "平安银行",
            "business": "银行",
            "sector": "银行",
            "bullish_score": None,
            "consensus": None,
        },
    ])

    with patch("api.services.vlm_position_parser.call_vlm") as mock_vlm:
        mock_vlm.return_value = mock_llm_response
        result = parse_watchlist_table_image(b"fake_image_bytes", "image/png")

    assert len(result) == 1
    assert result[0]["bullish_score"] is None
    assert result[0]["consensus"] is None
    assert result[0]["notes"] == "银行｜银行"
