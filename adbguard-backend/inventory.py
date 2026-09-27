"""
inventory.py —— 全盘体检：找出那批"没名字、没图标、卸不掉的"应用。

对应现象：
  · 手机桌面上突然多出 5~15 个 App
  · 还有 3 个没有名称甚至图标透明的应用（其实是广告/隐私窃取壳）
  · 它们有最高权限、不让卸载、还会往服务器传通讯录/相册/界面

我们用来抓它们的是这 7 个信号（每个独立算分，最后排序）：

  S1 没有启动图标      → 第三方包但不在 LAUNCHER 列表里（+35）★核心特征
  S2 有悬浮窗权限      → SYSTEM_ALERT_WINDOW=allow（+30）★这就是弹窗的来源
  S3 开了无障碍        → 能读屏、能自动点击（+40）★★最危险
  S4 是设备管理员      → 普通方式卸不掉（+30）
  S5 包名可疑          → clean/boost/optimize/toolbox…（+25）
  S6 最近才装上的      → 7 天内首装（+20）
  S7 由浏览器/商店装的 → installerPackageName 是浏览器或下载器（+20）

>  60 分基本可以确定是流氓应用；>= 80 分可以直接处置。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

import config


# ----------------------------------------------------------------------

@dataclass
class AppRisk:
    pkg: str
    score: int = 0
    reasons: List[str] = field(default_factory=list)
    has_launcher: bool = True
    overlay: bool = False
    accessibility: bool = False
    device_admin: bool = False
    installer: str = ""
    first_install: str = ""
    version: str = ""
    label_missing: bool = False

    @property
    def level(self) -> str:
        if self.score >= config.SCORE_ACT:
            return "高危"
        if self.score >= 60:
            return "可疑"
        if self.score >= 30:
            return "观察"
        return "正常"

    def line(self) -> str:
        return (f"[{self.level:2}] {self.score:3}分  {self.pkg}")
    
    def detail_lines(self) -> List[str]:
        out = [f"     包名   : {self.pkg}"]
        if self.version:
            out.append(f"     版本   : {self.version}")
        if self.installer:
            out.append(f"     安装来源: {self.installer}")
        if self.first_install:
            out.append(f"     首装时间: {self.first_install}")
        flags = []
        if not self.has_launcher:
            flags.append("无启动图标")
        if self.overlay:
            flags.append("有悬浮窗")
        if self.accessibility:
            flags.append("开了无障碍")
        if self.device_admin:
            flags.append("设备管理员")
        if self.label_missing:
            flags.append("无应用名")
        if flags:
            out.append("     特征   : " + " / ".join(flags))
        out.append("     判据   : " + "；".join(self.reasons))
        return out


# ----------------------------------------------------------------------

def _parse_launcher_packages(adb) -> Set[str]:
    """从 package resolver 里拿所有带 LAUNCHER 入口的包。"""
    cmds = [
        "cmd package query-activities --brief -a android.intent.action.MAIN "
        "-c android.intent.category.LAUNCHER",
        "pm query-activities --brief -a android.intent.action.MAIN "
        "-c android.intent.category.LAUNCHER",
        "cmd package query-activities -a android.intent.action.MAIN "
        "-c android.intent.category.LAUNCHER | grep -E 'Activity #|packageName'",
    ]
    for c in cmds:
        out = adb.sh(c, timeout=25)
        if not out or "Unknown command" in out or "Error" in out:
            continue
        pkgs = set()
        for m in re.finditer(r"([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+)/[.\w$]*", out):
            pkgs.add(m.group(1))
        if pkgs:
            return pkgs
    return set()


def _dump_package_info(adb) -> Dict[str, dict]:
    """一次性 dump 全部包信息并解析成 dict，避免逐个包开子进程。"""
    raw = adb.sh("dumpsys package", timeout=60)
    if not raw:
        return {}

    result: Dict[str, dict] = {}
    blocks = re.split(r"\n\s{0,6}Package \[", raw)
    for block in blocks[1:]:
        name = block.split("]", 1)[0].strip()
        if not re.match(r"^[a-zA-Z][\w.]*$", name):
            continue
        info: dict = {}

        m = re.search(r"firstInstallTime=([^\n]+)", block)
        if m:
            info["first_install"] = m.group(1).strip()
        m = re.search(r"lastUpdateTime=([^\n]+)", block)
        if m:
            info["last_update"] = m.group(1).strip()
        m = re.search(r"installerPackageName=([^\s\n]+)", block)
        if m:
            info["installer"] = m.group(1).strip()
        m = re.search(r"versionName=([^\s\n]+)", block)
        if m:
            info["version"] = m.group(1).strip()
        m = re.search(r"labelRes=0x0\b", block)
        if m:
            info["label_missing"] = True
        m = re.search(r"codePath=([^\s\n]+)", block)
        if m:
            info["code_path"] = m.group(1).strip()
        m = re.search(r"flags=\[([^\]]*)\]", block)
        if m:
            info["flags"] = m.group(1).strip()

        result[name] = info
    return result


def _parse_time(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _is_ad_installer(installer: str) -> bool:
    if not installer:
        return False
    low = installer.lower()
    for h in config.AD_LANDING_HINTS:
        if h in low:
            return True
    if "browser" in low or "market" in low or "download" in low:
        return True
    return False


# ----------------------------------------------------------------------

def scan(adb, recent_days: int = 7, verbose: bool = True) -> List[AppRisk]:
    """全盘体检，返回按风险分从高到低排序的列表。"""
    if verbose:
        print("· 读取设备信息…")
    model = adb.model()
    ver = adb.android_version()
    if verbose:
        print(f"  设备: {model} / Android {ver}")

    if verbose:
        print("· 枚举第三方应用…")
    pkgs = adb.third_party_packages()
    if verbose:
        print(f"  共 {len(pkgs)} 个第三方包")

    if verbose:
        print("· 抓取启动器列表（判断谁没有图标）…")
    launcher = _parse_launcher_packages(adb)
    launcher_ok = bool(launcher)
    if verbose:
        print(f"  带启动图标的只有 {len(launcher)} 个")

    if verbose:
        print("· 抓取无障碍 / 设备管理员 / 悬浮窗名单…")
    a11y_raw = adb.enabled_accessibility()
    a11y_pkgs = {s.split("/", 1)[0] for s in a11y_raw if "/" in s}
    admins = set(adb.device_admins())

    if verbose:
        print("· dump 包信息（安装来源 / 首装时间）…")
    info = _dump_package_info(adb)

    if verbose:
        print("· 逐个算风险分…")

    cutoff = datetime.now() - timedelta(days=recent_days)
    results: List[AppRisk] = []

    for pkg in pkgs:
        # 白名单：自己人直接跳过 —— 守护喵自身持悬浮窗 + 常驻无障碍，
        # 特征和流氓应用完全一致，不排除就会自己把自己打成「高危」。
        if pkg in getattr(config, "SELF_PACKAGES", ()):  # noqa
            continue

        r = AppRisk(pkg=pkg)
        meta = info.get(pkg, {})
        r.installer = meta.get("installer", "")
        r.version = meta.get("version", "")
        r.first_install = meta.get("first_install", "")
        r.label_missing = bool(meta.get("label_missing"))

        # S1 无启动图标
        if launcher_ok:
            r.has_launcher = pkg in launcher
            if not r.has_launcher:
                r.score += 35
                r.reasons.append("S1 没有启动图标（桌面上看不见，只能被别的 App 拉起来）")

        # S2 悬浮窗
        try:
            r.overlay = adb.overlay_allowed(pkg)
        except Exception:
            r.overlay = False
        if r.overlay:
            r.score += 30
            r.reasons.append("S2 持有悬浮窗权限（所有假关闭按钮/弹窗都靠它）")

        # S3 无障碍
        r.accessibility = pkg in a11y_pkgs
        if r.accessibility:
            r.score += 40
            r.reasons.append("S3 开启并常驻无障碍服务（可读屏、可自动点击、可偷当前界面）")

        # S4 设备管理员
        r.device_admin = pkg in admins
        if r.device_admin:
            r.score += 30
            r.reasons.append("S4 已激活设备管理员（这就是「卸不掉」的原因）")

        # S5 包名可疑
        low = pkg.lower()
        hit = next((h for h in config.RISKY_PKG_HINTS if h in low), None)
        if hit:
            r.score += 25
            r.reasons.append(f"S5 包名带可疑词：{hit}")

        # S6 最近安装
        t = _parse_time(r.first_install)
        if t and t > cutoff:
            days = (datetime.now() - t).days
            r.score += 20
            r.reasons.append(f"S6 最近才装上（{days} 天前，{r.first_install}）")

        # S7 广告来源安装
        if _is_ad_installer(r.installer):
            r.score += 20
            r.reasons.append(f"S7 由浏览器/下载器装进来的：{r.installer}")

        if r.label_missing:
            r.score += 10
            r.reasons.append("附加 包内没有应用名字符串（伪装成系统组件）")

        results.append(r)

    results.sort(key=lambda x: (-x.score, x.pkg))
    return results


def print_report(results: List[AppRisk], top: int = 40) -> None:
    """把体检结果打成给人看的报告。"""
    bad = [r for r in results if r.score >= 60]
    watch = [r for r in results if 30 <= r.score < 60]

    print()
    print("=" * 78)
    print(f"  体检报告   高危/可疑 {len(bad)} 个   观察 {len(watch)} 个   "
          f"总共扫描 {len(results)} 个第三方应用")
    print("=" * 78)

    if not bad:
        print("  ✓ 没有发现高危应用。")
    else:
        print()
        print("  ⚠ 建议立即处置：")
        for r in bad[:top]:
            print()
            print(f"  {r.line()}")
            for ln in r.detail_lines():
                print(ln)

    if watch:
        print()
        print("-" * 78)
        print("  观察名单（分不高，但有你留个心眼）：")
        for r in watch[:top]:
            print(f"  {r.line()}")

    print()
    print("=" * 78)
