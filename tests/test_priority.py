from app.models import SearchResult
from app.ranking import rank_results, select_from_priority


def R(title, source, sid, artist="周杰伦", duration=270):
    return SearchResult(source=source, source_id=sid, title=title, artist=artist, duration=duration)


PRIORITY = ["qq", "qishui", "kugou", "bili", "netease"]


def test_fallback_when_higher_source_empty():
    playable = {
        "qq": [],
        "qishui": [],
        "kugou": [],
        "bili": rank_results([R("晴天 官方音频", "bili", "bv1")], "晴天"),
        "netease": rank_results([R("晴天", "netease", "n1")], "晴天"),
    }
    auto, choices, sid = select_from_priority(PRIORITY, playable)
    assert auto is None
    assert sid is None
    assert [c.source for c in choices] == ["bili", "netease"]
    assert [c.source_id for c in choices] == ["bv1", "n1"]


def test_one_best_result_per_platform():
    playable = {
        "qq": rank_results(
            [
                R("晴天 官方音频", "qq", "q1"),
                R("晴天", "qq", "q2"),
                R("晴天 Official MV", "qq", "q3"),
            ],
            "晴天",
        ),
        "bili": rank_results([R("晴天 官方音频", "bili", "bv1")], "晴天"),
    }
    auto, choices, sid = select_from_priority(PRIORITY, playable)
    assert auto is None
    assert sid is None
    assert [c.source for c in choices] == ["qq", "bili"]
    assert choices[0].source_id == "q1"
    assert choices[1].source_id == "bv1"


def test_all_empty():
    auto, choices, sid = select_from_priority(PRIORITY, {})
    assert auto is None and choices == [] and sid is None


def test_skips_cover_only_source_for_later_original():
    covers = rank_results(
        [
            R("晴天", "qq", "c1", artist="小柔Channel"),
            R("晴天（超盛大弦乐)", "qq", "c2", artist="Jerry音乐站"),
        ],
        "晴天",
    )
    # 给翻唱结果带上专辑名，模拟真实搜索
    covers[0].extra = {"album": "小柔日翻周杰伦计划"}
    covers = rank_results(covers, "晴天")
    official = rank_results([R("晴天 官方音频", "bili", "bv1", artist="周杰伦")], "晴天")
    playable = {"qq": covers, "qishui": [], "kugou": [], "bili": official, "netease": []}
    auto, choices, sid = select_from_priority(PRIORITY, playable)
    # 多个平台都有可播结果时，各出一条让观众选，不再只留前面的平台
    assert auto is None
    assert [c.source for c in choices] == ["qq", "bili"]
    assert any(c.source_id == "bv1" for c in choices)


def test_single_platform_still_auto_picks():
    playable = {
        "qq": [],
        "qishui": [],
        "kugou": [],
        "bili": rank_results([R("晴天 官方音频", "bili", "bv1")], "晴天"),
        "netease": [],
    }
    auto, choices, sid = select_from_priority(PRIORITY, playable)
    assert sid == "bili"
    assert auto is not None
    assert auto.source_id == "bv1"
    assert choices == []
