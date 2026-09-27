"""
detector.py —— 可疑诱导页面打分器（Python 端）。

评分表与 APK 端 UiHeuristics 完全一致：

    +50   大片空白（blankRatio >= 0.62）
    +30   每命中一类诱导关键词（退出 / 清理 / 奖励 / 陷阱），最多 +60
    +30   假关闭按钮聚簇（同一粗网格区域内 >= 3 个无文字小按钮）
    +20/+25  大量无文字可点节点
    +40   极少节点 + 全屏图（伪装成别的 App 的假截图）
    +40   高危包名
    +15   无可滚动容器

命中条件：
    score >= 80  或  (大片空白 and 命中关键词)  或  (score >= 60 and 高危包名)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

import config
from layout_probe import Node, all_text, parse_nodes


# ----------------------------------------------------------------------
# 结果结构
# ----------------------------------------------------------------------

@dataclass
class Score:
    score: int = 0
    pkg: str = ""
    blank_ratio: float = 0.0
    blank_large: bool = False
    keyword_groups: int = 0
    matched_keywords: List[str] = field(default_factory=list)
    fake_close_count: int = 0
    textless_clickable: int = 0
    node_count: int = 0
    text_node_count: int = 0
    risky_pkg: bool = False
    reasons: List[str] = field(default_factory=list)
    source: str = "uiautomator"      # 或 dumpsys-fallback

    def should_act(self) -> bool:
        if self.score >= config.SCORE_ACT:
            return True
        if self.blank_large and self.keyword_groups > 0:
            return True
        if self.score >= config.SCORE_SOFT and self.risky_pkg:
            return True
        return False

    def summary(self) -> str:
        return (f"分数 {self.score} | 空白 {self.blank_ratio:.0%} | "
                f"关键词类 {self.keyword_groups} | 假关闭 {self.fake_close_count} | "
                f"节点 {self.node_count}")

    def detail(self) -> str:
        if not self.reasons:
            return self.summary()
        return self.summary() + "\n    · " + "\n    · ".join(self.reasons)


@dataclass
class Detection:
    """一次完整探测的结果。"""
    pkg: str = ""
    activity: str = ""
    score: Score = field(default_factory=Score)
    nodes: List[Node] = field(default_factory=list)
    at: float = field(default_factory=time.time)

    def should_act(self) -> bool:
        return self.score.should_act()


# ----------------------------------------------------------------------
# 关键词
# ----------------------------------------------------------------------

def matched_keywords(text: str) -> List[str]:
    hits: List[str] = []
    for group in config.KEYWORD_GROUPS:
        for kw in group:
            if kw and kw in text and kw not in hits:
                hits.append(kw)
    return hits


def keyword_groups_hit(text: str) -> int:
    n = 0
    for group in config.KEYWORD_GROUPS:
        if any(kw in text for kw in group):
            n += 1
    return n


def is_risky_pkg(pkg: str) -> Optional[str]:
    """包名是否带可疑词。白名单（自己人）直接返回 None。

    注意：SELF_PACKAGES 是精确匹配，不是前缀 —— 避免「top.adbguard.fake」
    这类借名伪装被一起放行。
    """
    p = (pkg or "").lower()
    if p in {s.lower() for s in getattr(config, "SELF_PACKAGES", ())}:
        return None
    for hint in config.RISKY_PKG_HINTS:
        if hint in p:
            return hint
    return None


# ----------------------------------------------------------------------
# 几何
# ----------------------------------------------------------------------

def compute_blank_ratio(nodes: List[Node], w: int, h: int,
                        gx: int = 24, gy: int = 48) -> float:
    """
    把屏幕切成 gx*gy 网格，被"有实质内容"的节点覆盖的格子算非空白。

    关键点：全屏图片 / 全屏容器**不算**有内容，
    否则假截图页面（一整张图 + 几个浮层）的空白率会恒等于 0。
    """
    if w <= 0 or h <= 0:
        return 0.0
    full_threshold = w * h * 7 // 10
    grid = [[False] * gx for _ in range(gy)]
    covered = 0

    for n in nodes:
        a = n.area
        if a <= 0:
            continue
        substantive = bool(n.text) or (n.clickable and a < full_threshold)
        if not substantive:
            continue

        cx1 = clamp(n.x1 * gx // w, 0, gx - 1)
        cx2 = clamp(n.x2 * gx // w, 0, gx - 1)
        cy1 = clamp(n.y1 * gy // h, 0, gy - 1)
        cy2 = clamp(n.y2 * gy // h, 0, gy - 1)
        for y in range(cy1, cy2 + 1):
            row = grid[y]
            for x in range(cx1, cx2 + 1):
                if not row[x]:
                    row[x] = True
                    covered += 1

    return 1.0 - covered / float(gx * gy)


def has_fake_close_cluster(guys: List[Node], w: int, h: int) -> int:
    """把无文字小可点节点按 6x12 粗网格分桶，返回最挤的那个桶的大小。"""
    if not guys:
        return 0
    buckets: dict = {}
    for n in guys:
        cx, cy = n.center
        bx = clamp(cx * 6 // max(1, w), 0, 5)
        by = clamp(cy * 12 // max(1, h), 0, 11)
        k = (bx, by)
        buckets[k] = buckets.get(k, 0) + 1
    return max(buckets.values())


def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else (hi if v > hi else v)


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------

def score_nodes(nodes: List[Node], w: int, h: int, pkg: str) -> Score:
    r = Score(pkg=pkg or "")
    r.node_count = len(nodes)
    if not nodes or w <= 0 or h <= 0:
        r.reasons.append("空布局")
        return r

    full_threshold = w * h * 7 // 10

    text_nodes = 0
    textless_clickable = 0
    small_textless: List[Node] = []
    has_fullscreen_image = False
    scrollable_nodes = 0

    for n in nodes:
        if n.text:
            text_nodes += 1
        elif n.clickable:
            textless_clickable += 1
            if 0 < n.area < w * h * 12 // 100:
                small_textless.append(n)
        if n.image_like and n.area >= full_threshold:
            has_fullscreen_image = True
        if n.scrollable:
            scrollable_nodes += 1

    r.text_node_count = text_nodes

    # ---- 1. 空白率 ----
    r.blank_ratio = compute_blank_ratio(nodes, w, h)
    if r.blank_ratio >= config.BLANK_ACT:
        r.blank_large = True
        r.score += 50
        r.reasons.append(f"大片空白 {r.blank_ratio:.0%}（疑似假截图 + 浮层）")
    elif r.blank_ratio >= config.BLANK_SOFT:
        r.reasons.append(f"偏空 {r.blank_ratio:.0%}")

    # ---- 2. 关键词 ----
    text = all_text(nodes)
    r.matched_keywords = matched_keywords(text)
    r.keyword_groups = keyword_groups_hit(text)
    if r.keyword_groups:
        r.score += 30 * r.keyword_groups
        r.reasons.append(
            f"诱导关键词 {r.keyword_groups} 类：" + "、".join(r.matched_keywords[:8])
        )

    # ---- 3. 假关闭按钮聚簇 ----
    r.fake_close_count = len(small_textless)
    best_bucket = has_fake_close_cluster(small_textless, w, h)
    if r.fake_close_count >= 3 and best_bucket >= 3:
        r.score += 30
        r.reasons.append(f"假关闭按钮聚簇 x{r.fake_close_count}（同一区域挤了 {best_bucket} 个）")
    elif r.fake_close_count >= 8:
        r.score += 20
        r.reasons.append(f"大量无文字按钮 x{r.fake_close_count}")

    # ---- 4. 大量无文字可点节点 ----
    if textless_clickable >= 8:
        r.score += 25
        r.reasons.append(f"无文字可点节点 x{textless_clickable}")

    # ---- 5. 假截图页面 ----
    if has_fullscreen_image and len(nodes) <= 8 and text_nodes <= 2:
        r.score += 40
        r.reasons.append("全屏图 + 文字节点极少（伪装成其他 App 的页面）")

    # ---- 6. 高危包名 ----
    hint = is_risky_pkg(pkg)
    if hint:
        r.risky_pkg = True
        r.score += 40
        r.reasons.append(f"高危包名特征: {hint}")

    # ---- 7. 无可滚动容器 ----
    if scrollable_nodes == 0 and textless_clickable >= 5 and len(nodes) >= 10:
        r.score += 15
        r.reasons.append("无可滚动容器（多半是广告壳）")

    r.score = min(r.score, 100)
    return r


# ----------------------------------------------------------------------
# 带设备交互的探测
# ----------------------------------------------------------------------

def fallback_score_from_text(dump_text: str, w: int, h: int, pkg: str) -> Score:
    """
    兜底路径：uiautomator 抓不到时，从 dumpsys activity top 的文本里
    直接做关键词匹配 + 假关闭按钮计数（数 mText 为空的 clickable=true 行）。
    """
    r = Score(pkg=pkg or "")
    r.source = "dumpsys-fallback"
    if not dump_text:
        r.reasons.append("拿不到任何界面信息")
        return r

    r.matched_keywords = matched_keywords(dump_text)
    r.keyword_groups = keyword_groups_hit(dump_text)
    if r.keyword_groups:
        r.score += 30 * r.keyword_groups
        r.reasons.append(f"[兜底] 诱导关键词 {r.keyword_groups} 类："
                         + "、".join(r.matched_keywords[:8]))

    # 有些 ROM 会打印 view 层级，顺便数一下可点节点
    clickable = len(re.findall(r"clickable=true", dump_text))
    textless = len(re.findall(r"mText=\s*\n", dump_text))
    r.node_count = clickable
    r.textless_clickable = max(0, textless)

    if clickable >= 10 and textless >= 6:
        r.score += 30
        r.reasons.append(f"[兜底] 可点节点 {clickable} 个、其中 {textless} 个无文字")

    hint = is_risky_pkg(pkg)
    if hint:
        r.risky_pkg = True
        r.score += 40
        r.reasons.append(f"高危包名特征: {hint}")

    r.score = min(r.score, 100)
    return r


def detect(adb, force_uiautomator: bool = True) -> Detection:
    """抓一次前台界面并打分。"""
    pkg = adb.foreground_package()
    activity = adb.foreground_activity()

    w, h = adb.screen_size()
    xml = adb.dump_layout()

    if xml:
        nodes = parse_nodes(xml)
        sc = score_nodes(nodes, w, h, pkg)
    else:
        nodes = []
        sc = fallback_score_from_text(adb.dump_activity_text(), w, h, pkg)

    return Detection(pkg=pkg, activity=activity, score=sc, nodes=nodes)
