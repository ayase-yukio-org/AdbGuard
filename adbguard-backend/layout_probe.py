"""
layout_probe.py —— 把 uiautomator 的 XML 变成可打分的节点列表。

Java 端的 UiHeuristics.Node 和这里的 Node 是同一份结构，
两边的打分逻辑刻意写成对称的，方便互相验证。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Tuple

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass
class Node:
    text: str = ""
    cls: str = ""
    pkg: str = ""
    res_id: str = ""
    clickable: bool = False
    scrollable: bool = False
    enabled: bool = True
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0

    @property
    def width(self) -> int:
        return max(0, self.x2 - self.x1)

    @property
    def height(self) -> int:
        return max(0, self.y2 - self.y1)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def empty_text(self) -> bool:
        return not self.text.strip()

    @property
    def image_like(self) -> bool:
        c = self.cls.lower()
        return "imageview" in c or "imagebutton" in c

    @property
    def center(self) -> Tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    def __str__(self) -> str:
        return f"{self.cls}[{self.text}]({self.x1},{self.y1},{self.x2},{self.y2})"


def _to_bool(v: str) -> bool:
    return str(v).strip().lower() == "true"


def parse_bounds(raw: str) -> Tuple[int, int, int, int]:
    m = _BOUNDS_RE.search(raw or "")
    if not m:
        return (0, 0, 0, 0)
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def parse_nodes(xml: str) -> List[Node]:
    """把 hierarchy XML 摊平成节点列表（只保留可见且有信息的节点）。"""
    if not xml or "<hierarchy" not in xml:
        return []

    # uiautomator 偶尔会吐出非法字符，先清一遍
    xml = xml.strip()
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        # 尝试截到最后一个 </hierarchy>
        idx = xml.rfind("</hierarchy>")
        if idx < 0:
            return []
        try:
            root = ET.fromstring(xml[: idx + len("</hierarchy>")])
        except ET.ParseError:
            return []

    out: List[Node] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            tag = child.tag
            if tag == "node":
                a = child.attrib
                x1, y1, x2, y2 = parse_bounds(a.get("bounds", ""))
                txt = (a.get("text") or "").strip()
                desc = (a.get("content-desc") or "").strip()
                if not txt and desc:
                    txt = desc

                visible = _to_bool(a.get("visible-to-user", "true"))
                clickable = _to_bool(a.get("clickable", "false"))
                scrollable = _to_bool(a.get("scrollable", "false"))
                has_info = bool(txt) or clickable or scrollable

                if visible and has_info and (x2 - x1) > 0 and (y2 - y1) > 0:
                    out.append(Node(
                        text=txt,
                        cls=a.get("class", "") or "",
                        pkg=a.get("package", "") or "",
                        res_id=a.get("resource-id", "") or "",
                        clickable=clickable,
                        scrollable=scrollable,
                        enabled=_to_bool(a.get("enabled", "true")),
                        x1=x1, y1=y1, x2=x2, y2=y2,
                    ))
            # 继续递归（有些 ROM 会多包一层）
            walk(child)

    walk(root)
    return out


def all_text(nodes: List[Node]) -> str:
    return "\u0001".join(n.text for n in nodes if n.text)


def classify_text_sources(nodes: List[Node]) -> dict:
    """给日志用的统计。"""
    return {
        "nodes": len(nodes),
        "text_nodes": sum(1 for n in nodes if n.text),
        "clickable": sum(1 for n in nodes if n.clickable),
        "textless_clickable": sum(1 for n in nodes if n.clickable and n.empty_text),
        "scrollable": sum(1 for n in nodes if n.scrollable),
    }
