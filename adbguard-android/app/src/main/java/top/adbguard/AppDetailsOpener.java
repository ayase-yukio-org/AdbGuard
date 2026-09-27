package top.adbguard;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.util.Log;

/**
 * AppDetailsOpener —— 「APK 强杀」的落地方式。
 *
 * 普通应用没有 KILL_BACKGROUND_PROCESSES 之外的任何跨进程杀进程能力，
 * am force-stop 只有 shell / root / Device Owner 能干。
 *
 * 所以纯手机端走的是"曲线救国"：
 *   1) 先退回桌面，让流氓页面的 Activity 全部进入后台
 *   2) 打开系统「设置 → 应用 → <应用名>」的详细信息页
 *      （Intent: android.settings.APPLICATION_DETAILS_SETTINGS + package:<pkg>）
 *   3) 通过无障碍服务在这页里自动找到并点击「强行停止」
 *      —— 这一步等同于用户在设置里手动强杀，是系统认可的路径，不需要任何特殊权限
 *
 * 自动点击是"限时 + 限目标"的：只在 10 秒内、只在设置包里、
 * 只点白名单里的文案（强行停止 / 停用），绝不会去点「卸载」或「确定」这类
 * 可能造成不可逆后果的按钮 —— 卸载留给用户自己决定。
 */
public final class AppDetailsOpener {

    private static final String TAG = "AdbGuard/Details";

    /** 允许自动点击的文案（按优先级）。刻意不含"卸载""确定"。 */
    public static final String[] AUTO_CLICK_WHITELIST = {
            "强行停止", "强制停止", "停止", "停用", "禁用"
    };

    /** 自动点击的有效期 */
    public static final long AUTO_CLICK_TTL_MS = 10_000L;

    /** 打开应用详情页。@param autoStop 是否安排自动点"强行停止" */
    public static void open(Context ctx, String pkg, boolean autoStop) {
        if (pkg == null || pkg.isEmpty()) return;

        Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
        i.setData(Uri.parse("package:" + pkg));
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                | Intent.FLAG_ACTIVITY_CLEAR_TOP
                | Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS);
        try {
            ctx.startActivity(i);
            Log.i(TAG, "打开应用详情页: " + pkg);
        } catch (Throwable t) {
            Log.w(TAG, "详情页打不开，退化到应用列表: " + t);
            try {
                Intent fb = new Intent(Settings.ACTION_APPLICATION_SETTINGS)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                ctx.startActivity(fb);
            } catch (Throwable t2) {
                ToastHelper.show(ctx, "打不开系统设置，请手动：设置 → 应用 → " + pkg);
                return;
            }
        }

        if (autoStop) {
            GuardAccessibilityService.scheduleAutoClick(AUTO_CLICK_WHITELIST, AUTO_CLICK_TTL_MS);
        }
    }

    /** 打开"卸载"页（给用户自己确认，我们只负责跳过去） */
    public static void openUninstall(Context ctx, String pkg) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            try {
                Intent i = new Intent(Intent.ACTION_DELETE, Uri.parse("package:" + pkg));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                ctx.startActivity(i);
                return;
            } catch (Throwable ignore) { }
        }
        open(ctx, pkg, false);
    }

    /** 打开应用权限页（顺手关掉通讯录/相册/存储授权） */
    public static void openPermissions(Context ctx, String pkg) {
        try {
            Intent i = new Intent("android.settings.APPLICATION_DETAILS_SETTINGS")
                    .setData(Uri.parse("package:" + pkg))
                    .putExtra(":settings:fragment_args_key", "permissions")
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(i);
        } catch (Throwable t) {
            open(ctx, pkg, false);
        }
    }

    /** 打开悬浮窗/特殊权限汇总页 */
    public static void openSpecialAccess(Context ctx) {
        try {
            Intent i = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(i);
        } catch (Throwable t) {
            ToastHelper.show(ctx, "请手动进入：设置 → 应用 → 特殊应用权限");
        }
    }

    private AppDetailsOpener() { }
}
