from __future__ import annotations

import re

from app.models import CommandKind, ParsedCommand

_CANCEL_ALIASES = {"撤回", "取消点歌", "取消"}
_CHOICE_RE = re.compile(r"^\s*0*([1-9]\d*)\s*$")


def parse_danmaku(text: str, *, expect_choice: bool = False) -> ParsedCommand:
    """解析弹幕。开头两字为「点歌」即视为点歌（允许无空格）。

    expect_choice=True 时，纯数字会解析为选歌序号；否则纯数字忽略。
    「点歌 123」始终视为点歌，而不是选歌。
    """
    raw = (text or "").strip()
    if not raw:
        return ParsedCommand(kind=CommandKind.IGNORE, raw=raw)

    if raw.startswith("点歌"):
        rest = raw[2:].strip().lstrip("：:，,、.")
        if rest:
            return ParsedCommand(kind=CommandKind.REQUEST, query=rest, raw=raw)
        return ParsedCommand(kind=CommandKind.IGNORE, raw=raw)

    if raw in _CANCEL_ALIASES or raw.startswith("撤回"):
        return ParsedCommand(kind=CommandKind.CANCEL, raw=raw)

    if expect_choice:
        m = _CHOICE_RE.match(raw)
        if m:
            return ParsedCommand(kind=CommandKind.CHOICE, choice=int(m.group(1)), raw=raw)

    return ParsedCommand(kind=CommandKind.IGNORE, raw=raw)


def fit_danmaku(uname: str, message_after_name: str, limit: int = 20) -> str:
    """拼一条不超过字数限制的短弹幕。优先保留提示正文，昵称过长则截断。"""
    name = (uname or "观众").strip() or "观众"
    body = message_after_name.strip()
    # 先试 @昵称 + 空格 + 正文
    prefix = f"@{name} "
    if len(prefix) + len(body) <= limit:
        return prefix + body
    # 截断昵称
    remain_for_name = limit - len(body) - 2  # @ 和空格
    if remain_for_name >= 1:
        return f"@{name[:remain_for_name]} {body}"
    return body[:limit]


_ARTIST_SEPS = (
    " - ", " — ", " – ", " － ",
    " / ", "/", "／",
    " | ", "|", "｜",
    " by ", " BY ", " By ",
)

# 点歌词末尾的音源别名，例如「晴天 周杰伦 酷狗」「晴天 B站」
_SOURCE_ALIASES = {
    "b站": "bili",
    "ｂ站": "bili",
    "哔哩": "bili",
    "哔哩哔哩": "bili",
    "bili": "bili",
    "bilibili": "bili",
    "qq": "qq",
    "qq音乐": "qq",
    "qqmusic": "qq",
    "酷狗": "kugou",
    "酷狗概念": "kugou",
    "酷狗概念版": "kugou",
    "kugou": "kugou",
    "网易": "netease",
    "网易云": "netease",
    "网易云音乐": "netease",
    "云音乐": "netease",
    "netease": "netease",
    "汽水": "qishui",
    "汽水音乐": "qishui",
    "qishui": "qishui",
}


def _fold_alias(token: str) -> str:
    key = (token or "").strip().lower().replace(" ", "").replace("　", "")
    return key.replace("ｑ", "q").replace("Ｑ", "q")


def source_alias_id(token: str) -> str:
    """若该词是音源别名，返回内部 id，否则空字符串。"""
    return _SOURCE_ALIASES.get(_fold_alias(token), "")


def _strip_trailing_alias(raw: str) -> tuple[str, str]:
    """若字符串以音源别名结尾，拆出前面的歌词。整句只是别名时不拆。"""
    text = (raw or "").strip()
    if not text:
        return "", ""
    folded = _fold_alias(text)
    for alias in sorted(_SOURCE_ALIASES, key=len, reverse=True):
        if folded == alias:
            return text, ""
        if folded.endswith(alias):
            # 用原文字符长度回切（别名里没有被折叠掉的空格）
            cut = len(alias)
            rest = text[:-cut].rstrip(" 	-_/|.,，。、:：")
            if rest:
                return rest, _SOURCE_ALIASES[alias]
    return text, ""


def extract_requested_source(query: str) -> tuple[str, str]:
    """从点歌词末尾拆出音源。返回 (去掉音源后的歌词, 音源 id)。

    只有前面还剩歌名时才识别，避免把「点歌 QQ」当成指定平台。
    也支持「ZzTt/Ev网易云」这种平台紧贴在后面、中间没有空格的写法。
    """
    raw = (query or "").strip()
    if not raw:
        return "", ""
    parts = raw.split()
    if len(parts) >= 2:
        sid = source_alias_id(parts[-1])
        if sid:
            rest = " ".join(parts[:-1]).strip()
            if rest:
                return rest, sid
    return _strip_trailing_alias(raw)


def parse_song_query(query: str) -> tuple[str, str, str]:
    """把点歌词拆成 (歌名, 歌手, 音源 id)。音源或歌手没有时为空。"""
    rest, source = extract_requested_source(query)
    title, artist = split_song_query(rest)
    return title, artist, source


def split_song_query(query: str) -> tuple[str, str]:
    """把点歌词拆成 (歌名, 歌手)。

    支持「晴天 周杰伦」「晴天 - 周杰伦」。末尾的音源别名会先去掉。
    没有歌手时第二项为空。
    """
    raw, _source = extract_requested_source(query)
    if not raw:
        return "", ""
    for sep in _ARTIST_SEPS:
        if sep in raw:
            left, right = raw.split(sep, 1)
            title, artist = left.strip(), right.strip()
            if not (title and artist):
                continue
            # ZzTt/Ev 这种歌手名里的斜线不要拆开
            if sep.strip() in {"/", "／"} and re.search(r"[A-Za-z0-9]$", title) and re.search(r"^[A-Za-z0-9]", artist):
                continue
            return title, artist
    parts = raw.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:]).strip()
    return raw, ""
