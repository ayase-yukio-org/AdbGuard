"""
selftest.py —— 不需要手机也能验证打分逻辑。

用两段手写的布局 XML 跑一遍：
  ① 一个典型的"流氓清理页"：全屏假截图 + 一堆假关闭按钮 + 诱导文案 + 可疑包名
     → 期望得分 >= 80 且 should_act() == True
  ② 一个正常的列表页：可滚动 + 大量文字
     → 期望得分很低且 should_act() == False

跑法：python selftest.py
"""

from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")      # type: ignore[attr-defined]
except Exception:
    pass

from layout_probe import Node, parse_nodes            # noqa: E402
from detector import score_nodes                      # noqa: E402


W, H = 1080, 2340


# ----------------------------------------------------------------------
# ① 流氓清理页
# ----------------------------------------------------------------------

def scam_xml() -> str:
    nodes = []

    # 全屏假截图（伪装成抖音/购物页）
    nodes.append('<node index="0" text="" resource-id="" class="android.widget.ImageView" '
                 'package="com.clean.superspeed" '
                 'content-desc="" clickable="false" enabled="true" '
                 'bounds="[0,0][1080,2340]" />')

    # 15 个假关闭按钮，全挤在右上角一小块
    for i in range(15):
        x = 880 + (i % 5) * 18
        y = 90 + (i // 5) * 18
        nodes.append(f'<node index="{i+1}" text="" resource-id="" '
                     f'class="android.widget.ImageView" package="com.clean.superspeed" '
                     f'content-desc="" clickable="true" enabled="true" '
                     f'bounds="[{x},{y}][{x+16},{y+16}]" />')

    # 诱导文案
    texts = ["手机清理", "立即优化", "是否退出", "再想想", "狠心离开", "领取奖励", "恭喜获得"]
    for i, t in enumerate(texts):
        y = 800 + i * 90
        nodes.append(f'<node index="{20+i}" text="{t}" resource-id="" '
                     f'class="android.widget.TextView" package="com.clean.superspeed" '
                     f'content-desc="" clickable="false" enabled="true" '
                     f'bounds="[200,{y}][880,{y+70}]" />')

    return '<hierarchy rotation="0">' + "".join(nodes) + "</hierarchy>"


# ----------------------------------------------------------------------
# ② 正常列表页
# ----------------------------------------------------------------------

def normal_xml() -> str:
    nodes = ['<node index="0" text="" resource-id="" class="android.widget.FrameLayout" '
             'package="com.example.notes" content-desc="" clickable="false" '
             'enabled="true" bounds="[0,0][1080,2340]" />']

    nodes.append('<node index="1" text="" resource-id="" '
                 'class="androidx.recyclerview.widget.RecyclerView" '
                 'package="com.example.notes" content-desc="" clickable="false" '
                 'scrollable="true" enabled="true" bounds="[0,200][1080,2200]" />')

    for i in range(20):
        y = 220 + i * 95
        nodes.append(f'<node index="{10+i}" text="会议纪要 第 {i+1} 条 内容正文" '
                     f'resource-id="" class="android.widget.TextView" '
                     f'package="com.example.notes" content-desc="" clickable="false" '
                     f'enabled="true" bounds="[60,{y}][1000,{y+80}]" />')
        nodes.append(f'<node index="{50+i}" text="" resource-id="" '
                     f'class="android.widget.ImageButton" package="com.example.notes" '
                     f'content-desc="更多选项" clickable="true" enabled="true" '
                     f'bounds="[980,{y}][1040,{y+60}]" />')

    return '<hierarchy rotation="0">' + "".join(nodes) + "</hierarchy>"


# ----------------------------------------------------------------------

def run() -> int:
    failed = 0

    print("=" * 74)
    print("  守护喵 · 打分引擎自检（无需真机）")
    print("=" * 74)

    # ---- ① ----
    print()
    print("① 流氓清理页  (package=com.clean.superspeed)")
    nodes = parse_nodes(scam_xml())
    sc = score_nodes(nodes, W, H, "com.clean.superspeed")
    print("   解析到节点:", len(nodes))
    print("   " + sc.summary())
    for r in sc.reasons:
        print("     · " + r)
    ok1 = sc.should_act() and sc.score >= 80
    print("   → should_act =", sc.should_act(), " (期望 True)" )
    if not ok1:
        failed += 1
        print("   ✗ 判定错误！")

    # ---- ② ----
    print()
    print("② 正常列表页  (package=com.example.notes)")
    nodes2 = parse_nodes(normal_xml())
    sc2 = score_nodes(nodes2, W, H, "com.example.notes")
    print("   解析到节点:", len(nodes2))
    print("   " + sc2.summary())
    for r in sc2.reasons:
        print("     · " + r)
    ok2 = not sc2.should_act()
    print("   → should_act =", sc2.should_act(), " (期望 False)")
    if not ok2:
        failed += 1
        print("   ✗ 判定错误！")

    # ---- ③ 高危包名但界面正常，不应直接处置（分数 < 80） ----
    print()
    print("③ 高危包名 + 正常界面  (package=com.toolbox.clean.master)")
    sc3 = score_nodes(nodes2, W, H, "com.toolbox.clean.master")
    print("   " + sc3.summary())
    ok3 = sc3.score < 80 and sc3.risky_pkg
    print("   识别为高危包 =", sc3.risky_pkg, " 分数 =", sc3.score, " (期望 < 80)")
    if not ok3:
        failed += 1
        print("   ✗ 判定错误！")

    print()
    print("=" * 74)
    if failed == 0:
        print("  ✓ 全部通过（3/3）")
    else:
        print(f"  ✗ 有 {failed} 项失败")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
