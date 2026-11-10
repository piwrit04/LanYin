from app.bili_links import is_bili_ref, parse_bili_input


def test_plain_query():
    assert parse_bili_input("晴天 周杰伦") == {"kind": "query", "query": "晴天 周杰伦"}
    assert not is_bili_ref("晴天 周杰伦")


def test_bvid_in_url_and_bare():
    assert parse_bili_input("https://www.bilibili.com/video/BV1xx411c7mD")["bvid"] == "BV1xx411c7mD"
    assert parse_bili_input("BV1xx411c7mD")["kind"] == "bvid"
    assert parse_bili_input("BV1xx411c7mD")["bvid"] == "BV1xx411c7mD"
    # 带 query 和分 p
    parsed = parse_bili_input("https://www.bilibili.com/video/BV1ab411c7XX/?p=2&vd_source=abc")
    assert parsed["kind"] == "bvid"
    assert parsed["bvid"] == "BV1ab411c7XX"


def test_av_numbers():
    assert parse_bili_input("https://www.bilibili.com/video/av170001") == {"kind": "aid", "aid": 170001}
    assert parse_bili_input("av170001") == {"kind": "aid", "aid": 170001}
    assert parse_bili_input("https://www.bilibili.com/video/BV1xx411c7mD?aid=170001")["kind"] == "bvid"
    # 纯数字不是 av
    assert parse_bili_input("170001") == {"kind": "query", "query": "170001"}


def test_b23_short_link():
    parsed = parse_bili_input("https://b23.tv/abcdef")
    assert parsed["kind"] == "short"
    assert "b23.tv/abcdef" in parsed["url"]
    parsed = parse_bili_input("b23.tv/AbC_12")
    assert parsed["kind"] == "short"
    assert parsed["url"].startswith("https://")


def test_empty():
    assert parse_bili_input("  ")["kind"] == "query"
