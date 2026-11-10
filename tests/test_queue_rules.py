from app.queue_rules import (
    DEFAULT_THEME,
    TIER_ADMIRAL,
    TIER_CAPTAIN,
    TIER_FAN_MEDAL,
    TIER_GOVERNOR,
    TIER_REGULAR,
    insert_index,
    normalize_blacklist,
    normalize_priority_queue,
    normalize_theme,
    priority_tier,
    song_is_blocked,
    user_is_blocked,
)
from app import settings


def test_theme_default_and_normalize():
    assert normalize_theme(None) == DEFAULT_THEME == "wine"
    assert normalize_theme("blush") == "blush"
    assert normalize_theme("PINK") == "wine"
    assert normalize_theme("celadon") == "celadon"
    assert normalize_theme("inkgold") == "inkgold"


def test_theme_and_blacklist_defaults_on_old_config(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("room_id: 42\n", encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    cfg = settings.load_config()
    assert cfg["console_theme"] == "wine"
    assert cfg["priority_queue"]["enabled"] is True
    assert cfg["blacklist"]["users"] == []
    assert cfg["blacklist"]["songs"] == []
    assert cfg["messages"]["blacklisted_user"] == "你已被拉黑"
    assert cfg["messages"]["blacklisted_song"] == "这首歌已拉黑"
    assert cfg["room_id"] == 42


def test_blacklist_and_theme_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    cfg = settings.load_config()
    cfg["console_theme"] = "blush"
    cfg["priority_queue"] = {"enabled": False}
    cfg["blacklist"] = {
        "users": [{"uid": 123, "name": "小明"}, "小红", 456],
        "songs": ["晴天", " 夜曲 ", "晴天"],
    }
    settings.save_config(cfg)
    again = settings.load_config()
    assert again["console_theme"] == "blush"
    assert again["priority_queue"]["enabled"] is False
    users = again["blacklist"]["users"]
    assert {"uid": 123, "name": "小明"} in users
    assert {"uid": 0, "name": "小红"} in users
    assert {"uid": 456, "name": ""} in users
    assert again["blacklist"]["songs"] == ["晴天", "夜曲"]
    # saving theme must not wipe other sections
    assert again["room_id"] == 1506613
    assert "idle_playlist" in again


def test_user_and_song_block_matching():
    bl = normalize_blacklist(raw={
        "users": [{"uid": 9, "name": "小明"}, "Alice"],
        "songs": ["夜曲", "告白气球"],
    })
    assert user_is_blocked(bl, 9, "别人")
    assert user_is_blocked(bl, 0, "小明")
    assert user_is_blocked(bl, 1, "alice")
    assert not user_is_blocked(bl, 2, "小红")
    assert song_is_blocked(bl, "夜曲")
    assert song_is_blocked(bl, "周杰伦 告白气球 现场")
    assert not song_is_blocked(bl, "晴天", "周杰伦")


def test_priority_tiers_and_fifo_insert():
    rooms = [1506613]
    assert priority_tier(1, 0, rooms) == TIER_GOVERNOR
    assert priority_tier(2, 0, rooms) == TIER_ADMIRAL
    assert priority_tier(3, 0, rooms) == TIER_CAPTAIN
    assert priority_tier(0, 1506613, rooms) == TIER_FAN_MEDAL
    assert priority_tier(3, 1506613, rooms) == TIER_CAPTAIN  # guard beats medal
    assert priority_tier(0, 999, rooms) == TIER_REGULAR
    assert priority_tier(1, 0, rooms, enabled=False) == TIER_REGULAR
    # Insert ahead of regulars, FIFO within the same tier, never before a better tier
    existing = [TIER_GOVERNOR, TIER_CAPTAIN, TIER_REGULAR, TIER_REGULAR]
    assert insert_index(existing, TIER_ADMIRAL) == 1
    assert insert_index(existing, TIER_CAPTAIN) == 2
    assert insert_index(existing, TIER_FAN_MEDAL) == 2
    assert insert_index(existing, TIER_REGULAR) == 4
    assert insert_index([], TIER_GOVERNOR) == 0


def test_normalize_priority_queue():
    assert normalize_priority_queue(raw={"enabled": "off"})["enabled"] is False
    assert normalize_priority_queue(raw={})["enabled"] is True
