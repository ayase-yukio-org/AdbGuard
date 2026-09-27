"""
main.py —— 守护喵后端命令行。

常用：

  python main.py devices                     看有哪些设备
  python main.py scan                        全盘体检（找那 3 个隐身应用）
  python main.py scan --fix                  体检 + 直接处置所有高危
  python main.py watch                       实时监测（核心功能）
  python main.py watch --fix                 监测到可疑页面就自动处置
  python main.py kill com.xxx.yyy            单杀一个
  python main.py quarantine com.xxx.yyy      拔刺组合拳（撤权限+解管理员+杀进程）
  python main.py disable com.xxx.yyy         让它冻结（图标消失、起不来）
  python main.py restore com.xxx.yyy         恢复
  python main.py promote                     把守护喵提升为 Device Owner
  python main.py demote                      撤销 Device Owner（退回可卸载状态）
  python main.py serve                       起后端，等手机来连（HTTP + UDP 发现）
  python main.py connect 192.168.1.7:5555    无线调试连接
  python main.py pair 192.168.1.7:43211 123456   无线调试配对

监测逻辑（watch）：
  每 1.2 秒做一次：
    ① dumpsys window 拿前台包名
    ② uiautomator dump 抓节点树 → detector 打分
    ③ 判定：score >= 80  或  (大片空白 且 命中诱导关键词)  或  (score >= 60 且 包名高危)
    ④ 命中 → 打印证据 → （--fix 时）直接拔刺：撤无障碍 / 解管理员 / 撤悬浮窗 /
       撤通讯录相册权限 / force-stop
    ⑤ 同一个包 12 秒内只处置一次，避免刷屏
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional, Set

# Windows 控制台默认 GBK，中文会炸，统一改成 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")      # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")      # type: ignore[attr-defined]
except Exception:
    pass

import config
import detector
import inventory
import remediate
from adbutil import Adb, AdbError


# ----------------------------------------------------------------------
# 颜色
# ----------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty()


def c(text: str, code: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def red(t: str) -> str:    return c(t, "91")
def green(t: str) -> str:  return c(t, "92")
def yellow(t: str) -> str: return c(t, "93")
def blue(t: str) -> str:   return c(t, "94")
def dim(t: str) -> str:    return c(t, "90")
def bold(t: str) -> str:   return c(t, "1")


def banner() -> None:
    print()
    print(bold(blue("  ╔══════════════════════════════════════════════════════╗")))
    print(bold(blue("  ║  守护喵 · 后端（ADB）      AdbGuard Backend v1.0      ║")))
    print(bold(blue("  ╚══════════════════════════════════════════════════════╝")))
    print(dim("  针对：伪装成「清理/优化」的流氓应用、假关闭按钮诱导弹窗、"))
    print(dim("        隐藏图标 + 无障碍读屏 + 偷通讯录相册的静默应用"))
    print()


# ----------------------------------------------------------------------
# 设备选择
# ----------------------------------------------------------------------

def pick_adb(args) -> Adb:
    if getattr(args, "serial", None):
        return Adb(args.serial)
    try:
        adb = Adb.auto()
    except AdbError as e:
        print(red(f"✗ {e}"))
        print()
        print_devices()
        sys.exit(2)
    return adb


def print_devices() -> None:
    print("当前 ADB 可见设备：")
    found = Adb.list_devices()
    if not found:
        print(dim("  （没有）"))
        print()
        print("排查顺序：")
        print("  1. 数据线插上，手机弹出「允许 USB 调试」→ 点允许")
        print("  2. 或无线调试：设置 → 开发者选项 → 无线调试 → 用「使用配对码配对设备」")
        print("     然后：python main.py pair <IP:配对端口> <配对码>")
        print("           python main.py connect <IP:连接端口>")
        return
    for serial, state in found:
        color = green if state == "device" else yellow
        print(f"  {color(serial)}   {state}")


# ----------------------------------------------------------------------
# watch —— 核心
# ----------------------------------------------------------------------

def cmd_watch(args) -> int:
    adb = pick_adb(args)
    banner()

    if not adb.wait_for_device(20):
        print(red("✗ 设备未就绪。"))
        print_devices()
        return 2

    print(f"  设备   : {bold(adb.model())} / Android {adb.android_version()}")
    print(f"  模式   : {'自动处置' if args.fix else '仅告警（加 --fix 才会动手）'}")
    print(f"  节奏   : 每 {config.WATCH_INTERVAL}s 抓一次界面")
    print(f"  阈值   : 综合分 >= {config.SCORE_ACT}，或 (大片空白 + 诱导关键词)")
    print(dim("  Ctrl+C 停止"))
    print()

    handled: dict = {}          # pkg -> 上次处置时间
    last_pkg = ""
    rounds = 0

    try:
        while True:
            rounds += 1
            try:
                det = detector.detect(adb)
            except Exception as e:  # noqa: BLE001
                print(dim(f"[{time.strftime('%H:%M:%S')}] 探测异常：{e}"))
                time.sleep(config.WATCH_INTERVAL)
                continue

            pkg = det.pkg
            if pkg and pkg != last_pkg:
                print(dim(f"[{time.strftime('%H:%M:%S')}] 前台 → {pkg}"))
                last_pkg = pkg

            if det.should_act():
                now = time.time()
                last = handled.get(pkg, 0)
                if now - last < config.SAME_PKG_COOLDOWN:
                    time.sleep(config.WATCH_INTERVAL)
                    continue
                handled[pkg] = now

                print()
                print(red("  ┌─ 命中可疑诱导页面 ─────────────────────────────────"))
                print(red("  │ ") + bold(pkg))
                if det.activity:
                    print(red("  │ ") + dim(det.activity))
                print(red("  │ ") + det.score.summary())
                for reason in det.score.reasons:
                    print(red("  │ ") + "  · " + reason)
                print(red("  └────────────────────────────────────────────────────"))

                if args.fix:
                    res = remediate.quarantine(
                        adb, pkg,
                        also_disable=config.AUTO_DISABLE if not args.disable else True,
                    )
                    for s in res.steps:
                        print("     " + (green(s) if s.startswith("✓") else red(s)))
                    print()
                else:
                    print(dim("     （仅告警模式，未处置。加 --fix 或手动 python main.py quarantine "
                              + pkg + "）"))
                    print()

            time.sleep(config.WATCH_INTERVAL)

    except KeyboardInterrupt:
        print()
        print(f"  已停止。共扫描 {rounds} 轮，处置过 {len(handled)} 个应用。")
        if handled:
            for p in handled:
                print("    · " + p)
        return 0


# ----------------------------------------------------------------------
# scan
# ----------------------------------------------------------------------

def cmd_scan(args) -> int:
    adb = pick_adb(args)
    banner()

    t0 = time.time()
    results = inventory.scan(adb, recent_days=args.days)
    inventory.print_report(results, top=args.top)
    print(dim(f"  用时 {time.time() - t0:.1f}s"))

    if args.fix:
        bad = [r.pkg for r in results if r.score >= config.SCORE_ACT]
        if not bad:
            print(green("  没有需要处置的高危应用。"))
            return 0
        print()
        print(yellow(f"  即将处置 {len(bad)} 个高危应用："))
        for p in bad:
            print("    · " + p)
        if config.CONFIRM_BEFORE_DISABLE and not args.yes:
            ans = input("\n  确认？这会撤销它们的权限并杀掉进程 (y/N): ").strip().lower()
            if ans not in ("y", "yes"):
                print("  已取消。")
                return 0
        remediate.bulk_quarantine(adb, bad, also_disable=args.disable)

    return 0


# ----------------------------------------------------------------------
# 单点操作
# ----------------------------------------------------------------------

def cmd_kill(args) -> int:
    adb = pick_adb(args)
    ok = remediate.kill(adb, args.pkg)
    print(green(f"✓ 已 force-stop {args.pkg}") if ok else red("✗ 失败"))
    return 0 if ok else 1


def cmd_quarantine(args) -> int:
    adb = pick_adb(args)
    banner()
    res = remediate.quarantine(adb, args.pkg, also_disable=args.disable,
                               also_uninstall=args.uninstall)
    print(res.text())
    return 0 if res.ok else 1


def cmd_disable(args) -> int:
    adb = pick_adb(args)
    out = remediate.disable_pkg(adb, args.pkg)
    print(f"pm disable-user → {out.strip() or '(无输出)'}")
    remediate.kill(adb, args.pkg)
    print(green(f"✓ {args.pkg} 已冻结（图标会消失、无法自启）"))
    print(dim("  想恢复：python main.py restore " + args.pkg))
    return 0


def cmd_restore(args) -> int:
    adb = pick_adb(args)
    out = remediate.enable_pkg(adb, args.pkg)
    print(f"pm enable → {out.strip() or '(无输出)'}")
    return 0


def cmd_uninstall(args) -> int:
    adb = pick_adb(args)
    out = remediate.uninstall_pkg(adb, args.pkg)
    print(f"pm uninstall --user 0 → {out.strip() or '(无输出)'}")
    return 0


def cmd_promote(args) -> int:
    adb = pick_adb(args)
    banner()
    print("  正在把守护喵提升为 Device Owner…")
    print(dim("  提升后 APK 自己就能：隐藏/挂起流氓应用、直接撤销它的权限"))
    print()
    print("  " + remediate.promote_device_owner(adb, args.component))
    print()
    print(dim("  想撤销？ python main.py demote"))
    return 0


def cmd_demote(args) -> int:
    adb = pick_adb(args)
    banner()
    print("  正在撤销守护喵的 Device Owner 身份…")
    print(dim("  撤销后 APK 退回普通应用，可以在系统设置里正常卸载"))
    print()
    print("  " + remediate.demote_device_owner(adb, args.component))
    return 0


def cmd_connect(args) -> int:
    adb = Adb()
    print(adb.connect(args.hostport))
    print_devices()
    return 0


def cmd_pair(args) -> int:
    adb = Adb()
    print(adb.pair(args.hostport, args.code))
    print_devices()
    return 0


def cmd_devices(args) -> int:
    print_devices()
    return 0


def cmd_serve(args) -> int:
    import server
    adb = None
    try:
        adb = Adb.auto()
    except Exception:
        pass
    server.serve(adb=adb, port=args.port, verbose=args.verbose)
    return 0


def cmd_revoke_overlay(args) -> int:
    """一键列出并撤销所有第三方应用的悬浮窗权限 —— 专治"弹窗关不掉"。"""
    adb = pick_adb(args)
    banner()
    pkgs = adb.third_party_packages()
    print(f"  扫描 {len(pkgs)} 个第三方应用，查找持有悬浮窗权限的…")
    hit: List[str] = []
    for p in pkgs:
        try:
            if adb.overlay_allowed(p):
                hit.append(p)
                print("    " + yellow("! ") + p)
        except Exception:
            continue
    if not hit:
        print(green("  ✓ 没有第三方应用持有悬浮窗权限。"))
        return 0
    print()
    print(red(f"  共 {len(hit)} 个应用有悬浮窗权限 —— 这些就是弹窗的来源。"))
    if args.yes or input("  全部撤销？(y/N): ").strip().lower() in ("y", "yes"):
        for p in hit:
            remediate.revoke_overlay(adb, p)
            remediate.kill(adb, p)
            print(green("    ✓ 已撤销并杀掉 ") + p)
    return 0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="守护喵后端 —— 用 ADB 抓伪装成清理/优化的流氓应用",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--serial", help="指定设备序列号（多设备时用）")

    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("devices", help="列出 ADB 设备")
    sp.set_defaults(func=cmd_devices)

    sp = sub.add_parser("watch", help="实时监测（核心）")
    sp.add_argument("--fix", action="store_true", help="命中就自动处置")
    sp.add_argument("--disable", action="store_true", help="处置时一并 pm disable-user")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("scan", help="全盘体检，找隐身/高危应用")
    sp.add_argument("--fix", action="store_true", help="体检完直接处置高危应用")
    sp.add_argument("--disable", action="store_true", help="处置时一并 pm disable-user")
    sp.add_argument("--days", type=int, default=7, help="多少天内安装的算「最近」（默认 7）")
    sp.add_argument("--top", type=int, default=40, help="最多显示多少条")
    sp.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("kill", help="强杀一个包")
    sp.add_argument("pkg")
    sp.set_defaults(func=cmd_kill)

    sp = sub.add_parser("quarantine", help="拔刺组合拳")
    sp.add_argument("pkg")
    sp.add_argument("--disable", action="store_true")
    sp.add_argument("--uninstall", action="store_true")
    sp.set_defaults(func=cmd_quarantine)

    sp = sub.add_parser("disable", help="冻结（pm disable-user）")
    sp.add_argument("pkg")
    sp.set_defaults(func=cmd_disable)

    sp = sub.add_parser("restore", help="恢复被冻结的应用")
    sp.add_argument("pkg")
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("uninstall", help="为当前用户卸载")
    sp.add_argument("pkg")
    sp.set_defaults(func=cmd_uninstall)

    sp = sub.add_parser("revoke-overlay", help="一键撤销所有第三方应用的悬浮窗权限")
    sp.add_argument("--yes", "-y", action="store_true")
    sp.set_defaults(func=cmd_revoke_overlay)

    sp = sub.add_parser("promote", help="把守护喵提升为 Device Owner")
    sp.add_argument("--component", default="top.adbguard/.GuardDeviceAdminReceiver")
    sp.set_defaults(func=cmd_promote)

    sp = sub.add_parser("demote", help="撤销 Device Owner（退回可卸载状态）")
    sp.add_argument("--component", default="top.adbguard/.GuardDeviceAdminReceiver")
    sp.set_defaults(func=cmd_demote)

    sp = sub.add_parser("serve", help="起后端，等手机连（HTTP + UDP 发现）")
    sp.add_argument("--port", type=int, default=config.HTTP_PORT)
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("connect", help="无线调试连接 ip:port")
    sp.add_argument("hostport")
    sp.set_defaults(func=cmd_connect)

    sp = sub.add_parser("pair", help="无线调试配对 ip:port 配对码")
    sp.add_argument("hostport")
    sp.add_argument("code")
    sp.set_defaults(func=cmd_pair)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "cmd", None):
        banner()
        parser.print_help()
        return 0

    if Adb.which() is None:
        print(red(f"✗ 找不到 adb（{config.ADB_PATH}）"))
        print("  请安装 Android Platform-Tools 并加入 PATH，")
        print("  或在 config.py 里把 ADB_PATH 改成绝对路径，例如：")
        print('     ADB_PATH = r"C:\\platform-tools\\adb.exe"')
        return 2

    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
