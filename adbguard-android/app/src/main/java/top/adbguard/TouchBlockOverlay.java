package top.adbguard;

import android.content.Context;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.provider.Settings;
import android.util.DisplayMetrics;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * TouchBlockOverlay —— 「冻结」的真正实现。
 *
 * 原理：在 WindowManager 最上层铺一张 TYPE_APPLICATION_OVERLAY 的全屏窗，
 * 它会被系统放在所有普通应用窗口之上（但低于状态栏/通知栏），
 * 于是：
 *   · 底下那个流氓 App 一点都点不到 → 15 个假关闭按钮全部失效
 *   · 通知栏仍然可以下拉 → 我们的 3 个按钮可用
 *   · 音量键被无障碍服务拦截 → 连按 3 次解除
 *
 * ⚠️ 诚实说明：无 root / 非 Device Owner 的情况下，Android 不提供"关闭物理陀螺仪"的公开 API。
 * 这里能做的是把 UI 层全部封死，让依赖"摇一摇跳转广告"的流氓 App 无法被交互；
 * 真正要关传感器需要后端走 adb（Android 12+ 的 sensor_privacy）。
 */
public final class TouchBlockOverlay {

    private static WindowManager wm;
    private static View overlayView;
    private static boolean showing = false;

    public static boolean isShowing() { return showing; }

    /** 申请悬浮窗权限入口（给 MainActivity 用） */
    public static boolean canOverlay(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return Settings.canDrawOverlays(ctx);
        }
        return true;
    }

    public static void show(Context ctx, String hint) {
        if (showing) return;
        Context app = ctx.getApplicationContext();
        wm = (WindowManager) app.getSystemService(Context.WINDOW_SERVICE);
        if (wm == null) return;

        DisplayMetrics dm = app.getResources().getDisplayMetrics();
        int w = dm.widthPixels;
        int h = dm.heightPixels;

        LinearLayout root = new LinearLayout(app);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(0xCC0B1220);   // 半透明深色蒙层

        // 让它必然吃触摸：不设 FLAG_NOT_TOUCHABLE
        // 同时设成 focusable，确保键盘/触摸都不会穿透
        root.setFocusable(true);
        root.setClickable(true);
        root.setFocusableInTouchMode(true);

        TextView title = new TextView(app);
        title.setText("屏幕已冻结");
        title.setTextColor(Color.WHITE);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 26);
        title.setGravity(Gravity.CENTER);
        title.setPadding(0, 0, 0, dp(app, 12));
        root.addView(title);

        TextView sub = new TextView(app);
        sub.setText(hint);
        sub.setTextColor(0xFFB9C6DD);
        sub.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        sub.setGravity(Gravity.CENTER);
        sub.setPadding(dp(app, 32), 0, dp(app, 32), dp(app, 24));
        root.addView(sub);

        Button unlock = new Button(app);
        unlock.setText("立即解除（也可连按 3 次音量减）");
        unlock.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(0xFF2F6FED);
        bg.setCornerRadius(dp(app, 12));
        unlock.setBackground(bg);
        unlock.setTextColor(Color.WHITE);
        unlock.setPadding(dp(app, 28), dp(app, 14), dp(app, 28), dp(app, 14));
        unlock.setOnClickListener(v -> GuardService.unfreeze(app, "遮罩按钮"));
        root.addView(unlock);

        // 兜底：任何触摸都被吃掉，不传给底下的 App
        root.setOnTouchListener((v, ev) -> true);

        int type = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                : WindowManager.LayoutParams.TYPE_PHONE;

        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                w, h, type,
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
                        | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
                PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.TOP | Gravity.START;
        lp.x = 0;
        lp.y = 0;

        try {
            wm.addView(root, lp);
            overlayView = root;
            showing = true;
        } catch (Throwable t) {
            showing = false;
        }
    }

    public static void hide() {
        if (!showing || wm == null || overlayView == null) {
            showing = false;
            return;
        }
        try {
            wm.removeViewImmediate(overlayView);
        } catch (Throwable ignore) { }
        overlayView = null;
        showing = false;
    }

    private static int dp(Context c, int v) {
        return (int) (v * c.getResources().getDisplayMetrics().density + 0.5f);
    }

    /** 供外部判断某个事件是否落在遮罩上（调试用） */
    public static boolean consumes(MotionEvent e) { return showing; }

    private TouchBlockOverlay() { }
}
