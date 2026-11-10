from app.sources.bili import collection_play_order, episode_start_index, episodes_from_view


def test_ugc_season_lists_all_episodes():
    view = {
        "bvid": "BV2",
        "title": "第二集",
        "owner": {"name": "UP主"},
        "pic": "https://i0.hdslb.com/a.jpg",
        "ugc_season": {
            "title": "宝藏音乐合集",
            "sections": [{
                "episodes": [
                    {"bvid": "BV1", "aid": 1, "cid": 11, "title": "第一集", "arc": {"duration": 100, "pic": "https://i0.hdslb.com/1.jpg"}},
                    {"bvid": "BV2", "aid": 2, "cid": 22, "title": "第二集", "arc": {"duration": 200, "pic": "https://i0.hdslb.com/2.jpg"}},
                    {"bvid": "BV3", "aid": 3, "cid": 33, "title": "第三集", "arc": {"duration": 300, "pic": "https://i0.hdslb.com/3.jpg"}},
                ]
            }],
        },
    }
    eps = episodes_from_view(view, start_bvid="BV2")
    assert [e["bvid"] for e in eps] == ["BV1", "BV2", "BV3"]
    assert eps[0]["season_title"] == "宝藏音乐合集"
    assert eps[0]["season_index"] == 1
    assert eps[1]["season_index"] == 2
    assert eps[1]["season_total"] == 3
    assert eps[2]["title"] == "第三集"
    assert episode_start_index(eps, bvid="BV2") == 1


def test_collection_play_order_starts_at_added_and_wraps_when_loop():
    eps = [{"bvid": f"BV{i}", "season_index": i} for i in range(1, 9)]
    start = episode_start_index(eps, bvid="BV4")
    assert start == 3
    assert [e["bvid"] for e in collection_play_order(eps, start, loop=False)] == [
        "BV4", "BV5", "BV6", "BV7", "BV8"
    ]
    assert [e["bvid"] for e in collection_play_order(eps, start, loop=True)] == [
        "BV4", "BV5", "BV6", "BV7", "BV8", "BV1", "BV2", "BV3"
    ]


def test_multi_page_lists_all_cids():
    view = {
        "bvid": "BVX",
        "aid": 9,
        "title": "专辑",
        "owner": {"name": "UP主"},
        "pages": [
            {"cid": 1, "part": "P1", "duration": 60},
            {"cid": 2, "part": "P2", "duration": 70},
            {"cid": 3, "part": "P3", "duration": 80},
        ],
    }
    eps = episodes_from_view(view, start_bvid="BVX", start_cid=2)
    assert [e["cid"] for e in eps] == [1, 2, 3]
    assert eps[1]["season_index"] == 2
    assert eps[1]["season_total"] == 3
    assert "P2" in eps[1]["title"]
    assert episode_start_index(eps, bvid="BVX", cid=2) == 1


def test_single_video_has_no_follow():
    view = {"bvid": "BV1", "title": "单集", "pages": [{"cid": 1, "part": "单集", "duration": 60}]}
    assert episodes_from_view(view, start_bvid="BV1") == []
