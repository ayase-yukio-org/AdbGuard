# 守护喵 · AdbGuard

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Android%207.0%2B-3ddc84.svg)]()
[![minSdk](https://img.shields.io/badge/minSdk-24-orange.svg)]()

> 专治伪装成「手机清理 / 优化大师」的流氓应用 —— 给爸妈手机用的一套防线。
> 由一个 Android APK（前端守护）+ 一个 Python/ADB 后端（清剿工具）组成。

---

## 一、它到底在对付什么

你描述的现象，逐条对上：

| 现象 | 背后的技术手段 | 本项目的对策 |
|---|---|---|
| 伪装成"xx清理""xx优化"，点进去是抖音/购物界面 | 一个全屏 `ImageView` 贴张假截图 | 打分器识别「全屏图 + 文字节点极少」(**+40**) |
| 15 个假关闭按钮，怎么点都退不出 | 一堆无文字的小 `ImageView` 挂在 `SYSTEM_ALERT_WINDOW` 上 | 识别「假关闭按钮聚簇」(**+30**) + 「无文字可点节点 ≥8」(**+25**) |
| 弹窗问"是否退出"，退出没奖励 | 退出陷阱文案 | 关键词库「退出陷阱」类 (**+30/类**) |
| 桌面突然多出 5~15 个 App | 静默安装 / 广告 SDK 拉活 | `inventory.py` 用 7 个信号综合打分 |
| 3 个没名称、图标透明的应用 | 无 `LAUNCHER` 入口 + `labelRes=0x0` | **S1 没有启动图标 (+35)** + 附加项 (+10) |
| 有最高权限、不让卸载 | 激活 `DeviceAdmin` | **S4 设备管理员 (+30)** → `dpm remove-active-admin` 拔掉 |
| 把通讯录/相册/界面传服务器 | 无障碍读屏 + 危险权限 | **S3 开了无障碍 (+40)** → 撤无障碍 + 撤权限 |
| 锁机一样 | 设备管理员 `lockNow()` | 解除设备管理员即失效 |

---

## 二、架构

```
┌──────────────────────────── 手机 ────────────────────────────┐
│                                                              │
│   守护喵 APK (top.adbguard)                                   │
│   ├── MainActivity ......... 引导开权限 + 控制台                │
│   ├── GuardAccessibilityService ── 核心                        │
│   │     A 页面监测 → UiHeuristics 打分 → 告警                  │
│   │     B 拦截音量键（连按 3 次解除冻结）                       │
│   │     C 摇一摇跳转拦截（"干扰陀螺仪"的落地）                  │
│   │     D 跳应用详情页后自动点「强行停止」                      │
│   ├── GuardService ......... 前台服务 + 通知栏三个按钮          │
│   ├── TouchBlockOverlay .... 全屏遮罩，吃掉所有触摸             │
│   ├── GyroGuard ............ 关自动旋转 + 摇一摇反制            │
│   └── DeviceOwnerGuard ..... 被提升后才生效：隐藏/挂起/撤权限   │
│                                                              │
└───────────────┬──────────────────────────────┬───────────────┘
                │ ①通知栏按钮（永远能点到）      │ ②HTTP / UDP
                ▼                              ▼
       ┌────────────────┐        ┌──────────────────────────────┐
       │ 用户自己按      │        │  Python 后端（电脑上跑）      │
       └────────────────┘        │  ├── adbutil.py    ADB 封装  │
                                 │  ├── layout_probe  XML 解析  │
                                 │  ├── detector.py   打分引擎  │
                                 │  ├── inventory.py  全盘体检  │
                                 │  ├── remediate.py  拔刺处置  │
                                 │  ├── server.py     HTTP+UDP  │
                                 │  └── main.py       CLI       │
                                 └──────────┬───────────────────┘
                                            │ adb
                                            ▼
                                 ┌──────────────────────┐
                                 │ 手机（USB / 无线调试）│
                                 │ force-stop / appops  │
                                 │ pm disable-user      │
                                 │ dpm set-device-owner │
                                 └──────────────────────┘
```

**为什么必须有后端？**
因为 Android 的安全模型决定了：普通 App **杀不掉别人的进程**，也**关不了别人的权限**。
能 `am force-stop` 的只有 `shell` / `root` / `Device Owner` 三种身份。
后端的价值就是拿到 `shell` 身份，并且能把你提升成 `Device Owner`——
提升之后，APK 自己才真正有了"隐藏流氓应用"的权力。

---

## 三、能力边界（先把话说清楚）

| 需求 | 能不能做到 | 说明 |
|---|---|---|
| 禁用触摸操作 | ✅ 完全可以 | 全屏 `TYPE_APPLICATION_OVERLAY` 遮罩，吃掉全部触摸事件；连按 3 次音量减解除 |
| 禁用陀螺仪 | ⚠️ 只能"干扰"，不能物理关闭 | Android 从 4.0 起就没有给普通 App 关传感器的公开 API。真正能关的只有 root / 系统应用 / Android 12+ 的 `SensorPrivacyManager`（系统权限）。**所以做法是掐断它的用途**：① 关掉系统自动旋转 ② 检测到 1.2 秒内从任意 App 跳到浏览器/商店 → 判定摇一摇误触 → 立刻 BACK 拉回 ③ 冻结遮罩期间广告根本点不到 |
| APK 强杀别的应用 | ❌ 普通权限做不到 | 曲线方案：退出 → 回桌面 → 跳到「设置 → 应用 → 应用名」详情页 → **无障碍自动点「强行停止」**。这等同于用户手动强杀，是系统认可路径。配了后端就能直接 `am force-stop`，提升 Device Owner 后还能 `setPackagesSuspended` + `setApplicationHidden` |
| 卸载不掉的流氓应用 | ✅ 可以 | 顺序：撤无障碍 → `dpm remove-active-admin` 解管理员 → `pm disable-user --user 0`（图标消失、无法自启）→ 或 `pm uninstall --user 0` |
| 撤销通讯录/相册上传 | ✅ 可以 | `pm revoke` + `appops set ... deny`，16 项危险权限一次清 |

> 自动点击「强行停止」是**限时（10 秒）+ 限目标（只在 `com.android.settings` 里）+ 白名单（只点 强行停止/停用）**。
> 刻意**不**自动点「卸载」和「确定」——不可逆的动作留给用户自己按。

---

## 四、打分规则（两端完全一致）

APK 端 `UiHeuristics.java` 与后端 `detector.py` 用同一套权重，可以互相交叉验证。

| 特征 | 分值 |
|---|---|
| 大片空白 `blankRatio >= 0.62` | **+50** |
| 命中「退出陷阱」类关键词 | **+30** |
| 命中「清理优化」类关键词 | **+30** |
| 命中「奖励诱饵」类关键词 | **+30** |
| 命中「付款/权限陷阱」类关键词 | **+30** |
| 假关闭按钮聚簇（同一粗网格区 ≥3 个无文字小按钮） | **+30** |
| 无文字可点节点 ≥ 8 | **+25** |
| 全屏图 + 文字节点 ≤ 2（伪装成别的 App） | **+40** |
| 前台包名带 `clean/boost/optimize/toolbox…` | **+40** |
| 没有任何可滚动容器 | **+15** |

**判定动作**（满足任一条即处置）：

```
score >= 80
或  大片空白 且 命中任何关键词
或  score >= 60 且 包名高危
```

`blankRatio` 的关键设计：**全屏图片和全屏容器不算"有内容"**，
否则「一整张假截图」会把空白率压到 0，反而漏判。

自检（不需要真机）：

```bash
cd adbguard-backend
python selftest.py
```

会跑三个用例：流氓清理页（期望 100 分 / 命中）、正常列表页（期望 0 分 / 不命中）、
高危包名+正常界面（期望识别包名但不误杀）。

---

## 五、快速开始

### 0. 准备

```bash
# 装 Platform-Tools（adb）
winget install Google.PlatformTools
# 手机：设置 → 关于手机 → 连点 7 次"版本号" → 打开开发者选项
#       开发者选项 → USB 调试
```

### 1. 装 APK

先在 `adbguard-android/local.properties` 里写好你的 SDK 路径（该文件不入库）：

```properties
sdk.dir=/your/path/to/Android/Sdk
```

**Gradle wrapper 已就位**，克隆下来直接编译，不用再跑 `gradle wrapper`：

```bash
cd adbguard-android
./build-apk.sh                                     # 推荐：可把 Gradle 缓存挪出系统盘
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

> **关于 `build-apk.sh`**：它支持把 Gradle 的缓存与临时目录指到**非系统盘**，
> 适合 C 盘空间紧张的情况。脚本顶部三个变量按需改，等价于：

```bash
export GRADLE_USER_HOME="/path/to/gradle-home"     # Gradle 缓存（默认写系统盘）
export TEMP="D:/build-tmp" TMP="D:/build-tmp" TMPDIR="D:/build-tmp"
./gradlew :app:assembleDebug
```

> Windows 上 JVM 读的是 `TEMP` / `TMP`，**不读 `TMPDIR`**（那是 Linux 的规矩），
> 所以三个都要设。不设而系统盘又满时，症状就是 `No space left on device`。

**工具链要求（已实测跑通的组合）：**

| 组件 | 版本 |
|---|---|
| Android Gradle Plugin | 8.13.0 |
| Gradle | 9.1.0（wrapper 自带） |
| JDK | 21 |
| Android SDK | compileSdk 36 |
| 构建产物 | `app/build/outputs/apk/debug/app-debug.apk`（约 97 KB） |

> 项目**不依赖 AndroidX / 任何三方库**，纯系统 API。minSdk 24 / targetSdk 34 / compileSdk 36。
> 也可在 Android Studio 里 "Open an existing project" 指向 `adbguard-android/`
> （Gradle JDK 选 21；Gradle user home 按需改到非系统盘）。

### 2. 走引导（APK 内）

打开守护喵，界面会给你 5 步清单：

| 步骤 | 必需？ | 做什么 |
|---|---|---|
| ① 允许显示悬浮窗 | **必需** | 冻结遮罩的载体 |
| ② 开启无障碍服务 | **必需** | 核心。开了才有音量键、才能监测页面 |
| ③ 允许发送通知 | 建议 | 告警弹得出来 |
| ④ 允许修改系统设置 | 可选 | "干扰陀螺仪"用（关自动旋转） |
| ⑤ 连接后端 ADB | 可选 | 配了才能真 force-stop |

第②步点完后，界面会进入**「正在等待无障碍服务启动···」**状态并轮询，
直到 `GuardAccessibilityService` 的进程真的起来（不是开关打开，是服务连上），
才自动打勾、提示「无障碍已连接 ✓ 音量键已接管」，然后放行到主面板。

主面板上就是你要的两个大按钮：

```
① 冻结屏幕（禁用触摸）      ← 点完整个世界安静，连按 3 次音量减恢复
② 申请后端（ADB）           ← 自动发现 / 手填 IP / 复制提权命令
```

### 3. 跑后端

```bash
cd adbguard-backend

python main.py devices                 # 看设备
python main.py scan                    # 全盘体检，把那 3 个隐身应用揪出来
python main.py watch                   # 实时监测（只告警）
python main.py watch --fix             # 实时监测 + 自动清剿
python main.py serve                   # 起后端，等手机连（HTTP + UDP 自动发现）
```

手机端「② 申请后端」→「自动发现」——UDP 广播一句就能自动填好电脑 IP，
不用让爸妈手输地址。

#### 图形界面（可选，不想敲命令就用它）

`gui.py` 就是上面这些命令的窗口版 —— 同一个后端，同一套打分逻辑，只是不用记参数：

```bash
cd adbguard-backend
python -m venv .venv
.venv/Scripts/python -m pip install customtkinter pillow   # 只有界面需要这两个
.venv/Scripts/python gui.py
```

两个页签：

| 页签 | 内容 |
|---|---|
| **应用管理** | 左侧列出第三方应用（可搜索，可按「高危 / 有悬浮窗 / 有设备管理员 / 无启动图标」筛）；右侧是详情：版本、安装来源、首装时间、是否悬浮窗 / 无障碍 / 设备管理员 / Device Owner，以及**已授予权限逐条列出、危险项标红**。选中后可直接强杀 / 冻解 / 解管理员 / 撤权限 / 拔刺 / 卸载 |
| **屏幕 / 设备** | 实时抓屏（可勾选每 2 秒自动刷新）+ 机型 / Android 版本 / SDK / root / 当前前台应用，并能一键「关掉它」 |

- **多选 = 批量处置**，处置顺序仍走 `remediate.quarantine`，界面不重造那套逻辑
- 所有 ADB 调用都在后台线程跑，界面不会卡死
- 依赖装在 `adbguard-backend/.venv/`，不污染系统 Python；抓屏临时文件 `_screen.png` 已排除在 `.gitignore` 外
- 自检：`.venv/Scripts/python gui.py --selftest`（无需真机，验证列表渲染 / 筛选 / 详情解析）

### 4. 提权（可选，但很值）

```bash
python main.py promote
# 等价于： adb shell dpm set-device-owner top.adbguard/.GuardDeviceAdminReceiver
```

成功之后 APK 自己就获得了隐藏/挂起流氓应用的能力，
主面板的按钮会变成「② 已接入 Device Owner · 点此撤销」。
> 失败多半是因为手机上还登着账号，或已有别的 Device Owner。

#### 怎么退出 Device Owner（重要，先读这段）

**Device Owner 是"粘性"的** —— 一旦设定，App 会变成不可卸载、不可停用，
而普通手段拔不掉。`dpm` 的官方帮助写得很明白：

> `dpm remove-active-admin`: Disables an active admin, **the admin must have
> declared `android:testOnly` in the application in its manifest.**
> This will also remove device and profile owners.

所以本项目**已经声明了 `android:testOnly="true"` 作为逃生舱**。退出有三条路：

| # | 方式 | 说明 |
|---|---|---|
| ① | **APK 内点一下**（推荐） | 守护喵主面板 →「② 已接入 Device Owner · 点此撤销」→ 二次确认。内部调 `clearDeviceOwnerApp()`，是 Android **唯一可靠**的降权方式 |
| ② | `python main.py demote` | 等价于 `adb shell dpm remove-active-admin top.adbguard/.GuardDeviceAdminReceiver`，靠的就是上一段提到的 testOnly 声明 |
| ③ | 恢复出厂设置 | 前两条都失效时的终极手段，**正常用不到** |

> ⚠️ 因为声明了 `testOnly`，安装时**必须带 `-t`**：
> ```bash
> adb install -t -r app/build/outputs/apk/debug/app-debug.apk
> ```
> 忘带会报 `INSTALL_FAILED_TEST_ONLY`。Android Studio 直接 Run 会自动加，不用管。

#### Device Owner 会影响日常使用吗？

| 问题 | 答案 |
|---|---|
| 会锁机吗？ | **不会**。本项目**没有**用 `lockTask` / Kiosk 模式，不限制你正常用手机 |
| 会动我的数据吗？ | **不会**。`device_admin.xml` 只申请 `force-lock`，刻意**不**申请 `wipe-data` / `reset-password` |
| 多出什么能力？ | 仅一项：能挂起 / 隐藏流氓应用、撤销它的权限 |
| 有什么不便？ | 提权期间**守护喵自己不能直接卸载**，要先按上面步骤撤销 |

> 换句话说：这是个"**功能解锁**"，不是"**把手机交出去**"。不想要时点一下就退。

---

## 六、后端命令一览

> 不想敲命令？`python gui.py` 是同能力集的可视化控制台，见「五、3. 图形界面」。

| 命令 | 干什么 |
|---|---|
| `devices` | 列出 ADB 设备，并给出排查指引 |
| `connect ip:port` / `pair ip:port 配对码` | 无线调试（Android 11+） |
| `watch [--fix] [--disable]` | **核心**：每 1.2s 抓一次前台界面并打分，命中就处置 |
| `scan [--fix] [--days N]` | 全盘体检，找出"没图标 / 有悬浮窗 / 开了无障碍 / 是设备管理员 / 最近装的"应用 |
| `quarantine <pkg>` | 拔刺组合拳：撤无障碍 → 解管理员 → 撤悬浮窗 → 撤 16 项权限 → force-stop |
| `disable <pkg>` / `restore <pkg>` | `pm disable-user` / `pm enable` |
| `uninstall <pkg>` | `pm uninstall --user 0` |
| `kill <pkg>` | 只 force-stop |
| `revoke-overlay` | 一键撤销**所有**第三方应用的悬浮窗权限（专治"弹窗关不掉"） |
| `promote` | 把守护喵提升为 Device Owner |
| `demote` | **撤销** Device Owner，让守护喵退回可卸载状态（靠 manifest 里的 `testOnly`）|
| `serve [--port N]` | 起 HTTP(8720) + UDP 发现(8721)，等手机连 |

### 体检的 7 个信号

| 编号 | 信号 | 分 |
|---|---|---|
| S1 | 第三方包但没有 `LAUNCHER` 入口（**桌面看不见**） | +35 |
| S2 | 持有 `SYSTEM_ALERT_WINDOW`（弹窗的根子） | +30 |
| S3 | 开启并常驻无障碍服务（可读屏、可偷界面） | +40 |
| S4 | 已激活设备管理员（这就是"卸不掉"） | +30 |
| S5 | 包名带 `clean/boost/optimize/toolbox…` | +25 |
| S6 | 7 天内新装的 | +20 |
| S7 | `installerPackageName` 是浏览器/下载器 | +20 |

≥60 可疑，≥80 建议直接处置。

---

## 七、文件清单

```
adbguard-android/                      Android APK（纯 Java，无三方依赖）
├── app/src/main/AndroidManifest.xml
├── app/src/main/java/top/adbguard/
│   ├── MainActivity.java              引导流程 + 控制台
│   ├── GuardAccessibilityService.java 核心：监测 / 音量键 / 摇一摇拦截 / 自动点击
│   ├── GuardService.java              前台服务 + 通知栏 3 按钮 + closeCurrent()
│   ├── UiHeuristics.java              打分引擎（Java 版）
│   ├── TouchBlockOverlay.java         冻结遮罩
│   ├── GyroGuard.java                 关自动旋转 + 摇一摇反制
│   ├── DeviceOwnerGuard.java          DO 能力：挂起/隐藏/撤权限
│   ├── AppDetailsOpener.java          跳「设置→应用→应用详情」+ 自动点强行停止
│   ├── Permissions.java               各项权限检查 + 跳转
│   ├── Prefs.java / ToastHelper.java / GuardActionReceiver.java
│   └── GuardDeviceAdminReceiver.java
├── app/src/main/res/xml/accessibility_service_config.xml
├── app/src/main/res/xml/device_admin.xml
└── app/src/main/res/layout/activity_main.xml

adbguard-backend/                      Python 后端（CLI 零三方依赖；GUI 需 customtkinter）
├── config.py          阈值 / 关键词表 / 权限表
├── adbutil.py         ADB 封装（含多路兜底拿前台包名）
├── layout_probe.py    uiautomator XML → 节点列表
├── detector.py        打分引擎（Python 版，与 Java 对称）
├── inventory.py       全盘体检（7 信号）
├── remediate.py       处置动作（拔刺组合拳）
├── server.py          HTTP API + UDP 自动发现
├── main.py            CLI
├── gui.py             可视化控制台（customtkinter，唯一用到三方包的模块）
└── selftest.py        打分逻辑自检（无需真机）
```

---

## 八、已知限制 / 待办

1. **`uiautomator dump` 有 0.5~1.5s 延迟**，所以 watch 用 1.2s 节奏。
   某些 ROM 上 `uiautomator` 被裁掉了，会自动退回 `dumpsys activity top` 文本特征匹配（精度较低）。
2. **厂商定制 ROM 差异大**：`dumpsys window` 输出格式、`cmd package query-activities` 的可用性
   在不同 ROM 上不一样，`adbutil.py` 里都做了多路兜底，但真实设备上可能需要微调正则。
3. **`pm disable-user` 在部分 ROM 上会被拒绝**（返回 `SecurityException`）。
   这时退一步用 `quarantine`（不 disable 那步），效果是"杀进程 + 撤权限"，至少它弹不出来了。
4. 陀螺仪**物理关闭做不到**，见第三节。如果你能接受 root，可以加 `SensorPrivacyManager` 那条路。
5. APK 尚未做 ProGuard/R8 混淆验证（`proguard-rules.pro` 里已经把四个组件 keep 住了）。

---

## 九、安全与隐私

- 所有打分**在本机完成**，节点文本不会离开手机。
- 只有**命中的可疑包名**会上报给你自己部署的后端（`/api/blocked`）。
- 后端不会主动连外部网络，只监听局域网 8720/8721。
- APK 刻意**不**申请 `wipe-data` / `reset-password` 等危险设备策略。
- 自动点击只点「强行停止 / 停用」，**永不自动点「卸载」**。

---

## 十、许可证

**GNU General Public License v3.0** —— 全文见 [LICENSE](LICENSE)。

```
守护喵 AdbGuard
Copyright (C) 2026 ciallo0721-cmd / Ayase Yukio
```

你可以自由使用、修改、分发，**但衍生作品必须以同样的许可证开源**。
简单说：欢迎拿去用、拿去改，但别包一层壳做成闭源捞钱的工具。
