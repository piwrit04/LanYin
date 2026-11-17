from app import settings


def test_welcome_and_idle_playlist_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)

    cfg = settings.load_config()
    assert cfg["welcome"]["enabled"] is True
    assert cfg["idle_playlist"]["enabled"] is False
    assert cfg["idle_playlist"]["loop"] is True
    assert cfg["idle_playlist"]["resume_delay_seconds"] == 5
    assert cfg["idle_playlist"]["items"] == []
    assert cfg["overlay"]["show_lyrics"] is True
    assert cfg["selection"]["timeout_action"] == "skip"
    assert cfg["welcome"]["extra_templates"] == []

    cfg["welcome"]["enabled"] = False
    cfg["idle_playlist"] = {
        "enabled": True,
        "loop": False,
        "resume_delay_seconds": 8,
        "items": [
            {"kind": "query", "query": "晴天 周杰伦"},
            {
                "kind": "bili",
                "bvid": "BV1xx411c7mD",
                "title": "晴天",
                "artist": "某UP",
                "cover_url": "https://i0.hdslb.com/x.jpg",
            },
        ],
    }
    settings.save_config(cfg)

    again = settings.load_config()
    assert again["welcome"]["enabled"] is False
    assert again["idle_playlist"]["enabled"] is True
    assert again["idle_playlist"]["loop"] is False
    assert again["idle_playlist"]["resume_delay_seconds"] == 8
    items = again["idle_playlist"]["items"]
    assert items[0]["kind"] == "query"
    assert items[0]["query"] == "晴天 周杰伦"
    assert items[0]["id"]
    assert items[1]["kind"] == "bili"
    assert items[1]["bvid"] == "BV1xx411c7mD"
    assert items[1]["title"] == "晴天"
    assert items[1]["artist"] == "某UP"
    assert again["room_id"] == 1506613
    assert "sources_priority" in again
    assert "anti_spam" in again


def test_missing_sections_default_on_load(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("room_id: 42\n", encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    cfg = settings.load_config()
    assert cfg["room_id"] == 42
    assert cfg["welcome"]["enabled"] is True
    assert cfg["idle_playlist"]["enabled"] is False
    assert cfg["idle_playlist"]["items"] == []


def test_overlay_messages_and_timeout_action_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)

    cfg = settings.load_config()
    assert cfg["overlay"]["left"] == 6.7
    assert cfg["overlay"]["bottom"] == 5.0
    assert cfg["overlay"]["width"] == 22.2
    assert cfg["overlay"]["height"] == 17.4
    assert cfg["overlay"]["top"] == 77.6
    assert cfg["overlay"]["right"] == 71.1
    assert cfg["overlay"]["show_lyrics"] is True
    assert cfg["selection"]["timeout_action"] == "skip"
    assert cfg["messages"]["not_found"] == "未找到可播原曲"
    assert cfg["messages"]["not_found_platform"] == "{platform}未找到可播"
    assert cfg["messages"]["please_choose"] == "请回{nums}选歌"
    assert cfg["messages"]["selection_timeout"] == "选歌超时已取消"
    assert cfg["messages"]["choice_unplayable"] == "选中曲无法播放"
    assert cfg["welcome"]["extra_templates"] == []

    cfg["overlay"]["left"] = 10
    cfg["overlay"]["bottom"] = 8
    cfg["overlay"]["width"] = 30
    cfg["overlay"]["height"] = 20
    cfg["overlay"]["show_lyrics"] = False
    cfg["selection"]["timeout_action"] = "play_first"
    cfg["messages"]["not_found"] = "找不到歌"
    cfg["welcome"]["extra_templates"] = ["你好{name}", "  ", "欢迎{name}点歌"]
    settings.save_config(cfg)

    again = settings.load_config()
    assert again["overlay"]["left"] == 10
    assert again["overlay"]["bottom"] == 8
    assert again["overlay"]["width"] == 30
    assert again["overlay"]["height"] == 20
    assert again["overlay"]["top"] == 72.0
    assert again["overlay"]["right"] == 60.0
    assert again["overlay"]["show_lyrics"] is False
    assert again["selection"]["timeout_action"] == "play_first"
    assert again["messages"]["not_found"] == "找不到歌"
    assert again["messages"]["please_choose"] == "请回{nums}选歌"
    assert again["welcome"]["extra_templates"] == ["你好{name}", "欢迎{name}点歌"]


def test_overlay_values_are_clamped(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    cfg = settings.load_config()
    cfg["overlay"] = {"left": 99, "bottom": -3, "width": 1, "height": 90, "show_lyrics": "off"}
    cfg["selection"]["timeout_action"] = "nope"
    settings.save_config(cfg)
    again = settings.load_config()
    assert again["overlay"]["left"] == 80
    assert again["overlay"]["bottom"] == 0
    assert again["overlay"]["width"] == 8
    assert again["overlay"]["height"] == 60
    assert again["overlay"]["top"] == 40.0
    assert again["overlay"]["right"] == 12.0
    assert again["overlay"]["show_lyrics"] is False
    assert again["selection"]["timeout_action"] == "skip"


def test_old_config_merges_new_defaults(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("room_id: 42\n", encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    cfg = settings.load_config()
    assert cfg["room_id"] == 42
    assert cfg["overlay"]["show_lyrics"] is True
    assert cfg["overlay"]["guide_enabled"] is True
    assert cfg["overlay"]["guide_play_count"] == 1
    assert cfg["overlay"]["guide_hide_delay_seconds"] == 3
    assert cfg["overlay"]["guide_reappear_seconds"] == 90
    assert cfg["overlay"]["guide_scroll_speed"] == 40
    assert cfg["overlay"]["left"] == 6.7
    assert cfg["selection"]["timeout_action"] == "skip"
    assert cfg["messages"]["selection_timeout"] == "选歌超时已取消"
    assert cfg["welcome"]["extra_templates"] == []


def test_overlay_insets_are_source_of_truth(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    cfg = settings.load_config()
    cfg["overlay"] = {"top": 70, "right": 60, "left": 10, "bottom": 8, "show_lyrics": True}
    settings.save_config(cfg)
    again = settings.load_config()
    assert again["overlay"]["top"] == 70.0
    assert again["overlay"]["right"] == 60.0
    assert again["overlay"]["left"] == 10.0
    assert again["overlay"]["bottom"] == 8.0
    assert again["overlay"]["width"] == 30.0
    assert again["overlay"]["height"] == 22.0


def test_old_width_height_keep_box_size(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("overlay:\n  left: 6.7\n  bottom: 5.0\n  width: 22.2\n  height: 17.4\n", encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    cfg = settings.load_config()
    assert cfg["overlay"]["left"] == 6.7
    assert cfg["overlay"]["bottom"] == 5.0
    assert cfg["overlay"]["width"] == 22.2
    assert cfg["overlay"]["height"] == 17.4
    assert cfg["overlay"]["top"] == 77.6
    assert cfg["overlay"]["right"] == 71.1


def test_overlay_guide_defaults_and_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)

    cfg = settings.load_config()
    assert cfg["overlay"]["guide_enabled"] is True
    assert cfg["overlay"]["guide_play_count"] == 1
    assert cfg["overlay"]["guide_hide_delay_seconds"] == 3
    assert cfg["overlay"]["guide_reappear_seconds"] == 90
    assert cfg["overlay"]["guide_scroll_speed"] == 40

    cfg["overlay"]["guide_enabled"] = False
    cfg["overlay"]["guide_play_count"] = 4
    cfg["overlay"]["guide_hide_delay_seconds"] = 2.5
    cfg["overlay"]["guide_reappear_seconds"] = 0
    cfg["overlay"]["guide_scroll_speed"] = 36
    settings.save_config(cfg)
    again = settings.load_config()
    assert again["overlay"]["guide_enabled"] is False
    assert again["overlay"]["guide_play_count"] == 4
    assert again["overlay"]["guide_hide_delay_seconds"] == 2.5
    assert again["overlay"]["guide_reappear_seconds"] == 0
    assert again["overlay"]["guide_scroll_speed"] == 36


def test_overlay_guide_values_are_clamped(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    cfg = settings.load_config()
    cfg["overlay"]["guide_enabled"] = "off"
    cfg["overlay"]["guide_play_count"] = 99
    cfg["overlay"]["guide_hide_delay_seconds"] = -2
    settings.save_config(cfg)
    again = settings.load_config()
    assert again["overlay"]["guide_enabled"] is False
    assert again["overlay"]["guide_play_count"] == 20
    assert again["overlay"]["guide_hide_delay_seconds"] == 0

    cfg = settings.load_config()
    cfg["overlay"]["guide_play_count"] = 0
    cfg["overlay"]["guide_hide_delay_seconds"] = 200
    cfg["overlay"]["guide_reappear_seconds"] = 9999
    cfg["overlay"]["guide_scroll_speed"] = 3
    settings.save_config(cfg)
    again = settings.load_config()
    assert again["overlay"]["guide_play_count"] == 1
    assert again["overlay"]["guide_hide_delay_seconds"] == 120
    assert again["overlay"]["guide_reappear_seconds"] == 1800
    assert again["overlay"]["guide_scroll_speed"] == 16
