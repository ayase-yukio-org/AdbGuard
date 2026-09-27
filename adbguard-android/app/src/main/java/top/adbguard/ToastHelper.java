package top.adbguard;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

/** Toast 小工具（统一走主线程）。 */
public final class ToastHelper {

    private static final Handler H = new Handler(Looper.getMainLooper());

    public static void show(Context ctx, final String msg) {
        if (ctx == null || msg == null) return;
        final Context app = ctx.getApplicationContext();
        H.post(new Runnable() {
            @Override public void run() {
                try {
                    Toast.makeText(app, msg, Toast.LENGTH_SHORT).show();
                } catch (Throwable ignore) { }
            }
        });
    }

    private ToastHelper() { }
}
