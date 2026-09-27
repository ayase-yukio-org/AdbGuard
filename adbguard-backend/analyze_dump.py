#!/usr/bin/env python3
"""analyze_dump.py —— 离线分析一份 uiautomator dump。

为什么需要它：
  真机上抓到的可疑页面，dump 下来存成 XML 后可以用这个脚本反复复算打分，
  不连设备、不打断正在跑的 watch，方便调权重和复现问题。

用法：
    # 先抓一份
    adb shell uiautomator dump /sdcard/w.xml
    adb exec-out cat /sdcard/w.xml > w.xml

    # 再分析
    python analyze_dump.py w.xml --pkg com.hihonor.search --size 1080x2388
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

import config
import detector
import layout_probe


def main() -> int:
    ap = argparse.ArgumentParser(description="离线分析 uiautomator dump")
    ap.add_argument("xml", help="dump 文件路径")
    ap.add_argument("--pkg", default="", help="包名（影响「高危包名」判据）")
    ap.add_argument("--size", default="1080x2388", help="屏幕尺寸，如 1080x2388")
    args = ap.parse_args()

    try:
        w, h = (int(v) for v in args.size.lower().split("x"))
    except Exception:
        print(f"✗ 尺寸格式不对：{args.size}（应为 1080x2388）")
        return 2

    with open(args.xml, encoding="utf-8", errors="replace") as f:
        xml = f.read()

    nodes = layout_probe.parse_nodes(xml)
    if not nodes:
        print("✗ 没解析出节点。")
        print("  可能原因：文件不是 uiautomator dump，或该页面是纯自绘 / WebView")
        print("  （WebView 里的内容 uiautomator 读不到，只能靠截图做图像分析）。")
        return 1

    sc = detector.score_nodes(nodes, w, h, args.pkg)

    print("=" * 72)
    print(f"  离线分析   {args.xml}")
    print("=" * 72)
    print(f"  屏幕     : {w} x {h}")
    print(f"  包名     : {args.pkg or '(未指定)'}")
    print(f"  节点数   : {sc.node_count}")
    print("-" * 72)
    print(json.dumps(asdict(sc), ensure_ascii=False, indent=2, default=str))
    print("-" * 72)
    hit = sc.score >= config.SCORE_ACT
    print(f"  综合分   : {sc.score}")
    print(f"  是否命中 : {'是 ⚠' if hit else '否'}（阈值 {config.SCORE_ACT}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
