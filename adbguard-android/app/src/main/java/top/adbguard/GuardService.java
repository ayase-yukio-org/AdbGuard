package top.adbguard;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

/**
 * GuardService —— 常驻前台服务。
 *
 * 职责：
 *   1) 挂一个常驻通知（监护人看得见、能一键操作）
 *   2) 收到无障碍的告警后，弹出「可疑页面」高优先级通知（下拉即可看到 3 个按钮）
 *   3) 执行三个动作：关闭当前 App / 冻结触摸 / 返回桌面
 *
 * 通知栏的 3 个按钮是这套方案的"操作台"：
 * 因为冻结遮罩会盖住整个屏幕（含底下的流氓 App），
 * 但系统通知栏永远在悬浮窗之上，所以按钮始终点得到。
 */
public class GuardService extends Service {

    private static final String TAG = "AdbGuard/Svc";

    public static final String CH_GUARD = "guard_channel";
    public static final String CH_ALERT = "alert_channel";

    public static final int ID_FOREGROUND = 1001;
    public static final int ID_ALERT = 1002;

    public static final String ACTION_START   = "top.adbguard.SVC_START";
    public static final String ACTION_FREEZE  = "top.adbguard.SVC_FREEZE";
    public static final String ACTION_UNFREEZE= "top.adbguard.SVC_UNFREEZE";
    public static final String ACTION_CLOSE   = "top.adbguard.SVC_CLOSE";
    public static final String ACTION_HOME    = "top.adbguard.SVC_HOME";

    public static final String EXTRA_PKG   = "pkg";
    public static final String EXTRA_SCORE = "score";
    public static final String EXTRA_DETAIL= "detail";

    private Prefs prefs;
    private NotificationManager nm;

    /* ------------------------------------------------------------------ */
    /*  生命周期                                                           */
    /* ------------------------------------------------------------------ */

    @Override
    public void onCreate() {
        super.onCreate();
        prefs = new Prefs(this);
        nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        ensureChannels();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (action == null) action = ACTION_START;

        // 必须先 startForeground，否则 5 秒内会被 ANR 掉
        startForegroundCompat(buildForegroundNotification("后台守护中"));

        switch (action) {
            case ACTION_FREEZE:
                doFreeze("通知栏按钮");
                break;
            case ACTION_UNFREEZE:
                doUnfreeze("通知栏按钮");
                break;
            case ACTION_CLOSE:
                closeCurrent(this);
                break;
            case ACTION_HOME:
                goHome();
                break;
            case ACTION_START:
            default:
                break;
        }
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onDestroy() {
        if (prefs != null && prefs.isFrozen()) TouchBlockOverlay.hide();
        super.onDestroy();
    }

    /* ------------------------------------------------------------------ */
    /*  通道 + 通知                                                         */
    /* ------------------------------------------------------------------ */

    private void ensureChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;

        NotificationChannel guard = new NotificationChannel(
                CH_GUARD, getString(R.string.notif_channel), NotificationManager.IMPORTANCE_LOW);
        guard.setShowBadge(false);
        nm.createNotificationChannel(guard);

        NotificationChannel alert = new NotificationChannel(
                CH_ALERT, "可疑页面告警", NotificationManager.IMPORTANCE_HIGH);
        alert.setShowBadge(true);
        alert.enableVibration(true);
        nm.createNotificationChannel(alert);
    }

    private void startForegroundCompat(Notification n) {
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(ID_FOREGROUND, n,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(ID_FOREGROUND, n);
        }
    }

    private PendingIntent svc(String action) {
        Intent i = new Intent(this, GuardService.class).setAction(action);
        int flag = Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                ? PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
                : PendingIntent.FLAG_UPDATE_CURRENT;
        return PendingIntent.getService(this, action.hashCode(), i, flag);
    }

    private Notification.Builder baseBuilder(String channel) {
        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            b = new Notification.Builder(this, channel);
        } else {
            b = new Notification.Builder(this);
        }
        b.setSmallIcon(R.drawable.ic_guard);
        return b;
    }

    /** 常驻通知：三个操作按钮就在这里。 */
    private Notification buildForegroundNotification(String text) {
        boolean frozen = prefs != null && prefs.isFrozen();

        Notification.Builder b = baseBuilder(CH_GUARD)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true);

        // ① 关闭当前 App
        b.addAction(new Notification.Action.Builder(null,
                getString(R.string.act_close), svc(ACTION_CLOSE)).build());
        // ② 禁用陀螺仪和触摸（冻结）
        if (frozen) {
            b.addAction(new Notification.Action.Builder(null,
                    getString(R.string.act_unfreeze), svc(ACTION_UNFREEZE)).build());
        } else {
            b.addAction(new Notification.Action.Builder(null,
                    getString(R.string.act_freeze), svc(ACTION_FREEZE)).build());
        }
        // ③ 返回桌面
        b.addAction(new Notification.Action.Builder(null,
                getString(R.string.act_home), svc(ACTION_HOME)).build());

        Intent open = new Intent(this, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        int flag = Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                ? PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
                : PendingIntent.FLAG_UPDATE_CURRENT;
        b.setContentIntent(PendingIntent.getActivity(this, 0, open, flag));
        return b.build();
    }

    private void refreshForeground(String text) {
        try {
            startForegroundCompat(buildForegroundNotification(text));
        } catch (Throwable t) {
            Log.w(TAG, "刷新通知失败: " + t);
        }
    }

    /* ------------------------------------------------------------------ */
    /*  静态动作（供无障碍 / Activity / Receiver 调用）                       */
    /* ------------------------------------------------------------------ */

    public static void start(Context ctx) {
        Intent i = new Intent(ctx, GuardService.class).setAction(ACTION_START);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            ctx.startForegroundService(i);
        } else {
            ctx.startService(i);
        }
    }

    public static void freeze(Context ctx, String reason) {
        Intent i = new Intent(ctx, GuardService.class).setAction(ACTION_FREEZE);
        i.putExtra(EXTRA_DETAIL, reason);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(i);
        else ctx.startService(i);
    }

    public static void unfreeze(Context ctx, String reason) {
        Intent i = new Intent(ctx, GuardService.class).setAction(ACTION_UNFREEZE);
        i.putExtra(EXTRA_DETAIL, reason);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(i);
        else ctx.startService(i);
    }

    /* ------------------------------------------------------------------ */

    private void doFreeze(String reason) {
        Prefs p = new Prefs(this);
        if (p.isFrozen()) { refreshForeground("已处于冻结状态"); return; }

        p.setFrozen(true);

        // 顺带把自动旋转关掉，减少"摇一摇/重力跳转"类广告被触发的概率
        GyroGuard.setAutoRotate(this, false);

        TouchBlockOverlay.show(this, getString(R.string.overlay_hint));
        refreshForeground("已冻结 ✓ 连按 3 次音量减解除");

        Log.i(TAG, "冻结： " + reason);
    }

    private void doUnfreeze(String reason) {
        Prefs p = new Prefs(this);
        p.setFrozen(false);
        TouchBlockOverlay.hide();
        GyroGuard.setAutoRotate(this, true);
        refreshForeground("已解除冻结");
        Log.i(TAG, "解冻： " + reason);
    }

    private void goHome() {
        GuardAccessibilityService a11y = GuardAccessibilityService.get();
        if (a11y != null) a11y.goHome();
    }

    /* ------------------------------------------------------------------ */
    /*  「强杀」：普通 App 杀不了别人，所以走 详情页 → 强行停止 这条路           */
    /* ------------------------------------------------------------------ */

    /**
     * 关闭当前 App。
     *
     * 三条路，按优先级：
     *  A. 本机已被后端提升为 Device Owner → setPackagesSuspended + setApplicationHidden，
     *     真正意义上的"杀掉并藏起来"，而且能同时撤销它偷通讯录/相册的权限。
     *  B. 有后端 ADB 直连 → 交给后端 `am force-stop`（后端那侧做，见 Python）。
     *  C. 纯手机端（默认）→ 无障碍连按返回 + 回桌面，然后直接跳到
     *     「设置 → 应用 → <应用名>」的详细界面，并自动帮你点掉「强行停止」。
     */
    public static void closeCurrent(Context ctx) {
        GuardAccessibilityService a11y = GuardAccessibilityService.get();
        String pkg = a11y != null ? a11y.currentPackage() : "";
        Prefs p = new Prefs(ctx);

        // A. Device Owner 路线
        if (DeviceOwnerGuard.isOwner(ctx) && !pkg.isEmpty() && !pkg.equals(ctx.getPackageName())) {
            boolean ok = DeviceOwnerGuard.quarantine(ctx, pkg);
            if (ok) {
                if (a11y != null) a11y.goHome();
                ToastHelper.show(ctx, "已挂起并隐藏：" + pkg);
                p.addBlockedPkg(pkg);
                return;
            }
        }

        // B. 有后端就让后端去 force-stop（不等结果，异步）
        BackendClient.requestForceStop(p, pkg);

        // C. 兜底：退出 → 回桌面 → 跳应用详情页 → 自动点「强行停止」
        if (a11y != null) {
            for (int i = 0; i < 3; i++) a11y.back();
            a11y.goHome();
        }
        if (!pkg.isEmpty() && !pkg.equals(ctx.getPackageName())) {
            AppDetailsOpener.open(ctx, pkg, true);
            p.addBlockedPkg(pkg);
        } else {
            ToastHelper.show(ctx, "没识别到前台应用，已返回桌面");
        }
    }

    /* ------------------------------------------------------------------ */
    /*  告警                                                               */
    /* ------------------------------------------------------------------ */

    /** 无障碍发现可疑页面后调用。 */
    public static void alertSuspicious(Context ctx, String pkg, UiHeuristics.Result r) {
        Prefs p = new Prefs(ctx);
        if (p.isBlocked(pkg)) return;          // 已经处置过的不再反复打扰

        NotificationManager nm =
                (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;

        Intent i = new Intent(ctx, GuardService.class).setAction(ACTION_START);
        i.putExtra(EXTRA_PKG, pkg).putExtra(EXTRA_SCORE, r.score).putExtra(EXTRA_DETAIL, r.toString());
        int flag = Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                ? PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
                : PendingIntent.FLAG_UPDATE_CURRENT;
        PendingIntent pi = PendingIntent.getService(ctx, 42, i, flag);

        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            b = new Notification.Builder(ctx, CH_ALERT);
        } else {
            b = new Notification.Builder(ctx);
        }

        String title = "检测到可疑页面";
        String text = shortPkg(pkg) + " · " + r.score + "分 · " + firstReason(r);
        b.setSmallIcon(R.drawable.ic_guard)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text + "\n" + r.toString()))
                .setAutoCancel(false)
                .setOngoing(false)
                .setPriority(Notification.PRIORITY_HIGH)
                .setDefaults(Notification.DEFAULT_VIBRATE)
                .setContentIntent(pi);

        b.addAction(new Notification.Action.Builder(null,
                ctx.getString(R.string.act_close), svcStatic(ctx, ACTION_CLOSE)).build());
        b.addAction(new Notification.Action.Builder(null,
                ctx.getString(R.string.act_freeze), svcStatic(ctx, ACTION_FREEZE)).build());
        b.addAction(new Notification.Action.Builder(null,
                ctx.getString(R.string.act_home), svcStatic(ctx, ACTION_HOME)).build());

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            b.setVisibility(Notification.VISIBILITY_PUBLIC);
        }
        nm.notify(ID_ALERT, b.build());
    }

    private static PendingIntent svcStatic(Context ctx, String action) {
        Intent i = new Intent(ctx, GuardService.class).setAction(action);
        int flag = Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                ? PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
                : PendingIntent.FLAG_UPDATE_CURRENT;
        return PendingIntent.getService(ctx, action.hashCode(), i, flag);
    }

    private static String firstReason(UiHeuristics.Result r) {
        return r.reasons.isEmpty() ? "疑似诱导页面" : r.reasons.get(0);
    }

    private static String shortPkg(String pkg) {
        if (pkg == null || pkg.isEmpty()) return "未知应用";
        int dot = pkg.lastIndexOf('.');
        return dot > 0 ? pkg.substring(dot + 1) : pkg;
    }
}
