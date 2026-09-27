package top.adbguard;

import android.app.admin.DeviceAdminReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * GuardDeviceAdminReceiver —— 承载"设备管理员 / Device Owner"身份的组件。
 *
 * 它自己不干活，只是让系统有个"身份标识位"可以指向我们的 App。
 * 被提升为 Device Owner 的命令（由 Python 后端执行）：
 *
 *   adb shell dpm set-device-owner top.adbguard/.GuardDeviceAdminReceiver
 *
 * ⚠️ 条件：设备上不能已有其他账号（部分机型要求"无任何已登录账号"），
 *    并且必须先安装本 APK、且不能已经激活过其他 Device Owner。
 */
public class GuardDeviceAdminReceiver extends DeviceAdminReceiver {

    private static final String TAG = "AdbGuard/Admin";

    @Override
    public void onEnabled(Context context, Intent intent) {
        super.onEnabled(context, intent);
        new Prefs(context).putBool(Prefs.K_DEVICE_OWNER, true);
        Log.i(TAG, "设备管理员已启用");
    }

    @Override
    public void onDisabled(Context context, Intent intent) {
        super.onDisabled(context, intent);
        new Prefs(context).putBool(Prefs.K_DEVICE_OWNER, false);
        Log.i(TAG, "设备管理员已停用");
    }

    @Override
    public CharSequence onDisableRequested(Context context, Intent intent) {
        return "停用后守护喵将无法再冻结/隐藏流氓应用。确定要停用吗？";
    }
}
