from app.sources.kugou import _cover_url, _from_web_item, _from_mobile_item
from app.sources.netease import _album_cover, _cover_from_pic_id, _encrypt_pic_id


def test_kugou_web_item_uses_image_and_replaces_size():
    result = _from_web_item(
        {
            "FileHash": "abc",
            "SongName": "晴天",
            "SingerName": "周杰伦",
            "Duration": 269,
            "AlbumID": 966846,
            "Image": "http://imge.kugou.com/stdmusic/{size}/20230920/20230920142503632013.jpg",
        }
    )
    assert result is not None
    assert result.cover_url == "https://imge.kugou.com/stdmusic/400/20230920/20230920142503632013.jpg"


def test_kugou_mobile_item_uses_album_cover():
    result = _from_mobile_item(
        {
            "hash": "def",
            "songname": "晴天",
            "singername": "周杰伦",
            "duration": 269,
            "album_sizable_cover": "http://imge.kugou.com/stdmusic/{size}/x.jpg",
        }
    )
    assert result is not None
    assert result.cover_url == "https://imge.kugou.com/stdmusic/400/x.jpg"


def test_kugou_cover_url_empty_when_missing():
    assert _cover_url({}) == ""


def test_netease_cover_from_pic_id_matches_official_cdn():
    pic_id = 109951170413587092
    assert _encrypt_pic_id(pic_id) == "-NVLOT5vt9I91LRiZV1TCQ=="
    assert _cover_from_pic_id(pic_id) == (
        "https://p1.music.126.net/-NVLOT5vt9I91LRiZV1TCQ==/109951170413587092.jpg"
    )


def test_netease_search_album_without_picurl_uses_pic_id():
    cover = _album_cover({"name": "晴天", "picId": 109951170413587092})
    assert cover.endswith("/109951170413587092.jpg")
    assert "p1.music.126.net" in cover


def test_netease_prefers_existing_picurl():
    cover = _album_cover(
        {"picUrl": "http://p2.music.126.net/abc==/1.jpg", "picId": 1}
    )
    assert cover == "https://p2.music.126.net/abc==/1.jpg"
