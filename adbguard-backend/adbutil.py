"""
adbutil.py —— ADB 薄封装。

只用标准库 subprocess，不依赖 adb-shell / pure-python-adb 之类的包。
所有方法都做了超时 + 错误吞掉（返回空串），保证监测循环不会因为
某一台设备抽风就整个崩掉。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from typing import List, Optional, Tuple

import config


class AdbError(RuntimeError):
    pass


# 前台包名匹配：从 "com.xxx.yyy/.MainActivity" 里抠出包名
_PKG_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+)/")
_WINDOW_RE = re.compile(r"(?:mCurrentFocus|mFocusedApp|mFocusedWindow)=.*?(?P<pkg>[a-zA-Z][\w.]*)/")
_RESUMED_RE = re.compile(r"(?:mResumedActivity|topResumedActivity|mLastPausedActivity).*?(?P<pkg>[a-zA-Z][\w.]*)/")


class Adb:
    """一台设备上的 adb 门面。"""

    def __init__(self, serial: Optional[str] = None, adb_path: Optional[str] = None):
        self.serial = serial
        self.adb = adb_path or config.ADB_PATH
        self._cached_size: Optional[Tuple[int, int]] = None

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------

    def _base(self) -> List[str]:
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def raw(self, *args: str, timeout: int = 20, text: bool = True):
        """裸跑一条 adb 命令。返回 (returncode, stdout, stderr)。"""
        cmd = self._base() + [str(a) for a in args]
        try:
            p = subprocess.run(
                cmd, capture_output=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            return p.returncode, (p.stdout or ""), (p.stderr or "")
        except subprocess.TimeoutExpired:
            return -1, "", "TIMEOUT"
        except FileNotFoundError:
            raise AdbError(
                f"找不到 adb 可执行文件（{self.adb}）。请把 platform-tools 加进 PATH，"
                f"或修改 config.ADB_PATH 为绝对路径。"
            )
        except Exception as e:  # noqa: BLE001
            return -1, "", str(e)

    def shell(self, *args: str, timeout: int = 20) -> str:
        """adb shell <args>，返回 stdout（已去尾部空白）。"""
        rc, out, err = self.raw("shell", *args, timeout=timeout)
        return out.strip()

    def sh(self, command: str, timeout: int = 20) -> str:
        """adb shell -c "<command>"，方便用管道和重定向。"""
        rc, out, err = self.raw("shell", command, timeout=timeout)
        return out.strip()

    def ok(self, *args: str, timeout: int = 20) -> bool:
        rc, out, err = self.raw(*args, timeout=timeout)
        return rc == 0

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    @staticmethod
    def which() -> Optional[str]:
        return shutil.which(config.ADB_PATH)

    @staticmethod
    def list_devices() -> List[Tuple[str, str]]:
        """返回 [(serial, state)]，state 一般是 device / offline / unauthorized。"""
        try:
            p = subprocess.run([config.ADB_PATH, "devices"], capture_output=True,
                               timeout=15, encoding="utf-8", errors="replace")
        except Exception:
            return []
        out = []
        for line in (p.stdout or "").splitlines()[1:]:
            line = line.strip()
            if not line or "\t" not in line:
                continue
            serial, _, state = line.partition("\t")
            out.append((serial.strip(), state.strip()))
        return out

    @staticmethod
    def auto() -> "Adb":
        """自动挑一台已授权设备。"""
        for serial, state in Adb.list_devices():
            if state == "device":
                return Adb(serial)
        raise AdbError("没有可用的设备。请 `adb devices` 检查，或先 `adb connect ip:port`。")

    def connect(self, hostport: str) -> str:
        return self.raw("connect", hostport, timeout=20)[1].strip()

    def disconnect(self, hostport: str = "") -> str:
        args = ["disconnect"] + ([hostport] if hostport else [])
        return self.raw(*args, timeout=15)[1].strip()

    def pair(self, hostport: str, code: str) -> str:
        """无线调试配对（Android 11+）。"""
        return self.raw("pair", hostport, code, timeout=30)[1].strip()

    # ------------------------------------------------------------------
    # 设备信息
    # ------------------------------------------------------------------

    def model(self) -> str:
        return self.sh("getprop ro.product.model") or "unknown"

    def android_version(self) -> str:
        return self.sh("getprop ro.build.version.release") or "?"

    def sdk(self) -> int:
        try:
            return int(self.sh("getprop ro.build.version.sdk") or 0)
        except ValueError:
            return 0

    def is_root(self) -> bool:
        return "uid=0" in self.sh("id")

    def is_online(self) -> bool:
        """设备是否在线可用。

        ⚠ 历史坑：这里曾经写成 `return "device" in self.sh("echo ok")`。
        但 `echo ok` 的输出就是 "ok"，里面永远不含 "device" 一词，
        于是本函数**恒为 False** —— 且因为只有 watch 走 wait_for_device，
        scan / kill 等其他命令全都正常，症状表现为
        「scan 能用，watch 永远说设备未就绪」。

        正确判据：命令真的跑通并原样回显 "ok"。设备 offline / unauthorized 时
        adb 会返回空串或错误信息，不会等于 "ok"。
        """
        try:
            return self.sh("echo ok", timeout=10).strip() == "ok"
        except Exception:
            return False

    def screen_size(self) -> Tuple[int, int]:
        """返回 (宽, 高)。优先 wm size 的 Override 值。"""
        if self._cached_size:
            return self._cached_size
        raw = self.sh("wm size")
        m = re.search(r"Override size:\s*(\d+)x(\d+)", raw)
        if not m:
            m = re.search(r"Physical size:\s*(\d+)x(\d+)", raw)
        if m:
            self._cached_size = (int(m.group(1)), int(m.group(2)))
        else:
            self._cached_size = (1080, 2340)
        return self._cached_size

    def screen_on(self) -> bool:
        return "mScreenOn=true" in self.sh("dumpsys power") or "state=ON" in self.sh("dumpsys display")

    # ------------------------------------------------------------------
    # 前台应用
    # ------------------------------------------------------------------

    def foreground_package(self) -> str:
        """多路兜底拿前台包名。"""
        # 路 1：dumpsys window
        out = self.sh("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp|mFocusedWindow'")
        m = _WINDOW_RE.search(out)
        if m:
            return m.group("pkg")

        # 路 2：dumpsys activity activities
        out = self.sh("dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivity'")
        m = _RESUMED_RE.search(out)
        if m:
            return m.group("pkg")

        # 路 3：dumpsys activity top
        out = self.sh("dumpsys activity top | grep -E 'ACTIVITY'")
        m = _PKG_RE.search(out)
        if m:
            return m.group(1)

        return ""

    def foreground_activity(self) -> str:
        out = self.sh("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
        m = re.search(r"([a-zA-Z][\w.]*/[.\w$]+)", out)
        return m.group(1) if m else ""

    # ------------------------------------------------------------------
    # 应用操作
    # ------------------------------------------------------------------

    def force_stop(self, pkg: str) -> bool:
        return self.ok("shell", "am", "force-stop", pkg)

    def kill_bg(self, pkg: str) -> bool:
        return self.ok("shell", "am", "kill", pkg)

    def clear_data(self, pkg: str) -> bool:
        return self.ok("shell", "pm", "clear", pkg)

    def disable_user(self, pkg: str) -> str:
        """pm disable-user --user 0 —— shell 身份即可执行，是最狠的"卸载替代品"。"""
        return self.sh(f"pm disable-user --user 0 {pkg}")

    def enable_user(self, pkg: str) -> str:
        return self.sh(f"pm enable {pkg}")

    def uninstall_user(self, pkg: str) -> str:
        """只卸载当前用户，系统分区里的残留不受影响。"""
        return self.sh(f"pm uninstall --user 0 {pkg}")

    def suspend(self, pkg: str) -> str:
        return self.sh(f"pm suspend --user 0 {pkg}")

    def unsuspend(self, pkg: str) -> str:
        return self.sh(f"pm unsuspend --user 0 {pkg}")

    def path_of(self, pkg: str) -> str:
        return self.sh(f"pm path {pkg}")

    def is_system_pkg(self, pkg: str) -> bool:
        return "system" in self.sh(f"pm path {pkg}") and self.sh(f"pm path {pkg}").startswith("/system")

    def grant(self, pkg: str, perm: str) -> bool:
        return self.ok("shell", "pm", "grant", pkg, perm)

    def revoke(self, pkg: str, perm: str) -> bool:
        return self.ok("shell", "pm", "revoke", pkg, perm)

    def third_party_packages(self, include_disabled: bool = True) -> List[str]:
        flag = "-3" + (" -u" if include_disabled else "")
        out = self.sh(f"pm list packages {flag}")
        pkgs = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                pkgs.append(line[len("package:"):].strip())
        return sorted(set(pkgs))

    # ------------------------------------------------------------------
    # appops
    # ------------------------------------------------------------------

    def appop_get(self, pkg: str, op: str) -> str:
        return self.sh(f"appops get {pkg} {op}")

    def appop_set(self, pkg: str, op: str, mode: str) -> bool:
        """mode: allow / ignore / deny / default"""
        return self.ok("shell", "appops", "set", pkg, op, mode)

    def overlay_allowed(self, pkg: str) -> bool:
        out = self.appop_get(pkg, "SYSTEM_ALERT_WINDOW")
        return "allow" in out.lower() and "deny" not in out.lower()

    # ------------------------------------------------------------------
    # 无障碍 / 设备管理员
    # ------------------------------------------------------------------

    def enabled_accessibility(self) -> List[str]:
        out = self.sh("settings get secure enabled_accessibility_services")
        if not out or out == "null":
            return []
        return [s.strip() for s in out.replace(":", ";").split(";") if s.strip()]

    def has_accessibility(self, pkg: str) -> bool:
        return any(s.startswith(pkg + "/") for s in self.enabled_accessibility())

    def device_admins(self) -> List[str]:
        out = self.sh("dumpsys device_policy")
        return sorted(set(re.findall(r"([a-zA-Z][\w.]*)/[.\w$]*AdminReceiver", out))
                      | set(re.findall(r"ComponentInfo\{([\w.]+)/", out)))

    def is_device_owner(self, pkg: str) -> bool:
        out = self.sh("dumpsys device_policy")
        return f"Device Owner: {pkg}" in out or f"deviceOwnerPackageName={pkg}" in out

    def set_device_owner(self, component: str) -> str:
        """component 形如 top.adbguard/.GuardDeviceAdminReceiver"""
        rc, out, err = self.raw("shell", "dpm", "set-device-owner", component, timeout=30)
        return (out + "\n" + err).strip()

    def remove_active_admin(self, component: str) -> str:
        return self.sh(f"dpm remove-active-admin {component}")

    # ------------------------------------------------------------------
    # 布局抓取
    # ------------------------------------------------------------------

    def dump_layout(self) -> str:
        """
        用 uiautomator dump 抓当前界面节点树，返回 XML 文本。
        失败时返回空串（调用方应退回 dumpsys 文本特征）。
        """
        remote = "/sdcard/adbguard_ui.xml"
        self.sh(f"rm -f {remote}")
        rc, out, err = self.raw("shell", "uiautomator", "dump", remote,
                                timeout=config.LAYOUT_TIMEOUT)
        if "dumped" not in (out + err).lower() and rc != 0:
            # 有些 ROM 的 uiautomator 输出格式不同，再试一次默认路径
            self.raw("shell", "uiautomator", "dump", timeout=config.LAYOUT_TIMEOUT)

        xml = self.sh(f"cat {remote}", timeout=15)
        if "<hierarchy" not in xml:
            xml = self.sh("cat /sdcard/window_dump.xml", timeout=15)
        return xml if "<hierarchy" in xml else ""

    def dump_activity_text(self, lines: int = 400) -> str:
        """
        布局抓取失败时的兜底：把 dumpsys activity top 打出来。
        很多 ROM 的 View Hierarchy 里带 mText / text= 字段，足够做关键词匹配。
        """
        return self.sh(f"dumpsys activity top | head -n {lines}", timeout=15)

    def screenshot(self, local_path: str) -> bool:
        """截图到电脑（exec-out 避免二进制被 shell 污染）。"""
        try:
            with open(local_path, "wb") as f:
                p = subprocess.run(self._base() + ["exec-out", "screencap", "-p"],
                                   stdout=f, timeout=30, stderr=subprocess.DEVNULL)
            return p.returncode == 0
        except Exception:
            return False

    # ------------------------------------------------------------------

    def wait_for_device(self, timeout: float = 30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.is_online():
                return True
            time.sleep(1.0)
        return False

    def __repr__(self) -> str:
        return f"<Adb {self.serial or 'auto'}>"
