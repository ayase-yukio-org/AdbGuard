package top.adbguard;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.content.Intent;
import android.os.Build;
import android.os.SystemClock;
import android.text.TextUtils;
import android.util.Log;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.ArrayList;
import java.util.List;

/**
 * GuardAccessibilityService —— 整套方案的核心。
 *
 * 它有且仅有四个职责（全部依赖无障碍开启）：
 *
 *   A. 页面监测：监听窗口变化 → 抓节点树 → UiHeuristics 打分 → 命中就告警
 *   B. 音量键：拦截「音量减」，连按 3 次 → 解除冻结
 *      （普通 App 拿不到物理音量键，这是无障碍独有的能力）
 *   C. 摇一摇反制（"干扰陀螺仪"的落地）：1.2 秒内从任意 App 跳到浏览器/商店
 *      → 判定误触 → 立刻 BACK 拉回
 *   D. 替用户点一下：跳到「设置→应用→应用详情」后自动点「强行停止」
 *
 * 隐私声明：所有打分都在本机完成，节点文本不会离开手机；
 * 只有命中的可疑包名会上报给用户自己部署的后端。
 */
public class GuardAccessibilityService extends AccessibilityService {

    private static final String TAG = "AdbGuard/A11y";

    /** 同进程单例，供 MainActivity / GuardService 调用 */
    private static volatile GuardAccessibilityService sInstance;

    /** 连按音量减的判定窗口 */
    private static final long VOL_WINDOW_MS = 1600L;
    private static final int VOL_TARGET = 3;

    /** 分析节流，避免无障碍线程被刷爆 */
    private static final long ANALYZE_THROTTLE_MS = 700L;

    /* ---- 自动点击（跳应用详情页后帮用户点"强行停止"） ---- */
    private static volatile String[] sAutoClickLabels = null;
    private static volatile long sAutoClickDeadline = 0L;

    private long lastVolDownAt = 0L;
    private int volDownCount = 0;

    private Prefs prefs;
    private String selfPkg;

    private volatile UiHeuristics.Result lastResult;
    private long lastAnalyzeAt = 0L;

    /* ------------------------------------------------------------------ */
    /*  生命周期                                                           */
    /* ------------------------------------------------------------------ */

    public static GuardAccessibilityService get() { return sInstance; }

    @Override
    public void onServiceConnected() {
        super.onServiceConnected();
        sInstance = this;
        prefs = new Prefs(this);
        selfPkg = getPackageName();

        try {
            AccessibilityServiceInfo info = getServiceInfo();
            if (info != null) {
                // 关键：FLAG_REQUEST_FILTER_KEY_EVENTS 是拿到音量键的前提
                info.flags |= AccessibilityServiceInfo.FLAG_REQUEST_FILTER_KEY_EVENTS
                        | AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                        | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                    info.flags |= AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS;
                }
                info.notificationTimeout = 120;
                setServiceInfo(info);
            }
        } catch (Throwable t) {
            Log.w(TAG, "setServiceInfo 失败: " + t);
        }

        // 用户开了"陀螺仪干扰"的话，无障碍一接上就把自动旋转关掉
        if (prefs.getBool(Prefs.K_GYRO_SHIELD, true)) {
            GyroGuard.setAutoRotate(this, false);
        }

        // 拉起守护服务，让通知栏的操作台就位
        GuardService.start(this);
        Log.i(TAG, "无障碍服务已连接 OK");
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) return;

        // ---- C. 摇一摇反制：抢在打分前面处理 ----
        if (event.getEventType() == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            String fp = pkgOf(event);
            if (!TextUtils.isEmpty(fp) && !fp.equals(selfPkg)
                    && !fp.startsWith("com.android.systemui")) {
                if (GyroGuard.noteForeground(fp)) {
                    Log.i(TAG, "疑似摇一摇误触跳转，拉回上一页");
                    performGlobalAction(GLOBAL_ACTION_BACK);
                    ToastHelper.show(this, "已拦下摇一摇跳转");
                    return;
                }
            }
        }

        // ---- D. 到了设置页就帮用户点"强行停止" ----
        if (sAutoClickLabels != null) {
            if (SystemClock.uptimeMillis() > sAutoClickDeadline) {
                sAutoClickLabels = null;
            } else if (event.getEventType() == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                    || event.getEventType() == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) {
                tryAutoClick(getRootInActiveWindow());
            }
        }

        // ---- A. 页面打分 ----
        int type = event.getEventType();
        if (type != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                && type != AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) {
            return;
        }
        long now = SystemClock.uptimeMillis();
        if (now - lastAnalyzeAt < ANALYZE_THROTTLE_MS) return;
        lastAnalyzeAt = now;

        if (prefs != null && prefs.isFrozen()) return;   // 冻结中不必重复告警

        try {
            analyzeCurrentWindow(pkgOf(event));
        } catch (Throwable t) {
            Log.w(TAG, "分析失败: " + t);
        }
    }

    private String pkgOf(AccessibilityEvent e) {
        if (e.getPackageName() == null) return "";
        return e.getPackageName().toString();
    }

    /* ------------------------------------------------------------------ */
    /*  A. 页面打分                                                        */
    /* ------------------------------------------------------------------ */

    private void analyzeCurrentWindow(String eventPkg) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return;

        CharSequence pkgCs = root.getPackageName();
        String pkg = pkgCs == null ? "" : pkgCs.toString();
        if (pkg.isEmpty()) pkg = eventPkg;
        if (TextUtils.isEmpty(pkg)) return;
        if (pkg.equals(selfPkg)) return;
        if (pkg.startsWith("com.android.systemui")) return;
        // 系统设置页面天然"很空"，容易误判，直接跳过
        if (pkg.startsWith("com.android.settings")) return;

        List<UiHeuristics.Node> nodes = new ArrayList<>();
        flatten(root, nodes, 0);

        int w = getResources().getDisplayMetrics().widthPixels;
        int h = getResources().getDisplayMetrics().heightPixels;

        UiHeuristics.Result r = UiHeuristics.score(nodes, w, h, pkg);
        lastResult = r;

        if (r.shouldAct()) {
            Log.i(TAG, "命中可疑页面 " + pkg + " -> " + r);
            GuardService.alertSuspicious(this, pkg, r);
        }
    }

    private void flatten(AccessibilityNodeInfo n, List<UiHeuristics.Node> out, int depth) {
        if (n == null || depth > 40) return;

        int count = 0;
        try { count = n.getChildCount(); } catch (Throwable ignore) { }

        boolean visible;
        try { visible = n.isVisibleToUser(); } catch (Throwable t) { visible = true; }

        boolean hasText = n.getText() != null || n.getContentDescription() != null;
        boolean interactive = n.isClickable() || n.isScrollable();

        if (visible && (hasText || interactive)) {
            UiHeuristics.Node t = new UiHeuristics.Node();
            CharSequence txt = n.getText();
            if (txt == null || txt.length() == 0) txt = n.getContentDescription();
            t.text = txt == null ? "" : txt.toString().trim();
            CharSequence cn = n.getClassName();
            t.cls = cn == null ? "" : cn.toString();
            CharSequence pn = n.getPackageName();
            t.pkg = pn == null ? "" : pn.toString();
            t.clickable = n.isClickable();
            t.scrollable = n.isScrollable();

            android.graphics.Rect rc = new android.graphics.Rect();
            n.getBoundsInScreen(rc);
            t.x1 = rc.left; t.y1 = rc.top; t.x2 = rc.right; t.y2 = rc.bottom;

            if (t.area() > 0) out.add(t);
        }

        for (int i = 0; i < count; i++) {
            AccessibilityNodeInfo c = null;
            try { c = n.getChild(i); } catch (Throwable ignore) { }
            if (c != null) flatten(c, out, depth + 1);
        }
    }

    /* ------------------------------------------------------------------ */
    /*  B. 音量键：连按 3 次解除冻结                                          */
    /* ------------------------------------------------------------------ */

    @Override
    protected boolean onKeyEvent(KeyEvent event) {
        if (event == null) return false;
        if (event.getKeyCode() != KeyEvent.KEYCODE_VOLUME_DOWN) return false;
        if (event.getAction() != KeyEvent.ACTION_DOWN) return false;
        if (event.getRepeatCount() > 0) return false;

        long now = SystemClock.uptimeMillis();
        if (now - lastVolDownAt > VOL_WINDOW_MS) volDownCount = 0;
        lastVolDownAt = now;
        volDownCount++;

        if (prefs != null && prefs.isFrozen()) {
            ToastHelper.show(this, "解除进度 " + volDownCount + "/" + VOL_TARGET);
            if (volDownCount >= VOL_TARGET) {
                volDownCount = 0;
                GuardService.unfreeze(this, "音量键 x3");
            }
            return true;    // 消费掉，别让音量条乱跳
        }
        return false;       // 未冻结时正常放行
    }

    /* ------------------------------------------------------------------ */
    /*  D. 自动点"强行停止"                                                 */
    /* ------------------------------------------------------------------ */

    /** 由 AppDetailsOpener 安排一次限时自动点击。 */
    public static void scheduleAutoClick(String[] labels, long ttlMs) {
        sAutoClickLabels = labels;
        sAutoClickDeadline = SystemClock.uptimeMillis() + ttlMs;
    }

    public static void cancelAutoClick() {
        sAutoClickLabels = null;
    }

    public static boolean hasPendingAutoClick() {
        return sAutoClickLabels != null && SystemClock.uptimeMillis() <= sAutoClickDeadline;
    }

    private void tryAutoClick(AccessibilityNodeInfo root) {
        String[] labels = sAutoClickLabels;
        if (root == null || labels == null) return;

        CharSequence pk = root.getPackageName();
        String pkg = pk == null ? "" : pk.toString();
        // 只在系统设置里动作，绝不误点别的 App
        if (!pkg.startsWith("com.android.settings")) return;

        for (final String label : labels) {
            AccessibilityNodeInfo target = findText(root, label, 0);
            if (target != null) {
                AccessibilityNodeInfo clickable = ascendToClickable(target);
                if (clickable != null
                        && clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                    Log.i(TAG, "已自动点击「" + label + "」");
                    ToastHelper.show(this, "已点击「" + label + "」");
                    sAutoClickLabels = null;
                    return;
                }
            }
        }
    }

    private AccessibilityNodeInfo findText(AccessibilityNodeInfo n, String label, int depth) {
        if (n == null || depth > 30) return null;
        CharSequence t = n.getText();
        if (t != null && t.length() > 0) {
            String s = t.toString().trim();
            if (s.equals(label) || s.contains(label)) return n;
        }
        int c = 0;
        try { c = n.getChildCount(); } catch (Throwable ignore) { }
        for (int i = 0; i < c; i++) {
            AccessibilityNodeInfo ch = null;
            try { ch = n.getChild(i); } catch (Throwable ignore) { }
            AccessibilityNodeInfo r = findText(ch, label, depth + 1);
            if (r != null) return r;
        }
        return null;
    }

    /** 往上找第一个可点击的祖先（设置页里的按钮经常包了一层容器）。 */
    private AccessibilityNodeInfo ascendToClickable(AccessibilityNodeInfo n) {
        AccessibilityNodeInfo cur = n;
        int guard = 0;
        while (cur != null && guard++ < 8) {
            if (cur.isClickable()) return cur;
            cur = cur.getParent();
        }
        return n;
    }

    /* ------------------------------------------------------------------ */
    /*  对外小工具                                                          */
    /* ------------------------------------------------------------------ */

    public void goHome() { performGlobalAction(GLOBAL_ACTION_HOME); }

    public void back() { performGlobalAction(GLOBAL_ACTION_BACK); }

    public void recents() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            performGlobalAction(GLOBAL_ACTION_RECENTS);
        }
    }

    /** 主动触发一次打分（界面"D 立即检查"用）。 */
    public UiHeuristics.Result runCheckNow() {
        analyzeCurrentWindow("");
        return lastResult;
    }

    public UiHeuristics.Result getLastResult() { return lastResult; }

    /** 当前前台包名。 */
    public String currentPackage() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return "";
        CharSequence p = root.getPackageName();
        return p == null ? "" : p.toString();
    }

    @Override
    public void onInterrupt() { }

    @Override
    public boolean onUnbind(Intent intent) {
        sInstance = null;
        Log.i(TAG, "无障碍服务已断开");
        return super.onUnbind(intent);
    }
}
