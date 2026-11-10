from __future__ import annotations

import random
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

def _shanghai_tz():
    """Windows 默认不带 IANA 时区数据，缺 tzdata 时回退到东八区。"""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


SHANGHAI = _shanghai_tz()

# B 站普通弹幕上限（与 send_danmaku / fit_danmaku 一致）
DANMAKU_LIMIT = 20

# 时段：早上 / 中午 / 下午 / 晚上 / 深夜
PERIODS = {
    "morning": "早上",
    "noon": "中午",
    "afternoon": "下午",
    "evening": "晚上",
    "night": "深夜",
}

# (key, template)  — {name} 会被替换。优先选用能完整放下昵称的模板。
PERIOD_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "morning": [
        ("m_hi", "{name}，早上好！"),
        ("m_hi2", "早上好，{name}～"),
        ("m_ya", "{name}早呀～"),
        ("m_hi3", "早安，{name}！"),
        ("m_listen", "{name}早，来听歌呀"),
    ],
    "noon": [
        ("n_hi", "{name}，中午好！"),
        ("n_hi2", "中午好，{name}～"),
        ("n_ya", "{name}中午好呀"),
        ("n_welcome", "中午欢迎{name}"),
    ],
    "afternoon": [
        ("a_hi", "{name}，下午好！"),
        ("a_hi2", "下午好，{name}～"),
        ("a_ya", "{name}下午好呀"),
        ("a_welcome", "下午欢迎{name}"),
    ],
    "evening": [
        ("e_hi", "{name}，晚上好！"),
        ("e_hi2", "晚上好，{name}～"),
        ("e_ya", "{name}晚上好呀"),
        ("e_welcome", "晚上欢迎{name}"),
    ],
    "night": [
        ("k_late", "{name}，夜深了～"),
        ("k_hi", "欢迎{name}深夜来"),
        ("k_ya", "深夜好，{name}～"),
        ("k_still", "{name}还没睡呀～"),
        ("k_welcome", "夜深欢迎{name}"),
    ],
}

COMMON_TEMPLATES: list[tuple[str, str]] = [
    ("c_welcome_wave", "欢迎{name}～"),
    ("c_welcome_bang", "欢迎{name}！"),
    ("c_welcome_plain", "欢迎{name}"),
    ("c_came_bang", "{name}来啦！"),
    ("c_came_wave", "{name}来了～"),
    ("c_listen", "欢迎{name}来听歌"),
    ("c_here", "{name}来了"),
    ("c_room", "欢迎{name}进房"),
    ("c_hi", "嗨，{name}！"),
    ("c_listen2", "{name}来听歌啦"),
    ("c_came_soft", "{name}来啦～"),
    ("c_hi_wave", "嗨～{name}"),
    ("c_sit", "{name}快坐呀"),
]

# 昵称很长时的短模板（仍尽量读得通）
SHORT_TEMPLATES: list[tuple[str, str]] = [
    ("s_welcome", "欢迎{name}"),
    ("s_came", "{name}来了"),
    ("s_wave", "欢迎{name}～"),
]

# 像「神奇弹幕」那样把时段招呼、语气、标点拆开再组合，让相邻几句几乎不重复。
PERIOD_GREETS: dict[str, list[str]] = {
    "morning": ["早上好", "早安"],
    "noon": ["中午好"],
    "afternoon": ["下午好"],
    "evening": ["晚上好"],
    "night": ["夜深了", "深夜好"],
}
PERIOD_PHRASES: dict[str, list[str]] = {
    "morning": ["来听歌呀"],
    "noon": ["午饭吃了吗"],
    "afternoon": ["来听歌呀"],
    "evening": ["晚饭吃了吗"],
    "night": ["还没睡呀", "通宵了吗"],
}
_COMBO_TONES = ("", "呀", "啊")
_COMBO_PUNCS = ("～", "！")


def _combo_templates(period: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for greet in PERIOD_GREETS.get(period) or []:
        for tone in _COMBO_TONES:
            for punc in _COMBO_PUNCS:
                out.append((f"g_{period}_{greet}_{tone}_{punc}a", "{name}，" + greet + tone + punc))
                out.append((f"g_{period}_{greet}_{tone}_{punc}b", greet + tone + "，{name}" + punc))
    for phrase in PERIOD_PHRASES.get(period) or []:
        if phrase.endswith("吗"):
            out.append((f"p_{period}_{phrase}a", "{name}，" + phrase + "？"))
            out.append((f"p_{period}_{phrase}b", phrase + "，{name}？"))
        else:
            out.append((f"p_{period}_{phrase}a", "{name}，" + phrase))
            out.append((f"p_{period}_{phrase}b", phrase + "，{name}～"))
    return out


def period_of(now: Optional[datetime] = None) -> str:
    """按 Asia/Shanghai 把当前时刻归到早上/中午/下午/晚上/深夜。"""
    if now is None:
        now = datetime.now(SHANGHAI)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI)
    else:
        now = now.astimezone(SHANGHAI)
    hour = now.hour
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 13:
        return "noon"
    if 13 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def period_label(now: Optional[datetime] = None) -> str:
    return PERIODS[period_of(now)]


def _render(template: str, name: str, limit: int = DANMAKU_LIMIT) -> Optional[str]:
    msg = template.replace("{name}", name)
    if len(msg) <= limit:
        return msg
    return None


def _render_trim(template: str, name: str, limit: int = DANMAKU_LIMIT) -> Optional[str]:
    """必要时截短昵称，保证整句 ≤ limit 且昵称至少 1 个字。"""
    fitted = _render(template, name, limit)
    if fitted:
        return fitted
    overhead = len(template.replace("{name}", ""))
    room = limit - overhead
    if room < 1:
        return None
    return template.replace("{name}", name[:room])


def _extra_template_pairs(extra_templates: Optional[list[str]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for i, raw in enumerate(extra_templates or []):
        tmpl = str(raw or "").strip()
        if tmpl:
            pairs.append((f"extra_{i}", tmpl))
    return pairs


def welcome_candidates(
    name: str,
    *,
    now: Optional[datetime] = None,
    limit: int = DANMAKU_LIMIT,
    extra_templates: Optional[list[str]] = None,
) -> list[tuple[str, str]]:
    """返回所有能完整放下昵称的 (key, message)，时段问候优先排在前面。"""
    uname = (name or "").strip() or "朋友"
    period = period_of(now)
    pool = (
        list(PERIOD_TEMPLATES.get(period) or [])
        + _combo_templates(period)
        + _extra_template_pairs(extra_templates)
        + list(COMMON_TEMPLATES)
    )
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, tmpl in pool:
        msg = _render(tmpl, uname, limit)
        if msg and msg not in seen:
            out.append((key, msg))
            seen.add(msg)
    return out


def _avoid_recent(
    candidates: list[tuple[str, str]],
    *,
    last_key: Optional[str] = None,
    last_keys: Optional[list[str]] = None,
) -> list[tuple[str, str]]:
    """尽量避开最近用过的模板；候选不够时至少避开上一条。"""
    if len(candidates) <= 1:
        return candidates
    recent = [k for k in (last_keys or []) if k]
    if not recent and last_key:
        recent = [last_key]
    if not recent:
        return candidates
    filtered = [c for c in candidates if c[0] not in recent]
    if len(filtered) >= 2:
        return filtered
    avoid = recent[-1]
    slim = [c for c in candidates if c[0] != avoid]
    return slim or candidates


def compose_welcome(
    name: str,
    *,
    now: Optional[datetime] = None,
    last_key: Optional[str] = None,
    last_keys: Optional[list[str]] = None,
    limit: int = DANMAKU_LIMIT,
    rng: Optional[random.Random] = None,
    extra_templates: Optional[list[str]] = None,
) -> tuple[str, str]:
    """生成一条欢迎弹幕和所用模板 key。避免立刻重复最近用过的模板。"""
    uname = (name or "").strip() or "朋友"
    rng = rng or random
    candidates = welcome_candidates(uname, now=now, limit=limit, extra_templates=extra_templates)
    candidates = _avoid_recent(candidates, last_key=last_key, last_keys=last_keys)
    if candidates:
        key, msg = rng.choice(candidates)
        return msg, key

    # 昵称太长：改用短模板，必要时截短昵称
    period = period_of(now)
    pool = list(PERIOD_TEMPLATES.get(period) or []) + _combo_templates(period) + list(SHORT_TEMPLATES)
    fitted: list[tuple[str, str]] = []
    for key, tmpl in pool:
        msg = _render_trim(tmpl, uname, limit)
        if msg:
            fitted.append((key, msg))
    fitted = _avoid_recent(fitted, last_key=last_key, last_keys=last_keys)
    if fitted:
        key, msg = rng.choice(fitted)
        return msg, key
    # 最后兜底：截到上限
    fallback = f"欢迎{uname}"[:limit]
    return fallback, "fallback"


class WelcomeGate:
    """进房欢迎的频控：发送间隔、同一人冷却、突发进入时队列封顶。"""

    def __init__(
        self,
        *,
        min_interval: float = 4.5,
        uid_cooldown: float = 600.0,
        max_queue: int = 8,
    ) -> None:
        self.min_interval = min_interval
        self.uid_cooldown = uid_cooldown
        self.max_queue = max_queue
        self._pending: deque[tuple[int, str]] = deque()
        self._last_uid_at: dict[int, float] = {}
        self.last_sent_at: float = 0.0
        self.last_key: Optional[str] = None
        self.last_keys: deque[str] = deque(maxlen=8)

    def accept(self, uid: int, uname: str, *, now: Optional[float] = None) -> bool:
        """能否进入待发送队列。不通过则直接丢弃（不报错）。"""
        now = time.time() if now is None else now
        if len(self._pending) >= self.max_queue:
            return False
        if uid:
            last = self._last_uid_at.get(uid) or 0.0
            if last and now - last < self.uid_cooldown:
                return False
            # 队列里已有同一人
            if any(u == uid for u, _ in self._pending):
                return False
            self._last_uid_at[uid] = now
        self._pending.append((uid, uname))
        return True

    def pending_count(self) -> int:
        return len(self._pending)

    def pop(self) -> Optional[tuple[int, str]]:
        if not self._pending:
            return None
        return self._pending.popleft()

    def seconds_until_ready(self, *, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        if not self.last_sent_at:
            return 0.0
        return max(0.0, self.min_interval - (now - self.last_sent_at))

    def mark_sent(self, key: str, *, now: Optional[float] = None) -> None:
        self.last_sent_at = time.time() if now is None else now
        self.last_key = key
        self.last_keys.append(key)
