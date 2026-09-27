package top.adbguard;

import android.app.admin.DevicePolicyManager;
import android.content.ComponentName;
import android.content.Context;
import android.os.Build;
import android.util.Log;

/**
 * DeviceOwnerGuard —— 有后端 ADB 时才能解锁的"真·强杀"能力。
 *
 * 为什么非要 Device Owner？
 *   普通 App / 设备管理员（Device Admin）都杀不了别人的进程、藏不了别人的图标。
 *   只有被 `adb shell dpm set-device-owner` 提升成 Device Owner 之后，
 *   才能调用：
 *     · setPackagesSuspended(pkg, true)      冻结目标应用（图标变灰、点不动）
 *     · setApplicationHidden(pkg, true)      直接从桌面/抽屉里藏掉
 *     · setPermissionGrantState(pkg, perm, DENIED)  撤销通讯录/相册/存储授权
 *   这三件事合起来，就是用户说的"让它再也弹不出来、也偷不了东西"。
 *
 * 本类不申请 wipe-data 之类的危险策略，避免误伤。
 */
public final class DeviceOwnerGuard {

    private static final String TAG = "AdbGuard/DO";

    /** 建议撤销的危险权限 */
    private static final String[] DANGEROUS_PERMS = {
            "android.permission.READ_CONTACTS",
            "android.permission.WRITE_CONTACTS",
            "android.permission.READ_CALL_LOG",
            "android.permission.READ_SMS",
            "android.permission.RECEIVE_SMS",
            "android.permission.READ_EXTERNAL_STORAGE",
            "android.permission.WRITE_EXTERNAL_STORAGE",
            "android.permission.READ_PHONE_STATE",
            "android.permission.ACCESS_FINE_LOCATION",
            "android.permission.ACCESS_COARSE_LOCATION",
            "android.permission.CAMERA",
            "android.permission.RECORD_AUDIO",
    };

    /* ------------------------------------------------------------------ */

    public static DevicePolicyManager dpm(Context ctx) {
        return (DevicePolicyManager) ctx.getSystemService(Context.DEVICE_POLICY_SERVICE);
    }

    public static ComponentName admin(Context ctx) {
        return new ComponentName(ctx, GuardDeviceAdminReceiver.class);
    }

    /** 当前这个 App 是不是 Device Owner。 */
    public static boolean isOwner(Context ctx) {
        try {
            DevicePolicyManager d = dpm(ctx);
            return d != null && d.isDeviceOwnerApp(ctx.getPackageName());
        } catch (Throwable t) {
            return false;
        }
    }

    /** 是不是至少是激活状态的 Device Admin（层级低一些）。 */
    public static boolean isAdminActive(Context ctx) {
        try {
            DevicePolicyManager d = dpm(ctx);
            return d != null && d.isAdminActive(admin(ctx));
        } catch (Throwable t) {
            return false;
        }
    }

    /** 给用户/后端看的提升命令。 */
    public static String promoteCommand(Context ctx) {
        return "adb shell dpm set-device-owner "
                + ctx.getPackageName() + "/.GuardDeviceAdminReceiver";
    }

    /**
     * 主动放弃 Device Owner 身份 + 撤销设备管理员。
     *
     * 为什么必须由 App 自己来调：
     *   Device Owner 是"粘性"的。`dpm` 的官方帮助写明 remove-active-admin
     *   只对**声明了 testOnly 的 admin** 生效；普通手段（系统设置里、纯 adb）
     *   都拔不掉。唯一可靠的正路就是 App 内调用本方法，把自己降回普通应用 ——
     *   之后才能在设置里正常卸载。
     *
     * @return true 表示至少完成了一步（DO 身份已放弃 或 管理员已撤销）
     */
    public static boolean releaseOwnership(Context ctx) {
        DevicePolicyManager d = dpm(ctx);
        if (d == null) return false;
        boolean ok = false;

        // ① 放弃 Device Owner 身份
        //    （clearDeviceOwnerApp 自 API 26 起标记废弃，但至今仍是唯一可行路径）
        try {
            if (d.isDeviceOwnerApp(ctx.getPackageName())) {
                d.clearDeviceOwnerApp(ctx.getPackageName());
                ok = true;
                Log.i(TAG, "已放弃 Device Owner 身份");
            }
        } catch (Throwable t) {
            Log.w(TAG, "clearDeviceOwnerApp 异常: " + t);
        }

        // ② 撤销设备管理员，否则设置里仍显示"已激活"
        try {
            if (d.isAdminActive(admin(ctx))) {
                d.removeActiveAdmin(admin(ctx));
                ok = true;
                Log.i(TAG, "已撤销设备管理员");
            }
        } catch (Throwable t) {
            Log.w(TAG, "removeActiveAdmin 异常: " + t);
        }

        return ok;
    }

    /** 退出 Device Owner 时给用户/后端看的命令（需本 APK 声明 testOnly 才有效）。 */
    public static String demoteCommand(Context ctx) {
        return "adb shell dpm remove-active-admin "
                + ctx.getPackageName() + "/.GuardDeviceAdminReceiver";
    }

    /* ------------------------------------------------------------------ */
    /*  核心：隔离一个流氓 App                                              */
    /* ------------------------------------------------------------------ */

    /**
     * 把目标应用"挂起 + 隐藏 + 撤权限"。
     * @return true 表示至少成功执行了挂起或隐藏
     */
    public static boolean quarantine(Context ctx, String pkg) {
        if (!isOwner(ctx) || pkg == null || pkg.isEmpty()) return false;
        DevicePolicyManager d = dpm(ctx);
        if (d == null) return false;

        boolean ok = false;

        // ① 挂起：图标灰掉、通知静音、后台被限制
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            try {
                String[] failed = d.setPackagesSuspended(admin(ctx), new String[]{pkg}, true);
                if (failed == null || failed.length == 0) {
                    ok = true;
                    Log.i(TAG, "已挂起: " + pkg);
                } else {
                    Log.w(TAG, "挂起失败: " + pkg);
                }
            } catch (Throwable t) {
                Log.w(TAG, "setPackagesSuspended 异常: " + t);
            }
        }

        // ② 隐藏：从桌面/抽屉消失
        try {
            if (d.setApplicationHidden(admin(ctx), pkg, true)) {
                ok = true;
                Log.i(TAG, "已隐藏: " + pkg);
            }
        } catch (Throwable t) {
            Log.w(TAG, "setApplicationHidden 异常: " + t);
        }

        // ③ 撤掉危险权限（通讯录/相册/存储…）
        revokeDangerousPermissions(ctx, pkg);

        return ok;
    }

    /** 解除隔离（误伤了可以恢复）。 */
    public static boolean release(Context ctx, String pkg) {
        if (!isOwner(ctx)) return false;
        DevicePolicyManager d = dpm(ctx);
        if (d == null) return false;
        boolean ok = false;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                d.setPackagesSuspended(admin(ctx), new String[]{pkg}, false);
                ok = true;
            }
        } catch (Throwable ignore) { }
        try {
            if (d.setApplicationHidden(admin(ctx), pkg, false)) ok = true;
        } catch (Throwable ignore) { }
        return ok;
    }

    /** 撤销该应用的通讯录/相册/存储/定位等危险权限。 */
    public static int revokeDangerousPermissions(Context ctx, String pkg) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return 0;
        DevicePolicyManager d = dpm(ctx);
        if (d == null || !isOwner(ctx)) return 0;

        int n = 0;
        for (String perm : DANGEROUS_PERMS) {
            try {
                boolean done = d.setPermissionGrantState(admin(ctx), pkg, perm,
                        DevicePolicyManager.PERMISSION_GRANT_STATE_DENIED);
                if (done) n++;
            } catch (Throwable ignore) { }
        }
        // Android 13+ 的通知权限也顺手关掉，减少弹窗骚扰
        if (Build.VERSION.SDK_INT >= 33) {
            try {
                d.setPermissionGrantState(admin(ctx), pkg,
                        "android.permission.POST_NOTIFICATIONS",
                        DevicePolicyManager.PERMISSION_GRANT_STATE_DENIED);
                n++;
            } catch (Throwable ignore) { }
        }
        Log.i(TAG, "撤销权限数量: " + n + " @" + pkg);
        return n;
    }

    /** 关闭目标应用的悬浮窗权限（弹窗的根子）。DO 下可以用 appops 之外的策略。 */
    public static boolean isSuspended(Context ctx, String pkg) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false;
        try {
            DevicePolicyManager d = dpm(ctx);
            return d != null && d.isPackageSuspended(admin(ctx), pkg);
        } catch (Throwable t) {
            return false;
        }
    }

    private DeviceOwnerGuard() { }
}
