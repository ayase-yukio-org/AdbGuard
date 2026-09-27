package top.adbguard;

import android.Manifest;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.view.accessibility.AccessibilityManager;

import java.util.List;

/**
 * Permissions —— 引导流程要用到的所有"开关检查 + 跳转设置页"。
 *
 * 这里的每一项都对应 MainActivity 引导清单里的一步。
 * 顺序很重要：无障碍是核心，只有它开了，音量键和页面监测才成立。
 */
public final class Permissions {

    /* ---------------- ① 悬浮窗（冻结遮罩必需） ---------------- */

    public static boolean hasOverlay(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return Settings.canDrawOverlays(ctx);
        }
        return true;
    }

    public static Intent overlayIntent(Context ctx) {
        Intent i = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + ctx.getPackageName()));
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return i;
    }

    /* ---------------- ② 无障碍（核心） ---------------- */

    /** 用系统服务查，比读 Settings.Secure 更可靠。 */
    public static boolean hasAccessibility(Context ctx) {
        try {
            AccessibilityManager am =
                    (AccessibilityManager) ctx.getSystemService(Context.ACCESSIBILITY_SERVICE);
            if (am == null || !am.isEnabled()) return false;

            List<AccessibilityServiceInfo> list =
                    am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK);
            String me = new ComponentName(ctx, GuardAccessibilityService.class)
                    .flattenToString();
            String meShort = new ComponentName(ctx, GuardAccessibilityService.class)
                    .flattenToShortString();

            for (AccessibilityServiceInfo info : list) {
                if (info == null || info.getId() == null) continue;
                String id = info.getId();
                if (id.equalsIgnoreCase(me) || id.equalsIgnoreCase(meShort)
                        || id.startsWith(ctx.getPackageName() + "/")) {
                    return true;
                }
            }
        } catch (Throwable ignore) { }

        // 兜底：直接读 Settings.Secure
        try {
            String enabled = Settings.Secure.getString(
                    ctx.getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
            if (enabled == null) return false;
            String target = ctx.getPackageName() + "/";
            String shortName = ctx.getPackageName() + "/.GuardAccessibilityService";
            String longName = ctx.getPackageName() + "/top.adbguard.GuardAccessibilityService";
            return enabled.contains(shortName) || enabled.contains(longName)
                    || enabled.contains(target);
        } catch (Throwable t) {
            return false;
        }
    }

    /** 无障碍服务是否"已连接"（不只是开关打开了，是 Service 真的跑起来了）。 */
    public static boolean isAccessibilityLive() {
        return GuardAccessibilityService.get() != null;
    }

    public static Intent accessibilityIntent() {
        Intent i = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return i;
    }

    /* ---------------- ③ 通知权限（Android 13+） ---------------- */

    public static boolean hasNotification(Context ctx) {
        if (Build.VERSION.SDK_INT < 33) return true;
        return ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED;
    }

    /* -------- ③b 附近 WiFi 设备（Android 13+）--------
     * 第⑤步「连接后端」靠 UDP 广播（255.255.255.255:8721）找后端，
     * Android 13 起广播收发包需要 NEARBY_WIFI_DEVICES，不申请会静默失败：
     * 权限没给 → DatagramSocket 建得起来但广播发出去没回应 → 表现为
     * 「一直找不到后端」却不报任何错。必须和通知权限一起申请。
     */
    public static boolean hasNearbyWifi(Context ctx) {
        if (Build.VERSION.SDK_INT < 33) return true;
        return ctx.checkSelfPermission(Manifest.permission.NEARBY_WIFI_DEVICES)
                == PackageManager.PERMISSION_GRANTED;
    }

    /* ---------------- ④ 修改系统设置（关自动旋转，干扰陀螺仪） ---------------- */

    public static boolean hasWriteSettings(Context ctx) {
        return GyroGuard.canWriteSettings(ctx);
    }

    /* ---------------- ⑤ 电池优化白名单（防被系统杀后台） ---------------- */

    public static boolean isIgnoringBatteryOptimizations(Context ctx) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return true;
        try {
            android.os.PowerManager pm =
                    (android.os.PowerManager) ctx.getSystemService(Context.POWER_SERVICE);
            return pm != null && pm.isIgnoringBatteryOptimizations(ctx.getPackageName());
        } catch (Throwable t) {
            return true;
        }
    }

    public static Intent batteryIntent(Context ctx) {
        Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                Uri.parse("package:" + ctx.getPackageName()));
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return i;
    }

    private Permissions() { }
}
