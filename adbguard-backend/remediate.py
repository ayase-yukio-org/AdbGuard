"""
remediate.py —— 处置动作。

一次"清干净"要按顺序拔掉它身上的每一根刺，顺序很重要：

  1. 撤掉无障碍        ← 必须先做，否则它还在读屏、还能反咬你
  2. 撤掉设备管理员     ← 不撤掉的话 "pm disable / uninstall" 会被系统拒绝
  3. 撤掉悬浮窗权限     ← 拔掉弹窗的根子
  4. 撤掉通讯录/相册/存储等危险权限
  5. force-stop + am kill
  6. 可选：pm disable-user（等于"冻结"，桌面图标直接变灰/消失）
  7. 可选：pm uninstall --user 0（真删）

所有动作都返回结构化结果，方便打印和上报。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import config


# ----------------------------------------------------------------------

@dataclass
class ActionResult:
    pkg: str
    steps: List[str] = field(default_factory=list)
    ok: bool = False

    def add(self, text: str, success: bool = True) -> None:
        mark = "✓" if success else "✗"
        self.steps.append(f"{mark} {text}")

    def text(self) -> str:
        return f"== 处置 {self.pkg} ==\n" + "\n".join("   " + s for s in self.steps)


# ----------------------------------------------------------------------
# 单项动作
# ----------------------------------------------------------------------

def active_admins(adb) -> Dict[str, str]:
    """返回 {包名: "包名/组件"}，用于 dpm remove-active-admin。"""
    out = adb.sh("dumpsys device_policy")
    result: Dict[str, str] = {}
    for m in re.finditer(r"ComponentInfo\{([\w.]+)/([\w.$]+)\}", out):
        pkg, comp = m.group(1), m.group(2)
        result[pkg] = f"{pkg}/{comp}"
    # 老一些的 ROM 只打印 pkg/recv
    for m in re.finditer(r"\b([a-zA-Z][\w.]*)/([A-Za-z][\w.$]*AdminReceiver)\b", out):
        pkg, comp = m.group(1), m.group(2)
        result.setdefault(pkg, f"{pkg}/{comp}")
    return result


def disable_accessibility(adb, pkg: str) -> bool:
    """把目标包的无障碍服务从系统名单里摘掉。"""
    current = adb.enabled_accessibility()
    keep = [s for s in current if not s.startswith(pkg + "/")]
    if len(keep) == len(current):
        return True  # 本来就没开

    if keep:
        joined = ":".join(keep)
        adb.sh(f'settings put secure enabled_accessibility_services "{joined}"')
    else:
        adb.sh("settings delete secure enabled_accessibility_services")
        adb.sh("settings put secure accessibility_enabled 0")
    return True


def remove_device_admin(adb, pkg: str) -> bool:
    admins = active_admins(adb)
    comp = admins.get(pkg)
    if not comp:
        return True
    adb.sh(f"dpm remove-active-admin {comp}")
    time.sleep(0.4)
    return pkg not in active_admins(adb)


def revoke_overlay(adb, pkg: str) -> bool:
    ok = adb.appop_set(pkg, "SYSTEM_ALERT_WINDOW", "deny")
    # 有些 ROM 还需要单独关掉"显示在其他应用上层"的特殊权限页
    adb.sh(f"appops set {pkg} SYSTEM_ALERT_WINDOW deny")
    return ok


def revoke_permissions(adb, pkg: str) -> int:
    n = 0
    for perm in config.DANGEROUS_PERMS:
        if perm == "android.permission.SYSTEM_ALERT_WINDOW":
            continue  # 走 appops
        if adb.revoke(pkg, perm):
            n += 1
    # 特殊权限走 appops
    for op in ("READ_CONTACTS", "WRITE_CONTACTS", "READ_CALL_LOG", "READ_SMS",
               "READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE",
               "CAMERA", "RECORD_AUDIO", "ACCESS_FINE_LOCATION",
               "ACCESS_COARSE_LOCATION", "READ_PHONE_STATE", "POST_NOTIFICATION"):
        adb.appop_set(pkg, op, "deny")
    return n


def kill(adb, pkg: str) -> bool:
    a = adb.force_stop(pkg)
    adb.kill_bg(pkg)
    return a


def disable_pkg(adb, pkg: str) -> str:
    """pm disable-user --user 0 —— 比卸载温和，但图标会消失、进程起不来。"""
    return adb.disable_user(pkg)


def enable_pkg(adb, pkg: str) -> str:
    return adb.enable_user(pkg)


def uninstall_pkg(adb, pkg: str) -> str:
    return adb.uninstall_user(pkg)


# ----------------------------------------------------------------------
# 组合拳
# ----------------------------------------------------------------------

def quarantine(adb, pkg: str,
               also_disable: bool = None,
               also_uninstall: bool = False,
               verbose: bool = True) -> ActionResult:
    """
    把一根刺一根刺拔掉。默认不 disable / 不 uninstall（那两个动作比较重）。
    """
    if also_disable is None:
        also_disable = config.AUTO_DISABLE

    res = ActionResult(pkg=pkg)

    # 1) 无障碍
    try:
        ok = disable_accessibility(adb, pkg)
        res.add("摘掉无障碍服务", ok)
    except Exception as e:  # noqa: BLE001
        res.add(f"摘掉无障碍服务失败: {e}", False)

    # 2) 设备管理员
    try:
        ok = remove_device_admin(adb, pkg)
        res.add("解除设备管理员", ok)
    except Exception as e:  # noqa: BLE001
        res.add(f"解除设备管理员失败: {e}", False)

    # 3) 悬浮窗
    if config.AUTO_REVOKE_OVERLAY:
        try:
            ok = revoke_overlay(adb, pkg)
            res.add("撤销悬浮窗权限（弹窗的根子）", ok)
        except Exception as e:  # noqa: BLE001
            res.add(f"撤销悬浮窗失败: {e}", False)

    # 4) 危险权限
    if config.AUTO_REVOKE_PERMS:
        try:
            n = revoke_permissions(adb, pkg)
            res.add(f"撤销危险权限 {n} 项（通讯录/相册/存储/定位/相机/麦克风）")
        except Exception as e:  # noqa: BLE001
            res.add(f"撤销权限失败: {e}", False)

    # 5) 杀进程
    try:
        kill(adb, pkg)
        res.add("force-stop + am kill")
    except Exception as e:  # noqa: BLE001
        res.add(f"杀进程失败: {e}", False)

    # 6) 禁用
    if also_disable:
        out = disable_pkg(adb, pkg)
        ok = "new state: disabled" in out or "disabled-user" in out
        res.add(f"pm disable-user → {out.strip() or '(无输出)'}", ok)

    # 7) 卸载
    if also_uninstall:
        out = uninstall_pkg(adb, pkg)
        ok = "Success" in out
        res.add(f"pm uninstall --user 0 → {out.strip() or '(无输出)'}", ok)

    res.ok = all(not s.startswith("✗") for s in res.steps)
    return res


def bulk_quarantine(adb, pkgs: List[str], also_disable: Optional[bool] = None,
                    verbose: bool = True) -> List[ActionResult]:
    out = []
    for p in pkgs:
        if verbose:
            print(f"\n▶ 处置 {p}")
        r = quarantine(adb, p, also_disable=also_disable, verbose=verbose)
        if verbose:
            print(r.text())
        out.append(r)
    return out


# ----------------------------------------------------------------------

def promote_device_owner(adb, component: str = "top.adbguard/.GuardDeviceAdminReceiver") -> str:
    """
    把守护喵提升为 Device Owner —— 之后 APK 自己就能隐藏/挂起流氓应用。
    注意：设备上必须没有其他账号（部分机型要求），否则会失败。
    """
    out = adb.set_device_owner(component)
    if "Success" in out:
        return f"✓ 已提升为 Device Owner：{component}"
    hint = (
        "\n  常见失败原因：\n"
        "   1. 设备上已登录账号（部分 ROM 要求先移除全部账号）\n"
        "   2. 已经存在另一个 Device Owner\n"
        "   3. 已激活过其他设备管理员 → 先 `dpm remove-active-admin`\n"
        "   4. 未安装对应的 APK"
    )
    return f"✗ 提升失败：{out.strip()}{hint}"


def demote_device_owner(adb, component: str = "top.adbguard/.GuardDeviceAdminReceiver") -> str:
    """
    撤销守护喵的 Device Owner 身份。

    前提：本 APK 的 manifest 里已声明 android:testOnly="true"。
    `dpm` 的官方帮助写明 remove-active-admin 只对声明了 testOnly 的 admin 生效；
    没有那一行就只能靠 APK 内的 clearDeviceOwnerApp()，或恢复出厂设置。
    """
    out = adb.remove_active_admin(component)
    if "success" in out.lower():
        return f"✓ 已撤销 Device Owner：{component}\n  现在可以在系统设置里正常卸载守护喵了"

    hint = (
        "\n  若提示 non-test admin / not removable：\n"
        "   1. 确认当前 APK 版本声明了 android:testOnly=\"true\"（v1.0 起已加）\n"
        "   2. 仍失败就走正路 —— 打开守护喵 → 主面板 →\n"
        "      「② 已接入 Device Owner · 点此撤销」，\n"
        "      那里调用 clearDeviceOwnerApp()，是 Android 唯一可靠的降权方式\n"
        "   3. 都无效时只剩「恢复出厂设置」（这是 Android 的防赖着不走设计）"
    )
    return f"✗ 撤销失败：{out.strip()}{hint}"


def make_kiosk_list(adb, pkgs: List[str]) -> str:
    """
    给"只允许这些应用"的场景生成锁定任务白名单（需要 Device Owner）。
    """
    joined = ",".join(pkgs)
    return f"adb shell dpm set-lock-task-packages top.adbguard {joined}"
