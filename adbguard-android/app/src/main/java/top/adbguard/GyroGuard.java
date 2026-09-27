package top.adbguard;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.TextUtils;
import android.util.Log;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/**
 * GyroGuard —— 「干扰陀螺仪」这一块的落地实现。
 *
 * ⚠️ 先把话说清楚（免得你以为 APK 能真的把物理陀螺仪关掉）：
 *   Android 从 4.0 起就没有给普通应用提供"关闭传感器"的公开 API。
 *   真正能关陀螺仪的只有三种身份：root、系统应用、或 Android 12+ 的
 *   SensorPrivacyManager（需要 MANAGE_SENSOR_PRIVACY，属于系统权限）。
 *   所以纯 APK 做不到"物理关闭"。
 *
 * 但流氓 App 用陀螺仪干什么？—— 绝大多数就是「摇一摇跳广告 / 自动跳浏览器下载」。
 * 这条链路是能被我们掐断的，做法有三层：
 *
 *   L1 关掉系统自动旋转（Setting.System.ACCELEROMETER_ROTATION = 0）
 *      —— 部分广告 SDK 的重力判定依赖旋转状态，直接降级；
 *      还能顺带防住老人手抖把屏幕转来转去。
 *
 *   L2 摇一摇跳转拦截：记录"上一个前台 App → 当前前台 App"的跳转。
 *      如果 1.2 秒内从任意 App 跳到【浏览器 / 应用商店 / 下载器】，
 *      判定为摇一摇误触，立刻 GLOBAL_ACTION_BACK 把用户拉回来。
 *
 *   L3 冻结遮罩期间，屏幕被完全盖住，广告点不到、摇不出去。
 *
 * 权限：L1 需要 WRITE_SETTINGS（"修改系统设置"），用户要在系统设置里单独授权。
 */
public final class GyroGuard {

    private static final String TAG = "AdbGuard/Gyro";

    /** 被认定为"广告落点"的包名特征 —— 跳到这些地方基本都是摇一摇误触 */
    private static final Set<String> AD_LANDING = new HashSet<>(Arrays.asList(
            // 浏览器
            "com.android.browser", "com.android.chrome", "com.tencent.mtt",
            "com.UCMobile", "com.ucmobile", "com.baidu.searchbox",
            "com.sec.android.app.sbrowser", "com.heytap.browser", "com.vivo.browser",
            "com.miui.browser", "com.huawei.browser", "org.mozilla.firefox",
            "com.quark.browser", "com.ss.android.article.news",
            // 应用商店 / 下载器
            "com.android.vending", "com.xiaomi.market", "com.huawei.appmarket",
            "com.oppo.market", "com.heytap.market", "com.bbk.appstore",
            "com.tencent.android.qqdownloader", "com.qihoo.appstore",
            "com.baidu.appsearch", "com.lenovo.leos.appstore",
            "com.android.packageinstaller"
    ));

    private static final Set<String> AD_LANDING_HINTS = new HashSet<>(Arrays.asList(
            "browser", "market", "appstore", "appstore", "download", "mtt", "ucmobile"
    ));

    /** 摇一摇判定的时间窗：这么快就跳出去，一定是被动跳的 */
    private static final long JUMP_WINDOW_MS = 1200L;

    private static String lastPkg = "";
    private static long lastPkgAt = 0L;

    /* ------------------------------------------------------------------ */
    /*  L1：自动旋转                                                        */
    /* ------------------------------------------------------------------ */

    public static boolean canWriteSettings(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return Settings.System.canWrite(ctx);
        }
        return true;   // M 以下默认有权限
    }

    public static Intent writeSettingsIntent(Context ctx) {
        Intent i = new Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS);
        i.setData(Uri.parse("package:" + ctx.getPackageName()));
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return i;
    }

    /** @param on true=允许自动旋转（恢复默认）, false=锁定为不改旋转 */
    public static boolean setAutoRotate(Context ctx, boolean on) {
        if (!canWriteSettings(ctx)) {
            Log.i(TAG, "没有 WRITE_SETTINGS 权限，跳过自动旋转设置");
            return false;
        }
        try {
            Settings.System.putInt(ctx.getContentResolver(),
                    Settings.System.ACCELEROMETER_ROTATION, on ? 1 : 0);
            Log.i(TAG, "自动旋转 -> " + on);
            return true;
        } catch (Throwable t) {
            Log.w(TAG, "设置自动旋转失败: " + t);
            return false;
        }
    }

    /* ------------------------------------------------------------------ */
    /*  L2：摇一摇跳转拦截                                                   */
    /* ------------------------------------------------------------------ */

    /** 判断一个包名是不是"广告落点"。 */
    public static boolean isAdLanding(String pkg) {
        if (TextUtils.isEmpty(pkg)) return false;
        if (AD_LANDING.contains(pkg)) return true;
        String p = pkg.toLowerCase();
        for (String h : AD_LANDING_HINTS) {
            if (p.contains(h)) return true;
        }
        return false;
    }

    /**
     * 无障碍每次窗口切换都会调这里。
     *
     * @return true 表示"疑似摇一摇误触跳转"，调用方应该立刻 BACK 拉回
     */
    public static boolean noteForeground(String pkg) {
        long now = SystemClock.uptimeMillis();
        String prev = lastPkg;
        long delta = now - lastPkgAt;

        lastPkg = pkg;
        lastPkgAt = now;

        if (TextUtils.isEmpty(pkg) || TextUtils.isEmpty(prev)) return false;
        if (pkg.equals(prev)) return false;
        if (delta > JUMP_WINDOW_MS) return false;

        boolean hit = isAdLanding(pkg);
        if (hit) {
            Log.i(TAG, "摇一摇跳转拦截: " + prev + " -> " + pkg + " (" + delta + "ms)");
        }
        return hit;
    }

    /** 用户主动切应用时重新计时，避免误判（比如自己点开浏览器）。 */
    public static void reset(String pkg) {
        lastPkg = pkg == null ? "" : pkg;
        lastPkgAt = SystemClock.uptimeMillis();
    }

    private GyroGuard() { }
}
