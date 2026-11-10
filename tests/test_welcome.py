from datetime import datetime

from app.welcome import (
    SHANGHAI,
    DANMAKU_LIMIT,
    compose_welcome,
    period_of,
    welcome_candidates,
)

SH = SHANGHAI


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 10, hour, minute, tzinfo=SH)


def test_all_candidates_fit_limit():
    for hour in (8, 12, 15, 20, 1):
        cands = welcome_candidates("小羊", now=_dt(hour))
        assert cands, f"hour={hour} should have candidates"
        for key, msg in cands:
            assert len(msg) <= DANMAKU_LIMIT, (key, msg)
            assert "小羊" in msg


def test_time_of_day_templates_present():
    assert any("早上" in msg for _, msg in welcome_candidates("小羊", now=_dt(8)))
    assert any("中午" in msg for _, msg in welcome_candidates("小羊", now=_dt(12)))
    assert any("下午" in msg for _, msg in welcome_candidates("小羊", now=_dt(15)))
    assert any("晚上" in msg for _, msg in welcome_candidates("小羊", now=_dt(20)))
    night = welcome_candidates("小羊", now=_dt(1))
    assert any(("深夜" in msg or "夜深" in msg) for _, msg in night)


def test_period_boundaries_shanghai():
    assert period_of(_dt(5)) == "morning"
    assert period_of(_dt(10, 59)) == "morning"
    assert period_of(_dt(11)) == "noon"
    assert period_of(_dt(13)) == "afternoon"
    assert period_of(_dt(18)) == "evening"
    assert period_of(_dt(23)) == "night"
    assert period_of(_dt(4, 59)) == "night"


def test_variety_and_avoid_repeat():
    now = _dt(8)
    keys = []
    last = None
    import random

    rng = random.Random(42)
    messages = []
    for _ in range(20):
        msg, key = compose_welcome("小羊", now=now, last_key=last, rng=rng)
        assert len(msg) <= 20
        messages.append(msg)
        keys.append(key)
        last = key
    assert len(set(messages)) >= 3
    # 不应连续两次抽到完全相同的模板 key（有候选时）
    cands = welcome_candidates("小羊", now=now)
    if len(cands) > 1:
        for a, b in zip(keys, keys[1:]):
            assert a != b


def test_examples_style():
    cands = dict(welcome_candidates("小羊", now=_dt(8)))
    assert "小羊，早上好！" in cands.values() or any("早上好" in m and "小羊" in m for m in cands.values())
    assert any(m.startswith("欢迎小羊") for m in cands.values())
    assert any("来啦" in m or "来了" in m for m in cands.values())


def test_long_name_still_fits_and_readable():
    long_name = "这是一个非常非常长的直播间昵称ABCDEF"
    msg, key = compose_welcome(long_name, now=_dt(20))
    assert len(msg) <= 20
    assert len(msg) >= 2
    # 仍然像一句问候，而不是空白或乱码
    assert ("欢迎" in msg) or ("来" in msg) or ("好" in msg)
    # 名字被截短后至少保留开头
    assert long_name[0] in msg or "欢迎" in msg


def test_typical_names_need_no_trim():
    for name in ("小羊", "小明", "YangYaShi"):
        cands = welcome_candidates(name, now=_dt(9))
        assert cands
        assert all(name in msg for _, msg in cands)


from app.welcome import WelcomeGate


def test_welcome_gate_cooldown_and_burst_cap():
    gate = WelcomeGate(min_interval=4.5, uid_cooldown=600, max_queue=3)
    assert gate.accept(1, "甲", now=1000)
    assert not gate.accept(1, "甲", now=1001)  # 同一人冷却
    assert gate.accept(2, "乙", now=1002)
    assert gate.accept(3, "丙", now=1003)
    assert not gate.accept(4, "丁", now=1004)  # 队列已满
    assert gate.pending_count() == 3
    first = gate.pop()
    assert first == (1, "甲")
    # 冷却过后可以再进
    assert gate.accept(1, "甲", now=1000 + 601)


def test_extra_templates_are_included():
    cands = welcome_candidates("小羊", now=_dt(8), extra_templates=["欢迎{name}点歌", "嗨{name}"])
    messages = [msg for _, msg in cands]
    assert "欢迎小羊点歌" in messages
    assert "嗨小羊" in messages
    msg, key = compose_welcome(
        "小羊",
        now=_dt(8),
        extra_templates=["欢迎{name}点歌"],
        rng=__import__("random").Random(1),
    )
    assert "小羊" in msg
    assert len(msg) <= DANMAKU_LIMIT


def test_combo_greet_variants_present():
    morning = [m for _, m in welcome_candidates("小羊", now=_dt(8))]
    assert any(m.startswith("小羊，早上") for m in morning)
    assert any("早上好" in m and "小羊" in m and m.endswith("～") for m in morning)
    evening = [m for _, m in welcome_candidates("小羊", now=_dt(20))]
    assert any("晚饭" in m for m in evening)
    night = [m for _, m in welcome_candidates("小羊", now=_dt(1))]
    assert any("通宵" in m or "还没睡" in m for m in night)


def test_avoid_recent_keys_keeps_variety():
    import random

    rng = random.Random(7)
    recent: list[str] = []
    messages = []
    for _ in range(16):
        msg, key = compose_welcome(
            "小羊",
            now=_dt(8),
            last_key=recent[-1] if recent else None,
            last_keys=recent,
            rng=rng,
        )
        messages.append(msg)
        recent.append(key)
        recent = recent[-8:]
    assert len(set(messages)) >= 8
    # 相邻两条不应完全相同
    for a, b in zip(messages, messages[1:]):
        assert a != b
