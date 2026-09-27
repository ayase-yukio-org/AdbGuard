package top.adbguard;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** 通知栏按钮的广播接收器（也用于从外部测试三个动作）。 */
public class GuardActionReceiver extends BroadcastReceiver {

    public static final String ACTION_CLOSE_CURRENT = "top.adbguard.ACTION_CLOSE_CURRENT";
    public static final String ACTION_FREEZE        = "top.adbguard.ACTION_FREEZE";
    public static final String ACTION_HOME          = "top.adbguard.ACTION_HOME";
    public static final String ACTION_UNFREEZE      = "top.adbguard.ACTION_UNFREEZE";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) return;
        String a = intent.getAction();
        Context app = context.getApplicationContext();

        switch (a) {
            case ACTION_CLOSE_CURRENT:
                GuardService.closeCurrent(app);
                break;
            case ACTION_FREEZE:
                GuardService.freeze(app, "广播");
                break;
            case ACTION_HOME:
                GuardAccessibilityService s = GuardAccessibilityService.get();
                if (s != null) s.goHome();
                else ToastHelper.show(app, "请先开启无障碍服务");
                break;
            case ACTION_UNFREEZE:
                GuardService.unfreeze(app, "广播");
                break;
            default:
                break;
        }
    }
}
