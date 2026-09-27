#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.py —— 守护喵 · 可视化控制台

把 adbguard-backend 里那些命令行动作（体检 / 查看 / 处置 / 提权）搬进一个窗口，
鼠标点点就能看和清，不用记命令、不用敲 adb。

依赖：
    pip install customtkinter        # 唯一的第三方依赖，只为这个界面
    其余全部复用同目录的 config / adbutil / remediate / inventory

运行：
    .venv/Scripts/python gui.py
    .venv/Scripts/python gui.py --selftest    # 无头自检（1.5 秒后自动关闭）

设计要点：
  · 所有 ADB 操作都在后台线程跑，UI 永不卡死；结果经 queue 回主线程渲染
  · 复用 remediate.quarantine 的「拔刺顺序」——顺序错了会被系统拒绝，
    所以界面里不重造这套逻辑，只调用现成的
  · 深度体检调 inventory.scan，与命令行 `python main.py scan` 完全同源
"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
from datetime import datetime

# ── pythonw 下 stdout/stderr 为 None，print 会炸 —— 先兜住 ──────────────
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import subprocess  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

# ── 让 adb 子进程不弹黑框（必须在导入 adbutil 之前做）──────────────────
if os.name == "nt":
    _orig_popen = subprocess.Popen

    def _popen_hidden(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000
        return _orig_popen(*args, **kwargs)

    subprocess.Popen = _popen_hidden  # type: ignore[assignment]

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import customtkinter as ctk  # noqa: E402

import adbutil  # noqa: E402
import config  # noqa: E402
import inventory  # noqa: E402
import remediate  # noqa: E402

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

APP_TITLE = "守护喵 · 控制台 — AdbGuard"

# 等级配色。这里不是股票，红=危险 是通用安全语义，与红涨绿跌无关。
LEVEL_COLOR = {
    "高危": "#E24B4A",
    "可疑": "#EF9F27",
    "观察": "#BA7517",
    "正常": "#1D9E75",
    "未体检": "#888780",
}

FILTERS = ["全部", "高危", "可疑及以上", "有悬浮窗", "有设备管理员", "无启动图标", "未体检"]


def _find(pat: str, text: str, default: str = "") -> str:
    m = re.search(pat, text)
    return m.group(1).strip() if m else default


def _raw_text(widget):
    """拿到 CTkTextbox 内部那个真正的 tk.Text。

    customtkinter 各版本给的属性名不一样（5.x 是 _textbox），而给文本上色
    只能在内层 Text 上做 tag_config。逐个试，都不行就退化成控件本身。
    """
    for attr in ("_textbox", "_text_widget", "textbox"):
        inner = getattr(widget, attr, None)
        if inner is not None and hasattr(inner, "tag_config"):
            return inner
    return widget


def flags_of(risk: "inventory.AppRisk | None") -> str:
    """把 AppRisk 的几个布尔特征压成一行短标签。"""
    if risk is None:
        return ""
    f = []
    if not risk.has_launcher:
        f.append("无图标")
    if risk.overlay:
        f.append("悬浮窗")
    if risk.accessibility:
        f.append("无障碍")
    if risk.device_admin:
        f.append("管理员")
    if risk.label_missing:
        f.append("无应用名")
    return " · ".join(f)


def format_detail(pkg: str, d: dict, risk: "inventory.AppRisk | None" = None) -> str:
    """把 dumpsys 解析结果 + 体检结论拼成详情面板的文本。

    抽成纯函数是为了能脱离 GUI 单测 —— 这里的解析规则（哪些算系统分区、
    哪些权限算危险）比界面本身更容易出错。
    """
    path = d.get("code_path") or ""
    is_sys = path.startswith(("/system", "/product", "/vendor", "/apex"))

    lines = [
        f"包名      {pkg}",
        f"版本      {d.get('version') or '?'}",
        f"安装来源  {d.get('installer') or '—'}",
        f"首次安装  {d.get('first') or '—'}",
        f"最后更新  {d.get('update') or '—'}",
        f"安装位置  {'系统分区' if is_sys else '用户空间'}",
        f"运行中    {'是（pid ' + d['pid'] + '）' if d.get('pid') else '否'}",
        "",
        "── 敏感能力 ──",
        f"  悬浮窗        {'是' if d.get('overlay') else '否'}",
        f"  无障碍服务    {'是' if d.get('acc') else '否'}",
        f"  设备管理员    {'是' if d.get('admin') else '否'}",
        f"  Device Owner  {'是' if d.get('owner') else '否'}",
    ]

    if risk is not None:
        lines += ["", f"── 体检结论 ──  {risk.score} 分 · {risk.level}"]
        for reason in risk.reasons:
            lines.append(f"  · {reason}")
        lines.append(f"  特征：{flags_of(risk) or '无明显异常'}")

    granted = d.get("granted") or []
    yes = [p for p, g in granted if g == "true"]
    no = [p for p, g in granted if g != "true"]

    lines += ["", "── 已授予权限 ──"]
    if yes:
        lines.append(f"  已授予 {len(yes)} 项：")
        for full in sorted(yes):
            name = full.rsplit(".", 1)[-1]
            danger = full in config.DANGEROUS_PERMS
            lines.append(f"    {'! ' if danger else '  '}{name}{'   ← 危险' if danger else ''}")
    else:
        lines.append("  已授予 0 项")
    lines.append(f"  未授予 {len(no)} 项")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------

class GuardConsole(ctk.CTk):

    def __init__(self, autostart: bool = True) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(APP_TITLE)
        self.geometry("1220x800")
        self.minsize(1040, 660)

        fam = "Microsoft YaHei UI" if os.name == "nt" else "sans-serif"
        self.fam = fam
        self.f_bold = ctk.CTkFont(family=fam, size=13, weight="bold")
        self.f_norm = ctk.CTkFont(family=fam, size=12)
        self.f_mono = ctk.CTkFont(family="Consolas", size=11)

        # ── 状态 ──
        self.adb: "adbutil.Adb | None" = None
        self.devices: list[tuple[str, str]] = []
        self.pkgs: list[str] = []
        self.risks: dict[str, "inventory.AppRisk"] = {}
        self.fg_pkg = ""
        self.photo = None
        self._q: queue.Queue = queue.Queue()
        self._auto_shot = False
        self._busy = 0

        self._build()
        self.after(80, self._pump)
        if autostart:
            self.after(250, self.refresh_devices)

    # ------------------------------------------------------------------
    # 骨架
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_topbar()

        self.tabs = ctk.CTkTabview(self, corner_radius=8)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(6, 0))
        t1 = self.tabs.add("应用管理")
        t2 = self.tabs.add("屏幕 / 设备")
        self._build_apps_tab(t1)
        self._build_screen_tab(t2)

        self._build_log()
        self._apply_tree_style()

    def _build_topbar(self) -> None:
        bar = ctk.CTkFrame(self, corner_radius=0, height=58)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)

        ctk.CTkLabel(bar, text="设备", font=self.f_bold).pack(side="left", padx=(14, 6), pady=14)
        self.dev_menu = ctk.CTkOptionMenu(bar, values=["（无设备）"], width=280,
                                          command=self._on_dev_pick, font=self.f_norm)
        self.dev_menu.pack(side="left", pady=14)
        ctk.CTkButton(bar, text="刷新", width=62, command=self.refresh_devices,
                      font=self.f_norm).pack(side="left", padx=6)

        ctk.CTkLabel(bar, text="无线调试", font=self.f_bold).pack(side="left", padx=(20, 6))
        self.conn_entry = ctk.CTkEntry(bar, width=180, font=self.f_norm,
                                       placeholder_text="192.168.1.5:39161")
        self.conn_entry.pack(side="left")
        ctk.CTkButton(bar, text="连接", width=58, command=self.connect_wifi,
                      font=self.f_norm).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="配对", width=58, command=self.pair_wifi, font=self.f_norm,
                      fg_color="transparent", border_width=1).pack(side="left")

        self.status_lbl = ctk.CTkLabel(bar, text="未连接", text_color="#888780", font=self.f_norm)
        self.status_lbl.pack(side="right", padx=14)

        ctk.CTkButton(bar, text="外观", width=58, command=self._toggle_appearance,
                      font=self.f_norm, fg_color="transparent",
                      border_width=1).pack(side="right", padx=6)

    # ------------------------------------------------------------------
    # Tab 1 · 应用管理
    # ------------------------------------------------------------------

    def _build_apps_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(0, weight=1)

        # ── 左：列表 ──
        left = ctk.CTkFrame(tab, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=8)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(left, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew")

        self.search_entry = ctk.CTkEntry(bar, width=180, font=self.f_norm,
                                         placeholder_text="搜索包名…")
        self.search_entry.pack(side="left")
        self.search_entry.bind("<KeyRelease>", lambda _e: self.fill_tree())

        self.filter_var = ctk.StringVar(value="全部")
        ctk.CTkOptionMenu(bar, values=FILTERS, variable=self.filter_var, width=124,
                          font=self.f_norm, command=lambda _v: self.fill_tree()).pack(side="left", padx=6)

        ctk.CTkButton(bar, text="重载", width=60, command=self.load_packages,
                      font=self.f_norm).pack(side="left")
        ctk.CTkButton(bar, text="深度体检", width=82, command=self.deep_scan,
                      font=self.f_norm, fg_color="#0F6E56").pack(side="left", padx=6)
        ctk.CTkButton(bar, text="选高危", width=62, command=self.select_high_risk,
                      font=self.f_norm, fg_color="transparent", border_width=1).pack(side="left")

        self.count_lbl = ctk.CTkLabel(bar, text="", font=self.f_norm, text_color="#888780")
        self.count_lbl.pack(side="right")

        wrap = ctk.CTkFrame(left, corner_radius=8)
        wrap.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        cols = ("pkg", "score", "level", "flags")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 style="Guard.Treeview", selectmode="extended")
        for c, title, w, anchor in (
            ("pkg", "包名", 250, "w"),
            ("score", "分", 46, "center"),
            ("level", "等级", 62, "center"),
            ("flags", "特征", 168, "w"),
        ):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c in ("pkg", "flags")))
        for lv, col in LEVEL_COLOR.items():
            self.tree.tag_configure(lv, foreground=col)

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        vsb.grid(row=0, column=1, sticky="ns", pady=4, padx=(0, 4))
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # ── 右：详情 + 操作 ──
        right = ctk.CTkFrame(tab, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", pady=8)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(right, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(head, text="应用详情", font=self.f_bold).pack(side="left")
        ctk.CTkButton(head, text="复制包名", width=76, command=self.copy_pkg, font=self.f_norm,
                      fg_color="transparent", border_width=1).pack(side="right")
        ctk.CTkButton(head, text="系统详情页", width=90, command=self.open_sys_detail, font=self.f_norm,
                      fg_color="transparent", border_width=1).pack(side="right", padx=6)

        self.detail_box = ctk.CTkTextbox(right, wrap="word", font=self.f_mono)
        self.detail_box.grid(row=1, column=0, sticky="nsew", pady=(6, 8))
        self.detail_box.configure(state="disabled")

        ops = ctk.CTkFrame(right, corner_radius=8)
        ops.grid(row=2, column=0, sticky="ew")
        for i in range(3):
            ops.grid_columnconfigure(i, weight=1)

        grid_btns = [
            ("强杀进程", self.do_kill, None),
            ("撤权限", self.do_revoke_perms, None),
            ("撤悬浮窗", self.do_revoke_overlay, None),
            ("解管理员", self.do_remove_admin, None),
            ("冻结", self.do_disable, None),
            ("解冻", self.do_enable, None),
        ]
        for i, (text, cmd, color) in enumerate(grid_btns):
            ctk.CTkButton(ops, text=text, command=cmd, font=self.f_norm, height=30,
                          fg_color=color or "transparent",
                          border_width=0 if color else 1).grid(
                row=i // 3, column=i % 3, padx=5, pady=5, sticky="ew")

        opt = ctk.CTkFrame(right, fg_color="transparent")
        opt.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.var_disable = ctk.BooleanVar(value=True)
        self.var_uninstall = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opt, text="拔刺时一并冻结", variable=self.var_disable,
                        font=self.f_norm).pack(side="left")
        ctk.CTkCheckBox(opt, text="一并卸载", variable=self.var_uninstall,
                        font=self.f_norm).pack(side="left", padx=14)

        big = ctk.CTkFrame(right, fg_color="transparent")
        big.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        big.grid_columnconfigure(0, weight=1)
        big.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(big, text="拔刺组合拳", command=self.do_quarantine, font=self.f_bold,
                      height=36, fg_color="#BA7517").grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkButton(big, text="卸载（用户级）", command=self.do_uninstall, font=self.f_bold,
                      height=36, fg_color="#A32D2D").grid(row=0, column=1, sticky="ew", padx=(5, 0))

    # ------------------------------------------------------------------
    # Tab 2 · 屏幕 / 设备
    # ------------------------------------------------------------------

    def _build_screen_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(tab, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ns", padx=(0, 12), pady=8)

        self.screen_lbl = ctk.CTkLabel(left, text="点「抓屏」看手机当前画面",
                                       width=300, height=545, font=self.f_norm,
                                       fg_color=("gray88", "gray17"), corner_radius=8)
        self.screen_lbl.pack()

        row = ctk.CTkFrame(left, fg_color="transparent")
        row.pack(pady=8)
        ctk.CTkButton(row, text="抓屏", width=76, command=self.grab_screen,
                      font=self.f_norm).pack(side="left")
        self.var_auto = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row, text="每 2 秒自动", variable=self.var_auto,
                        command=self._toggle_auto, font=self.f_norm).pack(side="left", padx=10)

        right = ctk.CTkFrame(tab, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", pady=8)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="设备信息", font=self.f_bold, anchor="w").grid(row=0, column=0, sticky="w")
        self.info_box = ctk.CTkTextbox(right, font=self.f_mono, height=220)
        self.info_box.grid(row=1, column=0, sticky="nsew", pady=(6, 8))
        self.info_box.configure(state="disabled")

        fg = ctk.CTkFrame(right, corner_radius=8)
        fg.grid(row=2, column=0, sticky="ew")
        self.fg_lbl = ctk.CTkLabel(fg, text="前台应用：—", font=self.f_norm, anchor="w")
        self.fg_lbl.pack(side="left", padx=10, pady=10)
        ctk.CTkButton(fg, text="关掉它", width=78, command=self.kill_foreground,
                      font=self.f_norm).pack(side="right", padx=10)

        ops = ctk.CTkFrame(right, fg_color="transparent")
        ops.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ctk.CTkButton(ops, text="刷新信息", width=88, command=self.load_device_info,
                      font=self.f_norm).pack(side="left")
        ctk.CTkButton(ops, text="提权为 Device Owner", command=self.do_promote,
                      font=self.f_norm, fg_color="#534AB7").pack(side="left", padx=8)
        ctk.CTkButton(ops, text="撤销 Device Owner", command=self.do_demote,
                      font=self.f_norm, fg_color="transparent",
                      border_width=1).pack(side="left")

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------

    def _build_log(self) -> None:
        box = ctk.CTkFrame(self, corner_radius=0)
        box.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 10))
        box.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(box, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 0))
        ctk.CTkLabel(head, text="操作日志", font=self.f_bold).pack(side="left")
        self.busy_lbl = ctk.CTkLabel(head, text="", font=self.f_norm, text_color="#EF9F27")
        self.busy_lbl.pack(side="left", padx=12)
        ctk.CTkButton(head, text="清空", width=54, command=self.clear_log, font=self.f_norm,
                      fg_color="transparent", border_width=1).pack(side="right")

        self.logbox = ctk.CTkTextbox(box, height=152, font=self.f_mono, wrap="word")
        self.logbox.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 8))
        self.logbox.configure(state="disabled")

        tb = _raw_text(self.logbox)
        tb.tag_config("info", foreground="#8A8A8E")
        tb.tag_config("ok", foreground="#1D9E75")
        tb.tag_config("err", foreground="#E24B4A")
        tb.tag_config("warn", foreground="#EF9F27")

    def log(self, msg: str, tag: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        mark = {"info": "·", "ok": "✓", "err": "✗", "warn": "!"}.get(tag, "·")
        tb = _raw_text(self.logbox)
        self.logbox.configure(state="normal")
        tb.insert("end", f"{ts}  {mark}  {msg}\n", tag)
        tb.see("end")
        self.logbox.configure(state="disabled")

    def clear_log(self) -> None:
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")

    def set_status(self, text: str, color: str | None = None) -> None:
        self.status_lbl.configure(text=text, text_color=color or "#888780")

    # ------------------------------------------------------------------
    # 异步执行
    # ------------------------------------------------------------------

    def run(self, fn, on_done=None, tip: str | None = None) -> None:
        """把阻塞的 ADB 调用扔到后台线程，结果回到主线程处理。"""
        self._busy += 1
        self.busy_lbl.configure(text=f"⟳ {tip or '处理中'}" if tip else "⟳ 处理中")

        def worker():
            try:
                r = fn()
                self._q.put((on_done, r, None))
            except Exception as e:  # noqa: BLE001
                self._q.put((on_done, None, e))

        threading.Thread(target=worker, daemon=True).start()

    def _pump(self) -> None:
        """主线程轮询结果队列。每个任务完成时递减计数，归零才清空忙碌提示。"""
        while True:
            try:
                cb, result, err = self._q.get_nowait()
            except queue.Empty:
                break
            self._busy = max(0, self._busy - 1)
            if err is not None:
                self.log(f"{type(err).__name__}: {err}", "err")
            elif cb is not None:
                try:
                    cb(result)
                except Exception as e:  # noqa: BLE001
                    self.log(f"回调出错：{e}", "err")
        if self._busy == 0:
            self.busy_lbl.configure(text="")
        self.after(80, self._pump)

    # ------------------------------------------------------------------
    # 设备
    # ------------------------------------------------------------------

    def refresh_devices(self) -> None:
        def work():
            return adbutil.Adb.list_devices()

        def done(lst):
            self.devices = lst
            labels = [f"{s}  [{st}]" for s, st in lst] or ["（无设备）"]
            self.dev_menu.configure(values=labels)
            self.dev_menu.set(labels[0])
            online = [s for s, st in lst if st == "device"]
            if online:
                self.adb = adbutil.Adb(online[0])
                self.set_status(f"已连接 {online[0]}", "#1D9E75")
                self.log(f"设备就绪：{online[0]}", "ok")
                self.load_packages()
                self.load_device_info()
            else:
                self.adb = None
                self.set_status("无可用设备")
                self.log("没有已授权设备。手机开「无线调试」后，把 IP:端口 填进上面的框点连接。", "warn")

        self.run(work, done, "枚举设备")

    def _on_dev_pick(self, label: str) -> None:
        serial = label.split("  [")[0]
        for s, st in self.devices:
            if s == serial and st == "device":
                self.adb = adbutil.Adb(serial)
                self.set_status(f"已切换 {serial}", "#1D9E75")
                self.log(f"切换到设备 {serial}", "ok")
                self.load_packages()
                self.load_device_info()
                return
        self.log(f"{serial} 当前不可用", "warn")

    def connect_wifi(self) -> None:
        hp = self.conn_entry.get().strip()
        if not hp:
            self.log("先在输入框填 ip:port（无线调试主页面上那一个）", "warn")
            return

        def work():
            return adbutil.Adb().connect(hp) or "(无输出)"

        def done(out):
            ok = "connected" in out.lower()
            self.log(f"connect {hp} → {out}", "ok" if ok else "err")
            if not ok:
                self.log("配对端口 ≠ 连接端口；配对成功后要连主页面显示的那个端口。", "warn")
            self.refresh_devices()

        self.run(work, done, f"连接 {hp}")

    def pair_wifi(self) -> None:
        hp = self.conn_entry.get().strip()
        if not hp:
            self.log("先在输入框填配对弹窗里的 ip:端口", "warn")
            return
        code = ctk.CTkInputDialog(text="输入手机上显示的 6 位配对码：", title="无线调试配对").get_input()
        if not code:
            return

        def work():
            return adbutil.Adb().pair(hp, code.strip()) or "(无输出)"

        self.run(work, lambda out: self.log(f"pair {hp} → {out}",
                                            "ok" if "Successfully paired" in out else "err"),
                 "配对中")

    # ------------------------------------------------------------------
    # 应用列表
    # ------------------------------------------------------------------

    def load_packages(self) -> None:
        adb = self.adb
        if not adb:
            return

        def work():
            return adb.third_party_packages(include_disabled=True)

        def done(pkgs):
            self.pkgs = pkgs
            self.risks.clear()
            self.fill_tree()
            self.log(f"载入 {len(pkgs)} 个第三方应用（含已冻结）", "ok")

        self.run(work, done, "读取应用列表")

    def deep_scan(self) -> None:
        adb = self.adb
        if not adb:
            self.log("先连接设备", "warn")
            return

        def work():
            return inventory.scan(adb, recent_days=7, verbose=False)

        def done(results):
            self.risks = {r.pkg: r for r in results}
            if not self.pkgs:
                self.pkgs = sorted(self.risks)
            self.fill_tree()
            hi = sum(1 for r in results if r.score >= config.SCORE_ACT)
            su = sum(1 for r in results if r.score >= 60)
            self.log(f"体检完成：{len(results)} 个包 / 高危 {hi} / 可疑及以上 {su}",
                     "warn" if hi else "ok")

        self.run(work, done, "深度体检（跑 dumpsys package，稍慢）")

    def fill_tree(self) -> None:
        kw = self.search_entry.get().strip().lower()
        flt = self.filter_var.get()

        self.tree.delete(*self.tree.get_children())
        shown = 0
        for pkg in self.pkgs:
            r = self.risks.get(pkg)
            score = r.score if r else 0
            level = r.level if r else "未体检"

            if kw and kw not in pkg.lower():
                continue
            if flt == "高危" and not (r and r.score >= config.SCORE_ACT):
                continue
            if flt == "可疑及以上" and not (r and r.score >= 60):
                continue
            if flt == "有悬浮窗" and not (r and r.overlay):
                continue
            if flt == "有设备管理员" and not (r and r.device_admin):
                continue
            if flt == "无启动图标" and not (r and not r.has_launcher):
                continue
            if flt == "未体检" and r is not None:
                continue

            self.tree.insert("", "end", iid=pkg,
                             values=(pkg, score if r else "—", level, flags_of(r)),
                             tags=(level,))
            shown += 1

        self.count_lbl.configure(text=f"显示 {shown} / 共 {len(self.pkgs)}")

    def select_high_risk(self) -> None:
        hi = [p for p in self.tree.get_children()
              if (self.risks.get(p) and self.risks[p].score >= config.SCORE_ACT)]
        if not hi:
            self.log("当前列表里没有高危项（先跑一次「深度体检」）", "warn")
            return
        self.tree.selection_set(hi)
        self.log(f"已选中 {len(hi)} 个高危应用", "ok")

    # ------------------------------------------------------------------
    # 详情
    # ------------------------------------------------------------------

    def _selected(self) -> list[str]:
        sel = list(self.tree.selection())
        if not sel:
            self.log("先在左侧选中至少一个应用", "warn")
        return sel

    def on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        if len(sel) > 1:
            self._set_detail(f"已选中 {len(sel)} 个应用，可直接对它们批量操作：\n\n"
                             + "\n".join("  " + p for p in sel))
            return
        self.show_detail(sel[0])

    def _set_detail(self, text: str) -> None:
        self.detail_box.configure(state="normal")
        self.detail_box.delete("1.0", "end")
        self.detail_box.insert("end", text)
        self.detail_box.configure(state="disabled")

    def show_detail(self, pkg: str) -> None:
        adb = self.adb
        if not adb:
            return
        risk = self.risks.get(pkg)
        self._set_detail(f"正在读取 {pkg} …")

        def work():
            dump = adb.sh(f"dumpsys package {pkg}", timeout=30)
            granted = re.findall(r"^\s+(android\.permission\.[\w.]+): granted=(true|false)",
                                 dump, re.M)
            d = {
                "dump": dump,
                "version": _find(r"versionName=(\S+)", dump, "?"),
                "installer": _find(r"installerPackageName=(\S+)", dump, "—"),
                "first": _find(r"firstInstallTime=([\d\- :]+)", dump, "—"),
                "update": _find(r"lastUpdateTime=([\d\- :]+)", dump, "—"),
                "code_path": adb.path_of(pkg) or "—",
                "pid": adb.sh(f"pidof {pkg}").strip(),
                "overlay": adb.overlay_allowed(pkg),
                "acc": adb.has_accessibility(pkg),
                "admin": pkg in adb.device_admins(),
                "owner": adb.is_device_owner(pkg),
                "granted": granted,
            }
            return d

        def done(d: dict) -> None:
            self._set_detail(format_detail(pkg, d, risk))

        self.run(work, done, f"读取 {pkg}")

    def copy_pkg(self) -> None:
        sel = self.tree.selection()
        if not sel:
            self.log("先选中一个应用", "warn")
            return
        text = "\n".join(sel)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.log(f"已复制 {len(sel)} 个包名到剪贴板", "ok")

    def open_sys_detail(self) -> None:
        sel = self.tree.selection()
        if not sel or not self.adb:
            self.log("先选中一个应用", "warn")
            return
        pkg = sel[0]
        adb = self.adb
        self.run(lambda: adb.sh(f"am start -a android.settings.APPLICATION_DETAILS_SETTINGS "
                                f"-d package:{pkg}"),
                 lambda _r: self.log(f"已在手机上打开 {pkg} 的详情页", "ok"),
                 "打开详情页")

    # ------------------------------------------------------------------
    # 处置动作
    # ------------------------------------------------------------------

    def _each(self, label: str, fn, tip: str) -> None:
        pkgs = self._selected()
        if not pkgs or not self.adb:
            return
        adb = self.adb

        def work():
            out = []
            for p in pkgs:
                try:
                    out.append((p, fn(adb, p)))
                except Exception as e:  # noqa: BLE001
                    out.append((p, f"异常 {e}"))
            return out

        def done(res):
            for p, r in res:
                s = str(r)
                bad = s.startswith("异常") or s.strip() in ("False", "") or "✗" in s
                self.log(f"{label} {p} → {s}", "err" if bad else "ok")

        self.run(work, done, tip)

    def do_kill(self) -> None:
        self._each("强杀", lambda a, p: remediate.kill(a, p), "强杀进程")

    def do_revoke_overlay(self) -> None:
        self._each("撤悬浮窗", lambda a, p: remediate.revoke_overlay(a, p), "撤销悬浮窗")

    def do_remove_admin(self) -> None:
        self._each("解管理员", lambda a, p: remediate.remove_device_admin(a, p), "解除设备管理员")

    def do_disable(self) -> None:
        self._each("冻结", lambda a, p: remediate.disable_pkg(a, p), "冻结应用")

    def do_enable(self) -> None:
        self._each("解冻", lambda a, p: remediate.enable_pkg(a, p), "恢复应用")

    def do_revoke_perms(self) -> None:
        self._each("撤权限", lambda a, p: f"撤销 {remediate.revoke_permissions(a, p)} 项",
                   "撤销危险权限")

    def do_quarantine(self) -> None:
        pkgs = self._selected()
        if not pkgs or not self.adb:
            return
        disable = self.var_disable.get()
        uninstall = self.var_uninstall.get()
        if uninstall and not messagebox.askyesno(
            "确认", f"将对 {len(pkgs)} 个应用执行【拔刺 + 卸载】，确定继续？\n\n"
                    + "\n".join(pkgs[:12])):
            return
        adb = self.adb

        def work():
            return [(p, remediate.quarantine(adb, p, also_disable=disable,
                                             also_uninstall=uninstall, verbose=False))
                    for p in pkgs]

        def done(res):
            for p, r in res:
                self.log(f"== 处置 {p} ==", "ok" if r.ok else "err")
                for step in r.steps:
                    self.log("     " + step, "err" if step.startswith("✗") else "ok")
            self.log(f"批量处置完成，共 {len(res)} 个", "ok")

        self.run(work, done, "拔刺组合拳")

    def do_uninstall(self) -> None:
        pkgs = self._selected()
        if not pkgs or not self.adb:
            return
        if not messagebox.askyesno(
                "确认卸载",
                f"真的卸载这 {len(pkgs)} 个应用？\n\n" + "\n".join(pkgs[:12])
                + "\n\n（用 pm uninstall --user 0，只动当前用户，不碰系统分区）"):
            return
        self._each("卸载", lambda a, p: remediate.uninstall_pkg(a, p), "卸载中")

    def kill_foreground(self) -> None:
        if not self.adb or not self.fg_pkg:
            self.log("先点「刷新信息」拿到当前前台包名", "warn")
            return
        adb, pkg = self.adb, self.fg_pkg
        self.run(lambda: remediate.kill(adb, pkg),
                 lambda ok: self.log(f"关掉前台 {pkg} → {'成功' if ok else '失败'}",
                                     "ok" if ok else "err"),
                 "关闭前台应用")

    # ------------------------------------------------------------------
    # 提权 / 降权
    # ------------------------------------------------------------------

    def do_promote(self) -> None:
        if not self.adb:
            self.log("先连接设备", "warn")
            return
        if not messagebox.askyesno(
                "提权为 Device Owner",
                "把守护喵提升为 Device Owner？\n\n"
                "提升后 APK 才能隐藏 / 挂起别的应用、撤销对方权限。\n\n"
                "前提：手机上没有任何已登录账号，且没有别的 Device Owner。\n"
                "随时可以用旁边的「撤销」退回，撤销后即可正常卸载。"):
            return
        adb = self.adb

        def done(s: str):
            ok = s.startswith("✓")
            for line in s.splitlines():
                self.log(line, "ok" if ok else "err")

        self.run(lambda: remediate.promote_device_owner(adb), done, "提权中")

    def do_demote(self) -> None:
        if not self.adb:
            self.log("先连接设备", "warn")
            return
        adb = self.adb

        def done(s: str):
            ok = s.startswith("✓")
            for line in s.splitlines():
                self.log(line, "ok" if ok else "err")

        self.run(lambda: remediate.demote_device_owner(adb), done, "撤销提权")

    # ------------------------------------------------------------------
    # 屏幕 / 设备信息
    # ------------------------------------------------------------------

    def grab_screen(self, quiet: bool = False) -> None:
        adb = self.adb
        if not adb:
            if not quiet:
                self.log("先连接设备", "warn")
            return
        tmp = os.path.join(_HERE, "_screen.png")

        def work():
            return adb.screenshot(tmp)

        def done(ok):
            if not ok:
                if not quiet:
                    self.log("抓屏失败（手机可能锁屏了）", "err")
                return
            try:
                from PIL import Image
                img = Image.open(tmp)
                w, h = img.size
                scale = min(300 / w, 545 / h)
                self.photo = ctk.CTkImage(light_image=img, dark_image=img,
                                          size=(int(w * scale), int(h * scale)))
                self.screen_lbl.configure(image=self.photo, text="")
                if not quiet:
                    self.log(f"抓屏 {w}×{h}", "ok")
            except Exception as e:  # noqa: BLE001
                self.log(f"图像处理失败：{e}", "err")

        self.run(work, done)

    def _toggle_auto(self) -> None:
        if self.var_auto.get():
            self.log("已开启自动抓屏（每 2 秒）", "info")
            self._auto_loop()
        else:
            self.log("已关闭自动抓屏", "info")

    def _auto_loop(self) -> None:
        if not self.var_auto.get():
            return
        self.grab_screen(quiet=True)
        self.after(2000, self._auto_loop)

    def load_device_info(self) -> None:
        adb = self.adb
        if not adb:
            return

        def work():
            return {
                "型号": adb.model(),
                "Android": adb.android_version(),
                "SDK": adb.sdk(),
                "root": "是" if adb.is_root() else "否",
                "屏幕": "%d × %d" % adb.screen_size(),
                "前台应用": adb.foreground_package() or "(未知)",
                "Device Owner": "是" if adb.is_device_owner("top.adbguard") else "否",
                "守护喵": "已安装" if adb.path_of("top.adbguard") else "未安装",
            }

        def done(d: dict) -> None:
            self.info_box.configure(state="normal")
            self.info_box.delete("1.0", "end")
            for k, v in d.items():
                self.info_box.insert("end", f"{k}\n    {v}\n")
            self.info_box.configure(state="disabled")
            self.fg_pkg = d["前台应用"] if d["前台应用"] != "(未知)" else ""
            self.fg_lbl.configure(text=f"前台应用：{self.fg_pkg or '—'}")

        self.run(work, done)

    # ------------------------------------------------------------------
    # 外观
    # ------------------------------------------------------------------

    def _toggle_appearance(self) -> None:
        cur = ctk.get_appearance_mode()
        ctk.set_appearance_mode("Light" if cur == "Dark" else "Dark")
        self._apply_tree_style()

    def _apply_tree_style(self) -> None:
        dark = ctk.get_appearance_mode() == "Dark"
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass

        bg = "#232329" if dark else "#FFFFFF"
        fg = "#E6E6E6" if dark else "#1A1A1A"
        head_bg = "#2E2E36" if dark else "#E9E9EC"
        sel_bg = "#185FA5" if dark else "#B5D4F4"
        sel_fg = "#FFFFFF" if dark else "#042C53"

        style.configure("Guard.Treeview", background=bg, foreground=fg,
                        fieldbackground=bg, rowheight=27, borderwidth=0,
                        font=(self.fam, 10))
        style.configure("Guard.Treeview.Heading", background=head_bg, foreground=fg,
                        borderwidth=0, relief="flat", font=(self.fam, 10, "bold"))
        style.map("Guard.Treeview",
                  background=[("selected", sel_bg)],
                  foreground=[("selected", sel_fg)])
        style.map("Guard.Treeview.Heading", background=[("active", head_bg)])


# ----------------------------------------------------------------------

FAKE_DUMP = """\
Package [com.fake.cleanmaster]:
  versionName=2.3.1
  firstInstallTime=2026-09-26 23:11:05
  lastUpdateTime=2026-09-27 09:02:44
  installerPackageName=com.hihonor.quickengine
grantedPermissions:
  android.permission.READ_CONTACTS: granted=true
  android.permission.CAMERA: granted=true
  android.permission.READ_SMS: granted=false
"""


def _selftest() -> None:
    """无头自检：用假数据跑通「列表渲染 + 筛选 + 详情解析」三条路径。

    不连手机也能验证 —— 这里最容易错的从来不是界面，而是解析规则
    （哪些路径算系统分区、哪些权限算危险、正则能不能抠出字段）。
    """
    app = GuardConsole(autostart=False)
    results: list[str] = []

    def check(name, fn):
        try:
            fn()
            results.append(f"  OK    {name}")
        except Exception as e:  # noqa: BLE001
            results.append(f"  FAIL  {name} → {type(e).__name__}: {e}")

    def seed():
        risks = [
            inventory.AppRisk(pkg="com.fake.cleanmaster", score=115,
                              reasons=["无启动图标 +35", "有悬浮窗 +30", "高危包名 clean +40"],
                              has_launcher=False, overlay=True, label_missing=True),
            inventory.AppRisk(pkg="com.tencent.mm", score=0,
                              reasons=["无可疑特征"], has_launcher=True),
            inventory.AppRisk(pkg="com.fake.hidden", score=65,
                              reasons=["无启动图标 +35", "开了无障碍 +30"],
                              has_launcher=False, accessibility=True),
        ]
        app.pkgs = [r.pkg for r in risks]
        app.risks = {r.pkg: r for r in risks}
        app.fill_tree()
        assert len(app.tree.get_children()) == 3, "列表应渲染 3 行"

    check("fill_tree 渲染列表", seed)
    check("筛选 = 高危", lambda: (app.filter_var.set("高危"), app.fill_tree(),
                                _assert_rows(app, 1)))
    check("筛选 = 无启动图标", lambda: (app.filter_var.set("无启动图标"), app.fill_tree(),
                                   _assert_rows(app, 2)))
    def search_case():
        app.filter_var.set("全部")
        app.fill_tree()
        app.search_entry.delete(0, "end")
        app.search_entry.insert(0, "fake")
        app.fill_tree()
        _assert_rows(app, 2, "搜索 fake 应命中 2 行")
        app.search_entry.delete(0, "end")
        app.fill_tree()
        _assert_rows(app, 3, "清空搜索应恢复 3 行")

    check("搜索 + 复位", search_case)
    check("select_high_risk 选中", lambda: (
        app.select_high_risk(),
        _assert_sel(app, ["com.fake.cleanmaster"])))

    def do_detail():
        parsed = {
            "version": _find(r"versionName=(\S+)", FAKE_DUMP, "?"),
            "installer": _find(r"installerPackageName=(\S+)", FAKE_DUMP, "—"),
            "first": _find(r"firstInstallTime=([\d\- :]+)", FAKE_DUMP, "—"),
            "update": _find(r"lastUpdateTime=([\d\- :]+)", FAKE_DUMP, "—"),
            "code_path": "/data/app/~~abc==/com.fake.cleanmaster-1/base.apk",
            "pid": "",
            "overlay": True, "acc": False, "admin": False, "owner": False,
            "granted": re.findall(r"^\s+(android\.permission\.[\w.]+): granted=(true|false)",
                                  FAKE_DUMP, re.M),
        }
        assert parsed["version"] == "2.3.1", f"版本号解析错：{parsed['version']}"
        assert parsed["installer"] == "com.hihonor.quickengine", "安装来源解析错"
        assert parsed["first"] == "2026-09-26 23:11:05", f"首装时间解析错：{parsed['first']}"
        assert len(parsed["granted"]) == 3, f"权限条数错：{len(parsed['granted'])}"

        text = format_detail("com.fake.cleanmaster", parsed, app.risks["com.fake.cleanmaster"])
        assert "READ_CONTACTS" in text and "危险" in text, "危险权限没被标记"
        assert "用户空间" in text, "安装位置判据错（应是用户空间）"
        assert "115" in text, "体检分数没带出来"
        assert text.count("\n") > 10, "详情太短，八成拼装漏了"
        app._set_detail(text)

    check("format_detail 详情拼装", do_detail)

    def do_sys_path():
        text = format_detail("x", {"code_path": "/system/priv-app/x/base.apk"}, None)
        assert "系统分区" in text, "/system 没判成系统分区"
        text = format_detail("x", {"code_path": "/apex/com.android.x/base.apk"}, None)
        assert "系统分区" in text, "/apex 没判成系统分区"

    check("系统分区判据", do_sys_path)

    def finish():
        print("\n".join(results))
        bad = [r for r in results if "FAIL" in r]
        print(f"\n{'✓ 全部通过' if not bad else '✗ 有失败项'}  "
              f"{len(results) - len(bad)}/{len(results)}")
        app.destroy()

    app.after(900, finish)
    app.mainloop()


def _assert_rows(app, expect: int, msg: str = "") -> None:
    got = len(app.tree.get_children())
    assert got == expect, f"{msg or '行数不符'}（应有 {expect}，实际 {got}）"


def _assert_sel(app, expect: list[str]) -> None:
    got = list(app.tree.selection())
    assert got == expect, f"应选中 {expect}，实际 {got}"


def main() -> None:
    if "--selftest" in sys.argv:
        _selftest()
        return
    GuardConsole().mainloop()


if __name__ == "__main__":
    main()
