package top.adbguard;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * MainActivity —— 引导 + 控制台。
 *
 * 整个界面分两块：
 *   【引导面板】先是"给爸妈手机装好后的一次性配置"：
 *       ① 悬浮窗权限        —— 冻结遮罩的载体，必需
 *       ② 无障碍服务        —— 核心！开了才有音量键、才监测页面，必需
 *       ③ 通知权限          —— Android 13+，告警要能弹出来
 *       ④ 修改系统设置      —— 可选，用来"干扰陀螺仪"（关掉自动旋转）
 *       ⑤ 后端 ADB          —— 可选，配了才能把包名真正 force-stop 掉
 *     其中第②步点完之后会进入"等待无障碍"状态，界面上会转着等，
 *     一旦 Service 真的连上（不是开关打开，是进程起来），自动打勾并放行。
 *
 *   【守护面板】引导完成后露出，两个大按钮就是最初设计的那两个：
 *       ① 冻结屏幕（禁用触摸）—— 连按 3 次音量减恢复
 *       ② 申请后端（ADB）
 *     外加三个快捷动作和实时日志。
 */
public class MainActivity extends Activity {

    private static final int REQ_NOTIF = 1001;

    /* ---- 步骤索引 ---- */
    private static final int S_OVERLAY = 0;
    private static final int S_A11Y    = 1;
    private static final int S_NOTIF   = 2;
    private static final int S_WRITE   = 3;
    private static final int S_BACKEND = 4;

    private Prefs prefs;
    private final Handler h = new Handler(Looper.getMainLooper());

    private LinearLayout stepContainer, panelGuide, panelMain;
    private TextView tvWaiting, tvShieldState, tvA11yState, tvForeground, tvLastCheck, tvLog;
    private Button btnFinishGuide, btnFreeze, btnBackend, btnCloseCurrent, btnHome,
            btnUnfreeze, btnCheckNow;
    private EditText etHost, etPort;

    private StepRow[] steps;
    private boolean waitingA11y = false;
    private int waitDots = 0;
    private long a11yLiveSince = 0L;

    /* ------------------------------------------------------------------ */
    /*  单个引导步骤的行                                                     */
    /* ------------------------------------------------------------------ */

    private class StepRow {
        LinearLayout row;
        TextView badge;
        TextView title;
        TextView desc;
        Button action;
        boolean done;

        void apply(boolean ok) {
            done = ok;
            badge.setText(ok ? "✓" : "!");
            badge.setTextColor(ok ? Color.WHITE : Color.WHITE);
            setBg(badge, ok ? 0xFF12A150 : 0xFFE08A00, 14);
            action.setText(ok ? "已完成" : "去开启");
            action.setEnabled(!ok);
            action.setAlpha(ok ? 0.5f : 1f);
        }
    }

    /* ------------------------------------------------------------------ */

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        prefs = new Prefs(this);

        stepContainer  = findViewById(R.id.stepContainer);
        panelGuide     = findViewById(R.id.panelGuide);
        panelMain      = findViewById(R.id.panelMain);
        tvWaiting      = findViewById(R.id.tvWaiting);
        tvShieldState  = findViewById(R.id.tvShieldState);
        tvA11yState    = findViewById(R.id.tvA11yState);
        tvForeground   = findViewById(R.id.tvForeground);
        tvLastCheck    = findViewById(R.id.tvLastCheck);
        tvLog          = findViewById(R.id.tvLog);
        btnFinishGuide = findViewById(R.id.btnFinishGuide);
        btnFreeze      = findViewById(R.id.btnFreeze);
        btnBackend     = findViewById(R.id.btnBackend);
        btnCloseCurrent= findViewById(R.id.btnCloseCurrent);
        btnHome        = findViewById(R.id.btnHome);
        btnUnfreeze    = findViewById(R.id.btnUnfreeze);
        btnCheckNow    = findViewById(R.id.btnCheckNow);
        etHost         = findViewById(R.id.etHost);
        etPort         = findViewById(R.id.etPort);

        buildSteps();
        bindButtons();

        etHost.setText(prefs.backendHost());
        etPort.setText(String.valueOf(prefs.backendPort()));

        // 只要不是首次，直接进主面板；想重看引导就点标题
        if (prefs.getBool(Prefs.K_ONBOARDED, false) && Permissions.isAccessibilityLive()) {
            showMain();
        } else {
            showGuide();
        }

        // 常驻守护服务先拉起来
        GuardService.start(this);

        tvShieldState.setOnClickListener(v -> showGuide());

        log("启动 " + Build.MANUFACTURER + " " + Build.MODEL
                + " / Android " + Build.VERSION.RELEASE);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshAll();
        // 从系统设置页回来时，无障碍可能刚被打开，补一次探测
        if (waitingA11y) startWaitingA11y();
    }

    @Override
    protected void onPause() {
        super.onPause();
        // 保存后端配置，免得用户填了又丢
        saveBackendSilently();
    }

    @Override
    protected void onDestroy() {
        h.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    /* ------------------------------------------------------------------ */
    /*  引导步骤构建                                                        */
    /* ------------------------------------------------------------------ */

    private void buildSteps() {
        steps = new StepRow[5];

        steps[S_OVERLAY] = addStep(
                "① 允许显示悬浮窗",
                "「冻结」靠它盖住整个屏幕，流氓 App 的假关闭按钮就全废了。",
                v -> {
                    try {
                        startActivity(Permissions.overlayIntent(this));
                    } catch (Throwable t) {
                        toast("请手动：设置 → 应用 → 守护喵 → 显示在其他应用上层");
                    }
                });

        steps[S_A11Y] = addStep(
                "② 开启无障碍服务（核心）",
                "只有开了它，音量键才拦得住、页面监测才有节点可读。"
                        + "在列表里找到「守护喵 · 页面监测」并打开。",
                v -> {
                    try {
                        startActivity(Permissions.accessibilityIntent());
                        toast("找到「守护喵 · 页面监测」打开它");
                    } catch (Throwable t) {
                        toast("请手动：设置 → 无障碍 → 已下载的服务");
                    }
                    startWaitingA11y();
                });

        steps[S_NOTIF] = addStep(
                "③ 允许发送通知",
                "可疑页面告警要靠通知弹出来，通知栏那 3 个按钮也是操作台。"
                        + "（Android 13+ 会顺带申请「附近的设备」，UDP 广播找后端要用）",
                v -> {
                    if (Build.VERSION.SDK_INT >= 33) {
                        // 通知 + 附近 WiFi 设备一起申请。
                        // 少了 NEARBY_WIFI_DEVICES，第⑤步会「静默失败」——
                        // 不报错、不崩溃，就是死活找不到后端。
                        java.util.List<String> need = new java.util.ArrayList<>();
                        if (!Permissions.hasNotification(this)) {
                            need.add(Manifest.permission.POST_NOTIFICATIONS);
                        }
                        if (!Permissions.hasNearbyWifi(this)) {
                            need.add(Manifest.permission.NEARBY_WIFI_DEVICES);
                        }
                        if (need.isEmpty()) {
                            toast("通知与附近设备权限都已授权");
                            refreshAll();
                        } else {
                            requestPermissions(need.toArray(new String[0]), REQ_NOTIF);
                        }
                    } else {
                        toast("当前系统版本无需单独授权");
                        refreshAll();
                    }
                });

        steps[S_WRITE] = addStep(
                "④ 允许修改系统设置（可选）",
                "用来「干扰陀螺仪」：关掉自动旋转，减少摇一摇跳广告的触发。",
                v -> {
                    try {
                        startActivity(GyroGuard.writeSettingsIntent(this));
                    } catch (Throwable t) {
                        toast("请手动：设置 → 应用 → 特殊应用权限 → 修改系统设置");
                    }
                });

        steps[S_BACKEND] = addStep(
                "⑤ 连接后端 ADB（可选）",
                "配了后端才能真正 force-stop 掉流氓应用，还能把它提升成"
                        + "Device Owner 直接隐藏/撤权限。",
                v -> {
                    toast("在电脑上运行 backend/main.py，用「自动发现」或手填 IP");
                    if (panelMain.getVisibility() != View.VISIBLE) showMain();
                    h.postDelayed(() -> {
                        if (etHost != null) etHost.requestFocus();
                    }, 300);
                });
    }

    /** 造一行引导步骤。 */
    private StepRow addStep(String title, String desc, View.OnClickListener onAction) {
        StepRow s = new StepRow();

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(dp(14), dp(14), dp(14), dp(14));
        setBg(row, 0xFFFFFFFF, 14);
        LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rlp.bottomMargin = dp(10);
        row.setLayoutParams(rlp);

        TextView badge = new TextView(this);
        badge.setText("!");
        badge.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        badge.setGravity(Gravity.CENTER);
        badge.setTextColor(Color.WHITE);
        setBg(badge, 0xFFE08A00, 14);
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(dp(28), dp(28));
        badge.setLayoutParams(blp);
        row.addView(badge);

        LinearLayout textCol = new LinearLayout(this);
        textCol.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        tlp.leftMargin = dp(12);
        tlp.rightMargin = dp(10);
        textCol.setLayoutParams(tlp);

        TextView tvTitle = new TextView(this);
        tvTitle.setText(title);
        tvTitle.setTextColor(0xFF16233A);
        tvTitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        tvTitle.setTypeface(tvTitle.getTypeface(), android.graphics.Typeface.BOLD);
        textCol.addView(tvTitle);

        TextView tvDesc = new TextView(this);
        tvDesc.setText(desc);
        tvDesc.setTextColor(0xFF6B7A93);
        tvDesc.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        tvDesc.setPadding(0, dp(4), 0, 0);
        textCol.addView(tvDesc);

        row.addView(textCol);

        Button action = new Button(this);
        action.setText("去开启");
        action.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        action.setTextColor(Color.WHITE);
        action.setBackgroundColor(0xFF2F6FED);
        LinearLayout.LayoutParams alp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(40));
        action.setLayoutParams(alp);
        action.setOnClickListener(onAction);
        row.addView(action);

        stepContainer.addView(row);

        s.row = row; s.badge = badge; s.title = tvTitle; s.desc = tvDesc; s.action = action;
        return s;
    }

    /* ------------------------------------------------------------------ */
    /*  面板切换 + 刷新                                                     */
    /* ------------------------------------------------------------------ */

    private void showGuide() {
        panelGuide.setVisibility(View.VISIBLE);
        panelMain.setVisibility(View.GONE);
        refreshSteps();
    }

    private void showMain() {
        panelGuide.setVisibility(View.GONE);
        panelMain.setVisibility(View.VISIBLE);
        refreshMain();
    }

    private void refreshAll() {
        refreshSteps();
        refreshMain();
        if (panelMain.getVisibility() == View.VISIBLE) refreshMain();
    }

    private void refreshSteps() {
        if (steps == null) return;

        boolean okOverlay = Permissions.hasOverlay(this);
        boolean okA11y    = Permissions.hasAccessibility(this);
        boolean liveA11y  = Permissions.isAccessibilityLive();
        // 通知 + 附近设备都要有：少了 NEARBY_WIFI_DEVICES 第⑤步会静默失败
        boolean okNotif   = Permissions.hasNotification(this)
                && Permissions.hasNearbyWifi(this);
        boolean okWrite   = Permissions.hasWriteSettings(this);
        boolean okBackend = !TextUtils.isEmpty(prefs.backendHost());

        // 无障碍以"Service 真的跑起来"为准，不只看开关
        steps[S_OVERLAY].apply(okOverlay);
        steps[S_A11Y].apply(liveA11y);
        steps[S_NOTIF].apply(okNotif);
        steps[S_WRITE].apply(okWrite);
        steps[S_BACKEND].apply(okBackend);

        // 无障碍等待条
        if (liveA11y) {
            waitingA11y = false;
            if (System.currentTimeMillis() - a11yLiveSince < 4000 && a11yLiveSince > 0) {
                tvWaiting.setVisibility(View.VISIBLE);
                tvWaiting.setText("✓ 无障碍已连接，音量键已接管\n"
                        + "现在连按 3 次「音量减」就能解除冻结。");
            } else {
                tvWaiting.setVisibility(View.GONE);
            }
        } else if (okA11y && !liveA11y) {
            tvWaiting.setVisibility(View.VISIBLE);
            tvWaiting.setText("开关已打开，正在等待服务启动…（若一直不动，"
                    + "回设置里关掉再打开一次）");
        } else if (!waitingA11y) {
            tvWaiting.setVisibility(View.GONE);
        }

        // 必需项齐了才让进主面板
        boolean core = okOverlay && liveA11y;
        btnFinishGuide.setVisibility(core ? View.VISIBLE : View.GONE);
        btnFinishGuide.setText(okOverlay ? "完成引导，进入守护面板"
                : "还差「悬浮窗」权限");
    }

    private void refreshMain() {
        boolean frozen = prefs.isFrozen();
        boolean live = Permissions.isAccessibilityLive();

        tvShieldState.setText(frozen ? "守护状态：屏幕已冻结" : "守护状态：运行中");
        tvA11yState.setText("无障碍：" + (live ? "已连接 ✓（音量键可用）" : "未开启 ✗"));
        tvA11yState.setTextColor(live ? 0xFF12A150 : 0xFFD0453E);

        GuardAccessibilityService a11y = GuardAccessibilityService.get();
        String fg = a11y != null ? a11y.currentPackage() : "";
        tvForeground.setText("当前前台：" + (TextUtils.isEmpty(fg) ? "--" : fg));

        UiHeuristics.Result r = a11y != null ? a11y.getLastResult() : null;
        tvLastCheck.setText("最近检查：" + (r == null ? "尚未检查" : r.toString()));

        btnFreeze.setText(frozen ? "① 解除冻结（恢复触摸）" : getString(R.string.btn_freeze));
        btnFreeze.setBackgroundColor(frozen ? 0xFFD0453E : 0xFF2F6FED);

        boolean owner = DeviceOwnerGuard.isOwner(this);
        btnBackend.setText(owner ? "② 已接入 Device Owner · 点此撤销" : getString(R.string.btn_backend));
        btnBackend.setBackgroundColor(owner ? 0xFF0D7A3C : 0xFF12A150);
    }

    /* ------------------------------------------------------------------ */
    /*  等待无障碍                                                          */
    /* ------------------------------------------------------------------ */

    private final Runnable a11yPoller = new Runnable() {
        @Override public void run() {
            boolean live = Permissions.isAccessibilityLive();
            if (live) {
                if (a11yLiveSince == 0L) a11yLiveSince = System.currentTimeMillis();
                waitingA11y = false;
                refreshSteps();
                log("无障碍服务已连接，音量键 / 页面监测 / 摇一摇拦截 全部就位");
                toast("无障碍已连接 ✓ 音量键已接管");
                prefs.putBool(Prefs.K_ONBOARDED, true);
                // 让"已连接"提示停留一会儿再进主面板
                h.postDelayed(() -> {
                    if (panelGuide.getVisibility() == View.VISIBLE
                            && Permissions.isAccessibilityLive()
                            && Permissions.hasOverlay(MainActivity.this)) {
                        showMain();
                    }
                }, 1500);
                return;
            }
            waitDots = (waitDots + 1) % 4;
            StringBuilder sb = new StringBuilder("正在等待无障碍服务启动");
            for (int i = 0; i < waitDots; i++) sb.append("·");
            sb.append("\n请去「设置 → 无障碍 → 已下载的服务」打开「守护喵 · 页面监测」");
            tvWaiting.setVisibility(View.VISIBLE);
            tvWaiting.setText(sb.toString());
            h.postDelayed(this, 700);
        }
    };

    private void startWaitingA11y() {
        if (Permissions.isAccessibilityLive()) {
            refreshSteps();
            return;
        }
        waitingA11y = true;
        tvWaiting.setVisibility(View.VISIBLE);
        h.removeCallbacks(a11yPoller);
        h.postDelayed(a11yPoller, 600);
    }

    /* ------------------------------------------------------------------ */
    /*  按钮绑定                                                            */
    /* ------------------------------------------------------------------ */

    private void bindButtons() {
        btnFinishGuide.setOnClickListener(v -> {
            prefs.putBool(Prefs.K_ONBOARDED, true);
            showMain();
        });

        // ① 冻结屏幕
        btnFreeze.setOnClickListener(v -> {
            if (!TouchBlockOverlay.canOverlay(this)) {
                toast("先给「显示在其他应用上层」权限");
                try { startActivity(Permissions.overlayIntent(this)); } catch (Throwable ignore) { }
                return;
            }
            if (!Permissions.isAccessibilityLive()) {
                toast("请先开启无障碍，否则没法用音量键解除");
                startWaitingA11y();
                showGuide();
                return;
            }
            if (prefs.isFrozen()) {
                GuardService.unfreeze(this, "界面按钮");
                log("解除冻结（界面按钮）");
            } else {
                GuardService.freeze(this, "界面按钮");
                log("冻结屏幕：已盖住全部触摸，连按 3 次音量减可恢复");
            }
            h.postDelayed(this::refreshMain, 400);
        });

        // ② 申请后端（ADB）—— 若已经是 Device Owner，这个按钮变成"撤销身份"入口
        btnBackend.setOnClickListener(v -> {
            if (DeviceOwnerGuard.isOwner(this)) askReleaseOwnership();
            else requestBackend();
        });

        btnCloseCurrent.setOnClickListener(v -> {
            GuardAccessibilityService a = GuardAccessibilityService.get();
            String fg = a != null ? a.currentPackage() : "";
            log("关闭当前 App：" + (TextUtils.isEmpty(fg) ? "未识别" : fg));
            GuardService.closeCurrent(this);
        });

        btnHome.setOnClickListener(v -> {
            GuardAccessibilityService a = GuardAccessibilityService.get();
            if (a != null) a.goHome(); else toast("请先开启无障碍");
        });

        btnUnfreeze.setOnClickListener(v -> {
            GuardService.unfreeze(this, "界面按钮");
            log("强制解除冻结");
            h.postDelayed(this::refreshMain, 300);
        });

        btnCheckNow.setOnClickListener(v -> {
            GuardAccessibilityService a = GuardAccessibilityService.get();
            if (a == null) { toast("请先开启无障碍"); return; }
            UiHeuristics.Result r = a.runCheckNow();
            String s = r == null ? "无结果" : r.toString();
            log("手动检查：" + s);
            tvLastCheck.setText("最近检查：" + (r == null ? "--" : r.toString()));
            toast(r != null && r.shouldAct() ? "命中可疑页面！" : "未发现明显特征");
        });

        // 后端区
        findViewById(R.id.btnSaveBackend).setOnClickListener(v -> {
            saveBackendSilently();
            log("后端地址已保存：" + prefs.backendHost() + ":" + prefs.backendPort());
            toast("已保存");
            refreshSteps();
        });

        findViewById(R.id.btnDiscover).setOnClickListener(v -> {
            toast("正在广播搜索后端…");
            log("UDP 广播发现后端中…");
            BackendClient.discover(prefs, (ok, msg) -> h.post(() -> {
                log("发现结果：" + msg);
                toast(msg);
                if (ok) {
                    etHost.setText(prefs.backendHost());
                    etPort.setText(String.valueOf(prefs.backendPort()));
                    refreshSteps();
                }
            }));
        });

        findViewById(R.id.btnPing).setOnClickListener(v -> {
            saveBackendSilently();
            log("测试后端连接 " + prefs.backendHost() + ":" + prefs.backendPort());
            BackendClient.ping(prefs, (ok, msg) -> h.post(() -> {
                log("后端：" + msg);
                toast(msg);
            }));
        });
    }

    /* ------------------------------------------------------------------ */
    /*  撤销 Device Owner —— 逃生舱                                          */
    /* ------------------------------------------------------------------ */

    /**
     * 二次确认后主动放弃 Device Owner 身份。
     *
     * 必须是"用户明确点两次"的动作：撤销后 APK 会退回普通应用，
     * 失去挂起 / 隐藏 / 撤权限这三项能力，但换来"可以正常卸载"。
     */
    private void askReleaseOwnership() {
        new AlertDialog.Builder(this)
                .setTitle("撤销 Device Owner？")
                .setMessage("撤销后守护喵会退回普通应用：\n\n"
                        + "· 失去「挂起 / 隐藏别的应用 / 撤销对方权限」这三项能力\n"
                        + "· 好处 —— 终于能在系统设置里正常卸载它了\n\n"
                        + "确定要继续吗？")
                .setNegativeButton("取消", null)
                .setPositiveButton("确认撤销", (dlg, w) -> {
                    boolean ok = DeviceOwnerGuard.releaseOwnership(this);
                    if (ok) {
                        log("已撤销 Device Owner，可正常卸载。adb 兜底命令："
                                + DeviceOwnerGuard.demoteCommand(this));
                        toast("已撤销，现在可以正常卸载了");
                    } else {
                        log("撤销未生效：当前可能不是 Device Owner");
                        toast("撤销失败，详见日志");
                    }
                    refreshMain();
                })
                .show();
    }

    /* ------------------------------------------------------------------ */
    /*  申请后端：把"怎么做"讲清楚                                            */
    /* ------------------------------------------------------------------ */

    private void requestBackend() {
        String host = etHost.getText().toString().trim();
        String port = etPort.getText().toString().trim();
        if (!TextUtils.isEmpty(host)) {
            prefs.putString(Prefs.K_BACKEND_HOST, host);
            prefs.putInt(Prefs.K_BACKEND_PORT, parseInt(port, 8720));
        }

        StringBuilder sb = new StringBuilder();
        sb.append("后端（ADB）能做的事：\n");
        sb.append("· 直连手机 force-stop 流氓应用\n");
        sb.append("· 撤销它的悬浮窗权限（弹窗的根子）\n");
        sb.append("· 撤销通讯录 / 相册 / 存储授权\n");
        sb.append("· 把你提升为 Device Owner，之后可以隐藏并挂起流氓应用\n\n");
        sb.append("电脑上跑：\n");
        sb.append("  python backend/main.py --serve\n\n");
        sb.append("USB 连接后后端会自动列出设备；无线调试就填配对地址。\n");
        sb.append("提升 Device Owner 的命令：\n  ");
        sb.append(DeviceOwnerGuard.promoteCommand(this));

        new android.app.AlertDialog.Builder(this)
                .setTitle("申请后端（ADB）")
                .setMessage(sb.toString())
                .setPositiveButton("自动发现", (d, w) -> {
                    toast("正在广播搜索后端…");
                    BackendClient.discover(prefs, (ok, msg) -> h.post(() -> {
                        log("发现结果：" + msg);
                        toast(msg);
                        if (ok) {
                            etHost.setText(prefs.backendHost());
                            etPort.setText(String.valueOf(prefs.backendPort()));
                        }
                    }));
                })
                .setNeutralButton("复制命令", (d, w) -> {
                    android.content.ClipboardManager cm = (android.content.ClipboardManager)
                            getSystemService(CLIPBOARD_SERVICE);
                    if (cm != null) {
                        cm.setPrimaryClip(android.content.ClipData.newPlainText(
                                "adb", DeviceOwnerGuard.promoteCommand(this)));
                        toast("命令已复制到剪贴板");
                    }
                })
                .setNegativeButton("知道了", null)
                .show();

        log("申请后端：Device Owner = " + DeviceOwnerGuard.isOwner(this));
    }

    /* ------------------------------------------------------------------ */
    /*  权限回调                                                            */
    /* ------------------------------------------------------------------ */

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == REQ_NOTIF) {
            refreshAll();
        }
    }

    /* ------------------------------------------------------------------ */
    /*  小工具                                                              */
    /* ------------------------------------------------------------------ */

    private void saveBackendSilently() {
        String host = etHost.getText().toString().trim();
        String port = etPort.getText().toString().trim();
        if (!TextUtils.isEmpty(host)) prefs.putString(Prefs.K_BACKEND_HOST, host);
        prefs.putInt(Prefs.K_BACKEND_PORT, parseInt(port, 8720));
    }

    private static int parseInt(String s, int def) {
        try { return Integer.parseInt(s.trim()); } catch (Throwable t) { return def; }
    }

    private void log(String s) {
        if (tvLog == null) return;
        String time = new java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.US)
                .format(new java.util.Date());
        String cur = tvLog.getText().toString();
        if ("（暂无）".equals(cur)) cur = "";
        String line = time + "  " + s;
        String out = line + "\n" + cur;
        if (out.length() > 4000) out = out.substring(0, 4000);
        tvLog.setText(out);
    }

    private void toast(String s) { ToastHelper.show(this, s); }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private static void setBg(View v, int color, int radiusDp) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(radiusDp);
        v.setBackground(d);
    }
}
