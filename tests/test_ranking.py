from app.models import SearchResult
from app.ranking import pick_auto_or_choices, rank_results, score_result


def R(title, artist="周杰伦", duration=270, source="bili", sid="1"):
    return SearchResult(source=source, source_id=sid, title=title, artist=artist, duration=duration)


def test_prefers_official_original():
    items = [
        R("晴天 翻唱 女声", sid="cover"),
        R("晴天 伴奏", sid="inst"),
        R("晴天 官方音频", sid="official"),
        R("晴天 DJ嗨曲", sid="dj"),
        R("晴天 十年热歌合集", duration=3600, sid="comp"),
    ]
    ranked = rank_results(items, "晴天")
    assert ranked[0].source_id == "official"
    assert ranked[0].is_original_like
    assert ranked[0].score > next(r.score for r in ranked if r.source_id == "cover")


def test_penalize_live_and_instrumental():
    orig = score_result(R("晴天"), "晴天")
    live = score_result(R("晴天 演唱会现场"), "晴天")
    inst = score_result(R("晴天 Instrumental"), "晴天")
    assert orig.score > live.score
    assert orig.score > inst.score
    assert inst.score < 45


def test_auto_pick_single_high_confidence():
    ranked = rank_results([R("晴天 官方音频", sid="a")], "周杰伦 晴天")
    auto, choices = pick_auto_or_choices(ranked)
    assert auto is not None
    assert auto.source_id == "a"
    assert choices == []


def test_ask_when_multiple_close_matches():
    items = [
        R("晴天 官方音频", artist="周杰伦", sid="a"),
        R("晴天", artist="周杰伦", sid="b"),
        R("晴天 Official MV", artist="周杰伦", sid="c"),
    ]
    ranked = rank_results(items, "晴天")
    auto, choices = pick_auto_or_choices(ranked)
    assert auto is None
    assert len(choices) >= 2


def test_no_results():
    auto, choices = pick_auto_or_choices([])
    assert auto is None
    assert choices == []


def test_penalize_cover_in_album_not_just_title():
    official = score_result(R("晴天", artist="周杰伦"), "晴天")
    cover = R("晴天", artist="小柔Channel", sid="c")
    cover.extra = {"album": "小柔日翻周杰伦计划"}
    cover = score_result(cover, "晴天")
    assert official.score > cover.score
    assert not cover.is_original_like
    assert official.score >= 45


def test_penalize_female_solo_version_title():
    official = score_result(R("晴天", artist="周杰伦"), "晴天")
    solo = score_result(R("晴天 (女声独唱版)", artist="梅菜扣肉肉"), "晴天")
    assert official.score > solo.score
    assert not solo.is_original_like


def test_prefers_matching_artist_after_title():
    right = score_result(R("晴天", artist="周杰伦", sid="jay"), "晴天 周杰伦")
    wrong = score_result(R("晴天", artist="汪苏泷", sid="other"), "晴天 周杰伦")
    assert right.score > wrong.score
    assert "歌手匹配" in right.flags


def test_artist_first_query_still_matches():
    result = score_result(R("晴天", artist="周杰伦", sid="jay"), "周杰伦 晴天")
    assert result.score >= 70
    assert "标题完全匹配" in result.flags or "标题包含点歌词" in result.flags
    assert "歌手匹配" in result.flags
