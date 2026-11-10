from __future__ import annotations

import re
from typing import Iterable

from app.models import SearchResult
from app.parser import split_song_query

# 明确更可能是非原唱 / 非完整单曲的标记
NEGATIVE_STRONG = [
    "伴奏", "纯伴奏", "karaoke", "karaoke", "卡拉ok", "卡拉OK", "k歌",
    "消音", "人声分离", "off vocal", "offvocal", "instrumental", "inst.",
    "inst版", "演奏版",
]
NEGATIVE_COVER = [
    "翻唱", "翻唱版", "cover", "covers", "cover版", "男声翻唱", "女声翻唱",
    "吉他弹唱", "弹唱", "钢琴版", "钢琴曲", "古筝", "小提琴", "指弹",
    "鼓盖", "drum cover", "violin", "piano cover",
    "日翻", "翻自", "翻奏", "翻唱计划",
    "女声独唱", "男声独唱", "独唱版", "女声版", "男声版", "女生版", "男生版",
    "女声", "男声",
]
NEGATIVE_REMIX = [
    " dj", "dj ", "dj版", "djremix", "remix", "混音", "夜店", "电音版",
    "加速", "超慢", "倒放", "剪辑版", "踩点",
    "r&b版", "弦乐", "交响乐", "改编版", "改编",
]
NEGATIVE_COMPILATION = [
    "合集", "精选", "串烧", "medley", "连播", "歌单", "全程", "纯享合集",
    "十首歌", "热歌", "循环",
]
NEGATIVE_LIVE = [
    "现场", "演唱会", "live版", " live", "live ", "音乐会", "综艺",
]
NEGATIVE_OTHER = [
    "教学", "教程", "片段", "预告", "reaction", "反应", "解说", "二创",
    "鬼畜", "音效", "配音", "ktv", "KTV",
]

POSITIVE = [
    "官方音频", "官方mv", "官方mv", "official audio", "official mv",
    "official music video", "原唱", "官方", "official", "主题曲",
    "原版", "高品质",
]

_TAG_RE = re.compile(r"<[^>]+>")
_BRACKET_RE = re.compile(r"[\(\[\{（【『「].*?[\)\]\}）】』」]")
_SPACE_RE = re.compile(r"\s+")


def strip_markup(text: str) -> str:
    return _SPACE_RE.sub(" ", _TAG_RE.sub("", text or "")).strip()


def normalize_title(text: str) -> str:
    t = strip_markup(text).lower()
    t = t.replace("official music video", " ")
    t = t.replace("official audio", " ")
    t = t.replace("official mv", " ")
    t = t.replace("official", " ")
    t = t.replace("官方音频", " ")
    t = t.replace("官方mv", " ")
    t = t.replace("官方", " ")
    t = t.replace("原唱", " ")
    t = _BRACKET_RE.sub(" ", t)
    t = re.sub(r"[·・\-—_|,，。!！?？:：'\"“”‘’]", " ", t)
    return _SPACE_RE.sub(" ", t).strip()


def _contains_any(text: str, words: Iterable[str]) -> list[str]:
    low = f" {text.lower()} "
    raw = f" {text} "
    hits = []
    for w in words:
        wl = w.lower()
        if wl.startswith(" ") or wl.endswith(" "):
            if wl in low:
                hits.append(w.strip())
        elif w in text or wl in low:
            hits.append(w.strip())
    return hits


def score_result(result: SearchResult, query: str) -> SearchResult:
    """给搜索结果打分：越像原唱完整单曲越高。会写回 result.score / flags。"""
    title = strip_markup(result.title)
    artist = strip_markup(result.artist)
    album = strip_markup(str((result.extra or {}).get("album") or ""))
    blob = " ".join(x for x in (title, artist, album) if x)
    query_n = normalize_title(query)
    title_n = normalize_title(title)
    artist_n = normalize_title(artist)

    flags: list[str] = []
    score = 40.0

    # 标题 / 歌手匹配。同时兼容「晴天 周杰伦」和「周杰伦 晴天」
    best_extra = -1.0
    best_flags: list[str] = []
    title_hint, artist_hint = split_song_query(query)
    interpretations: list[tuple[str, str]] = [(query, "")]
    if artist_hint:
        interpretations.append((title_hint, artist_hint))
        interpretations.append((artist_hint, title_hint))

    for q_title, q_artist in interpretations:
        extra = 0.0
        fl: list[str] = []
        qtn = normalize_title(q_title) if q_title else ""
        qan = normalize_title(q_artist) if q_artist else ""
        if qtn and title_n == qtn:
            extra += 45
            fl.append("标题完全匹配")
        elif qtn and qtn in title_n:
            extra += 30
            fl.append("标题包含点歌词")
        elif qtn:
            q_tokens = [t for t in qtn.split(" ") if t]
            if q_tokens:
                hits = sum(1 for t in q_tokens if t in title_n or t in artist_n)
                extra += (hits / len(q_tokens)) * 24
        if qan and artist_n and (qan == artist_n or qan in artist_n or artist_n in qan):
            extra += 18
            fl.append("歌手匹配")
        elif not qan and artist_n and artist_n in query_n:
            extra += 12
            fl.append("歌手匹配")
        if extra > best_extra:
            best_extra = extra
            best_flags = fl

    if best_extra > 0:
        score += best_extra
        flags.extend(best_flags)

    strong = _contains_any(blob, NEGATIVE_STRONG)
    if strong:
        score -= 70
        flags.extend(f"排除:{w}" for w in strong)
    cover = _contains_any(blob, NEGATIVE_COVER)
    if cover:
        score -= 45
        flags.extend(f"翻唱类:{w}" for w in cover)
    remix = _contains_any(blob, NEGATIVE_REMIX)
    if remix:
        score -= 40
        flags.extend(f"混音类:{w}" for w in remix)
    compilation = _contains_any(blob, NEGATIVE_COMPILATION)
    if compilation:
        score -= 35
        flags.extend(f"合集类:{w}" for w in compilation)
    live = _contains_any(blob, NEGATIVE_LIVE)
    if live:
        score -= 25
        flags.extend(f"现场类:{w}" for w in live)
    other = _contains_any(blob, NEGATIVE_OTHER)
    if other:
        score -= 30
        flags.extend(f"无关:{w}" for w in other)

    pos = _contains_any(title, POSITIVE)
    if pos:
        score += 18
        flags.extend(f"加分:{w}" for w in pos)

    # 「某某版」多半是改编/翻唱；官方/原版除外
    if re.search(r"版", title) and not any(
        w in title.lower() for w in ("官方", "原版", "完整版", "原唱", "official")
    ):
        score -= 28
        flags.append("改编版式标题")
        remix = remix or ["版"]

    duration = result.duration or 0
    if duration:
        if 90 <= duration <= 420:
            score += 8
        elif 60 <= duration < 90 or 420 < duration <= 540:
            score += 2
        elif duration < 45:
            score -= 25
            flags.append("时长过短")
        elif duration > 720:
            score -= 30
            flags.append("时长过长(可能是合集)")

    is_original = score >= 55 and not strong and not cover and not remix and not compilation
    if is_original:
        flags.append("疑似原唱")

    result.score = round(score, 2)
    result.flags = flags
    result.is_original_like = bool(is_original)
    result.title = title
    result.artist = artist
    return result


def rank_results(results: list[SearchResult], query: str) -> list[SearchResult]:
    scored = [score_result(r, query) for r in results]
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored


# 低于此分数的结果不参与「像样」候选
PLAUSIBLE_SCORE = 45.0
# 唯一高置信需要达到的分数
HIGH_CONFIDENCE_SCORE = 70.0
# 第一名领先第二名多少，才算可以自动入队
HIGH_CONFIDENCE_MARGIN = 22.0
MAX_CHOICES = 5


def pick_auto_or_choices(
    ranked: list[SearchResult],
) -> tuple[SearchResult | None, list[SearchResult]]:
    """决定是自动入队还是让观众选。

    返回 (auto_pick, choices)。
    - auto_pick 非空：可直接入队
    - choices 长度>=2：需要观众选序号
    - 都空：没有可接受的结果
    """
    plausible = [r for r in ranked if r.score >= PLAUSIBLE_SCORE]
    if not plausible:
        # 没有任何像样的，但如果有一个还算正分的结果，也交给上层决定是否勉强使用
        weak = [r for r in ranked if r.score >= 20]
        if len(weak) == 1:
            return weak[0], []
        if len(weak) > 1:
            return None, weak[:MAX_CHOICES]
        return None, []

    if len(plausible) == 1:
        return plausible[0], []

    top, second = plausible[0], plausible[1]
    if (
        top.score >= HIGH_CONFIDENCE_SCORE
        and (top.score - second.score) >= HIGH_CONFIDENCE_MARGIN
        and top.is_original_like
    ):
        return top, []

    return None, plausible[:MAX_CHOICES]


def _best_of_source(ranked: list[SearchResult]) -> SearchResult | None:
    if not ranked:
        return None
    auto, choices = pick_auto_or_choices(ranked)
    if auto is not None:
        return auto
    if choices:
        return choices[0]
    return ranked[0]


def select_from_priority(
    priority: list[str],
    playable_by_source: dict[str, list[SearchResult]],
) -> tuple[SearchResult | None, list[SearchResult], str | None]:
    """按音源优先级挑选。

    多个平台都有可播结果时，每个平台各出一条最好的，交给观众选，
    避免排名靠后的平台（如网易云）被前面的平台挤掉。
    只有一个平台有结果时，仍按原逻辑自动入队或在该平台内选择。
    """
    present = [sid for sid in priority if playable_by_source.get(sid)]
    if len(present) >= 2:
        picks: list[SearchResult] = []
        for sid in present:
            best = _best_of_source(playable_by_source.get(sid) or [])
            if best:
                picks.append(best)
        if len(picks) >= 2:
            return None, picks[:MAX_CHOICES], None
        if len(picks) == 1:
            return picks[0], [], picks[0].source
        return None, [], None

    weak_auto: SearchResult | None = None
    weak_auto_sid: str | None = None
    weak_choices: list[SearchResult] | None = None
    weak_choice_sid: str | None = None
    for sid in priority:
        ranked = playable_by_source.get(sid) or []
        if not ranked:
            continue
        auto, choices = pick_auto_or_choices(ranked)
        if auto is not None:
            if auto.is_original_like:
                return auto, [], sid
            if weak_auto is None and weak_choices is None:
                weak_auto, weak_auto_sid = auto, sid
            continue
        if choices:
            if any(c.is_original_like for c in choices):
                return None, choices, sid
            if weak_choices is None and weak_auto is None:
                weak_choices, weak_choice_sid = choices, sid
            continue
    if weak_auto is not None:
        return weak_auto, [], weak_auto_sid
    if weak_choices is not None:
        return None, weak_choices, weak_choice_sid
    return None, [], None
