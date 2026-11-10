from app.danmaku.client import parse_enter_event


def test_interact_word_enter():
    payload = {
        "cmd": "INTERACT_WORD",
        "data": {"msg_type": 1, "uname": "小羊", "uid": 12345},
    }
    assert parse_enter_event(payload) == (12345, "小羊")


def test_interact_word_ignores_non_enter():
    # 2=关注 3=分享
    for msg_type in (2, 3, 4, 5):
        payload = {
            "cmd": "INTERACT_WORD",
            "data": {"msg_type": msg_type, "uname": "小羊", "uid": 12345},
        }
        assert parse_enter_event(payload) is None


def test_interact_word_uname_from_uinfo():
    payload = {
        "cmd": "INTERACT_WORD",
        "data": {
            "msg_type": 1,
            "uid": 99,
            "uinfo": {"uid": 99, "base": {"name": "从嵌套来的名字"}},
        },
    }
    assert parse_enter_event(payload) == (99, "从嵌套来的名字")


def test_interact_word_v2_like_payload():
    payload = {
        "cmd": "INTERACT_WORD_V2",
        "data": {
            "msg_type": 1,
            "uinfo": {"base": {"name": "V2观众"}, "uid": 777},
        },
    }
    assert parse_enter_event(payload) == (777, "V2观众")


def test_interact_word_v2_non_enter_ignored():
    payload = {
        "cmd": "INTERACT_WORD_V2",
        "data": {"msg_type": 2, "uname": "关注的人", "uid": 1},
    }
    assert parse_enter_event(payload) is None


def test_welcome_and_guard():
    assert parse_enter_event({"cmd": "WELCOME", "data": {"uname": "贵宾", "uid": 8}}) == (8, "贵宾")
    assert parse_enter_event({"cmd": "WELCOME_GUARD", "data": {"uname": "舰长", "uid": 9}}) == (9, "舰长")
    assert parse_enter_event({"cmd": "WELCOME", "data": {"uid": 8}}) is None


def test_unrelated_commands_ignored():
    assert parse_enter_event({"cmd": "DANMU_MSG", "info": [0, "hi", [1, "x"]]}) is None
    assert parse_enter_event({"cmd": "SEND_GIFT", "data": {"uname": "x", "uid": 1}}) is None
    assert parse_enter_event({}) is None


def test_interact_word_v2_pb_enter():
    from app.danmaku.interact_pb import encode_interact_pb
    pb = encode_interact_pb(777, "V2加密观众", 1)
    payload = {"cmd": "INTERACT_WORD_V2", "data": {"dmscore": 1, "pb": pb}}
    assert parse_enter_event(payload) == (777, "V2加密观众")


def test_interact_word_v2_pb_nested_and_follow_ignored():
    from app.danmaku.interact_pb import encode_interact_pb
    enter = encode_interact_pb(12, "嵌套观众", 1)
    follow = encode_interact_pb(12, "关注的人", 2)
    assert parse_enter_event({"cmd": "INTERACT_WORD_V2", "data": {"data": {"pb": enter}}}) == (12, "嵌套观众")
    assert parse_enter_event({"cmd": "INTERACT_WORD_V2", "data": {"pb": follow}}) is None
