"""
config.py —— 后端可调参数集中在这里，改这一处就够。
"""

# ----------------------------------------------------------------------
# 检测阈值（和 APK 端 UiHeuristics 保持同一套）
# ----------------------------------------------------------------------
SCORE_ACT = 80          # 综合分 >= 80 直接处置
SCORE_SOFT = 60         # 60 分以上 + 高危包 也处置
BLANK_ACT = 0.62        # 空白率超过它就 +50 分
BLANK_SOFT = 0.55       # 低于 0.62 但高于这个值，算"偏空"

# ----------------------------------------------------------------------
# 处罚策略
# ----------------------------------------------------------------------
AUTO_KILL = True            # 命中后是否自动 force-stop
AUTO_REVOKE_OVERLAY = True  # 是否撤销 SYSTEM_ALERT_WINDOW（弹窗的根子）
AUTO_REVOKE_PERMS = True    # 是否撤销通讯录/相册/存储等危险权限
AUTO_DISABLE = False        # 是否 pm disable-user（默认关，比较重，需要用户开）
CONFIRM_BEFORE_DISABLE = True

# 危险权限 —— 全都是流氓应用爱偷的
DANGEROUS_PERMS = [
    "android.permission.READ_CONTACTS",
    "android.permission.WRITE_CONTACTS",
    "android.permission.READ_CALL_LOG",
    "android.permission.WRITE_CALL_LOG",
    "android.permission.READ_SMS",
    "android.permission.RECEIVE_SMS",
    "android.permission.READ_EXTERNAL_STORAGE",
    "android.permission.WRITE_EXTERNAL_STORAGE",
    "android.permission.READ_PHONE_STATE",
    "android.permission.READ_PHONE_NUMBERS",
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.CAMERA",
    "android.permission.RECORD_AUDIO",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.SYSTEM_ALERT_WINDOW",
    "android.permission.PACKAGE_USAGE_STATS",
]

# ----------------------------------------------------------------------
# 监测节奏
# ----------------------------------------------------------------------
WATCH_INTERVAL = 1.2        # 秒，两次布局抓取之间的间隔
LAYOUT_TIMEOUT = 8          # uiautomator dump 超时
SAME_PKG_COOLDOWN = 12      # 同一个包处置后多少秒内不再重复处置

# ----------------------------------------------------------------------
# 网络
# ----------------------------------------------------------------------
HTTP_PORT = 8720
UDP_PORT = 8721
UDP_MAGIC = "ADBGUARD?"

# ----------------------------------------------------------------------
# ADB
# ----------------------------------------------------------------------
ADB_PATH = "adb"            # 如果 adb 不在 PATH，填绝对路径，比如 r"C:\platform-tools\adb.exe"

# ----------------------------------------------------------------------
# 关键词表（与 APK 端同源）
# ----------------------------------------------------------------------
KW_EXIT = [
    "是否退出", "确认退出", "确定退出", "退出应用", "再想想", "狠心离开",
    "真的要离开", "确认离开", "退出将", "退出后", "放弃", "不要走",
    "再逛逛", "留下吧", "舍不得",
]

KW_CLEAN = [
    "手机清理", "一键清理", "立即清理", "深度清理", "垃圾清理", "扫描垃圾",
    "内存不足", "加速", "优化大师", "清理大师", "手机管家", "病毒", "风险",
    "检测到", "存在风险", "发热", "清理缓存", "立即优化", "免费清理",
    "电池", "省电", "跑分", "降温",
]

KW_REWARD = [
    "奖励", "礼物", "红包", "领取", "恭喜", "免费领", "金币", "元宝",
    "提现", "翻倍", "点击领取", "抽奖", "中奖", "福利", "限时",
]

KW_TRAP = [
    "开通会员", "自动续费", "仅需", "0元", "输入手机号", "验证码",
    "允许访问", "通讯录", "相册", "定位权限",
]

KEYWORD_GROUPS = [KW_EXIT, KW_CLEAN, KW_REWARD, KW_TRAP]

# 包名可疑特征（伪装成系统工具的命名套路）
RISKY_PKG_HINTS = [
    "clean", "boost", "optimize", "optimizer", "speedup", "ram",
    "lock", "battery", "tools", "scan", "master", "guard",
    "temple", "island", "media", "toolbox", "pack", "booster",
    "weather", "wifi", "charge", "antivirus", "security",
]

# 白名单：这些包一律跳过全部风险判据（不打分、不进处置流程）
# 注意：守护喵自己也持悬浮窗 + 常驻无障碍 —— 特征与流氓应用完全相同，
# 不排除的话每次体检都会把自己打成 100+ 分的「高危」。
# 故意不设「com.android.* 前缀白名单」：伪装者恰恰最爱用 com.android.xxx 这类包名，
# 放行前缀等于给它们开门。系统包本来就在系统分区，不会进「第三方应用」列表。
SELF_PACKAGES = {
    "top.adbguard",         # 守护喵自己
}

# 广告落点（摇一摇跳转常见目的地）
AD_LANDING_HINTS = [
    "browser", "market", "appstore", "download", "mtt", "ucmobile",
    "qqdownloader", "appsearch",
]
