from app.parser import fit_danmaku, parse_danmaku, parse_song_query, split_song_query
from app.models import CommandKind


def test_request_with_space():
    cmd = parse_danmaku("点歌 晴天")
    assert cmd.kind == CommandKind.REQUEST
    assert cmd.query == "晴天"


def test_request_without_space():
    cmd = parse_danmaku("点歌晴天")
    assert cmd.kind == CommandKind.REQUEST
    assert cmd.query == "晴天"


def test_request_with_artist():
    cmd = parse_danmaku("点歌 周杰伦 晴天")
    assert cmd.kind == CommandKind.REQUEST
    assert cmd.query == "周杰伦 晴天"


def test_request_with_colon():
    cmd = parse_danmaku("点歌：稻香")
    assert cmd.kind == CommandKind.REQUEST
    assert cmd.query == "稻香"


def test_bare_prefix_ignored():
    assert parse_danmaku("点歌").kind == CommandKind.IGNORE
    assert parse_danmaku("点歌   ").kind == CommandKind.IGNORE


def test_cancel():
    assert parse_danmaku("撤回").kind == CommandKind.CANCEL
    assert parse_danmaku("取消点歌").kind == CommandKind.CANCEL


def test_choice_only_when_expected():
    assert parse_danmaku("2").kind == CommandKind.IGNORE
    cmd = parse_danmaku("2", expect_choice=True)
    assert cmd.kind == CommandKind.CHOICE
    assert cmd.choice == 2
    assert parse_danmaku("01", expect_choice=True).choice == 1
    assert parse_danmaku("0", expect_choice=True).kind == CommandKind.IGNORE


def test_request_number_is_not_choice():
    cmd = parse_danmaku("点歌 123", expect_choice=True)
    assert cmd.kind == CommandKind.REQUEST
    assert cmd.query == "123"


def test_unrelated():
    assert parse_danmaku("哈哈哈").kind == CommandKind.IGNORE
    assert parse_danmaku("").kind == CommandKind.IGNORE


def test_fit_danmaku_short_name():
    msg = fit_danmaku("小明", "请回1或2选歌", limit=20)
    assert msg == "@小明 请回1或2选歌"
    assert len(msg) <= 20


def test_fit_danmaku_long_name_truncated():
    msg = fit_danmaku("这是一个非常非常长的昵称", "请回1/2选歌", limit=20)
    assert len(msg) <= 20
    assert "请回1/2选歌" in msg
    assert msg.startswith("@")


def test_request_title_then_artist():
    cmd = parse_danmaku("点歌 晴天 周杰伦")
    assert cmd.kind == CommandKind.REQUEST
    assert cmd.query == "晴天 周杰伦"
    assert split_song_query(cmd.query) == ("晴天", "周杰伦")


def test_split_song_query_formats():
    assert split_song_query("晴天") == ("晴天", "")
    assert split_song_query("晴天 周杰伦") == ("晴天", "周杰伦")
    assert split_song_query("起风了 买辣椒也用酱") == ("起风了", "买辣椒也用酱")
    assert split_song_query("晴天 - 周杰伦") == ("晴天", "周杰伦")
    assert split_song_query("晴天/周杰伦") == ("晴天", "周杰伦")



def test_parse_source_at_end():
    assert parse_song_query("晴天 B站") == ("晴天", "", "bili")
    assert parse_song_query("晴天 QQ") == ("晴天", "", "qq")
    assert parse_song_query("晴天 周杰伦 酷狗") == ("晴天", "周杰伦", "kugou")
    assert parse_song_query("晴天 - 周杰伦 网易云") == ("晴天", "周杰伦", "netease")
    assert parse_song_query("晴天 汽水音乐") == ("晴天", "", "qishui")
    assert parse_song_query("晴天 bili") == ("晴天", "", "bili")
    assert parse_song_query("晴天 哔哩哔哩") == ("晴天", "", "bili")


def test_source_not_eaten_when_alone():
    assert parse_song_query("QQ") == ("QQ", "", "")
    assert parse_song_query("酷狗") == ("酷狗", "", "")


def test_split_strips_trailing_source():
    assert split_song_query("晴天 周杰伦 酷狗") == ("晴天", "周杰伦")
    assert split_song_query("晴天 B站") == ("晴天", "")
    assert split_song_query("晴天 周杰伦") == ("晴天", "周杰伦")


def test_source_after_slash_artist():
    assert parse_song_query("闹钟- ZzTt/Ev 网易云")[2] == "netease"
    assert parse_song_query("闹钟- ZzTt/Ev 网易云")[0:2] == ("闹钟-", "ZzTt/Ev")
    assert parse_song_query("闹钟- ZzTt/Ev网易云") == ("闹钟-", "ZzTt/Ev", "netease")
    assert parse_song_query("晴天/周杰伦 酷狗") == ("晴天", "周杰伦", "kugou")
    assert split_song_query("晴天/周杰伦") == ("晴天", "周杰伦")
