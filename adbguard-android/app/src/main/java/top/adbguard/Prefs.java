package top.adbguard;

import android.content.Context;
import android.content.SharedPreferences;

/** 极简偏好存储（不引 AndroidX）。 */
public final class Prefs {

    private static final String FILE = "adbguard";

    public static final String K_BACKEND_HOST = "backend_host";
    public static final String K_BACKEND_PORT = "backend_port";
    public static final String K_AUTO_FREEZE = "auto_freeze";      // 命中高危是否自动冻结
    public static final String K_FROZEN = "frozen";                // 当前是否处于冻结态
    public static final String K_BLOCKED_PKGS = "blocked_pkgs";    // 已处置的包名(逗号分隔)
    public static final String K_DEVICE_OWNER = "device_owner";
    public static final String K_WATCHDOG = "watchdog_enabled";
    public static final String K_GYRO_SHIELD = "gyro_shield";      // 常驻干扰陀螺仪(关自动旋转)
    public static final String K_ONBOARDED = "onboarded";          // 引导流程是否走完
    public static final String K_LAST_CHECK = "last_check";        // 上次检查结果文本

    private final SharedPreferences sp;

    public Prefs(Context ctx) {
        sp = ctx.getApplicationContext().getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    public String getString(String k, String def) { return sp.getString(k, def); }
    public void putString(String k, String v) { sp.edit().putString(k, v).apply(); }
    public boolean getBool(String k, boolean def) { return sp.getBoolean(k, def); }
    public void putBool(String k, boolean v) { sp.edit().putBoolean(k, v).apply(); }
    public int getInt(String k, int def) { return sp.getInt(k, def); }
    public void putInt(String k, int v) { sp.edit().putInt(k, v).apply(); }

    public String backendHost() { return getString(K_BACKEND_HOST, ""); }
    public int backendPort() { return getInt(K_BACKEND_PORT, 8720); }

    public boolean isFrozen() { return getBool(K_FROZEN, false); }
    public void setFrozen(boolean v) { putBool(K_FROZEN, v); }

    public void addBlockedPkg(String pkg) {
        String cur = getString(K_BLOCKED_PKGS, "");
        if (cur.contains(pkg)) return;
        putString(K_BLOCKED_PKGS, cur.isEmpty() ? pkg : cur + "," + pkg);
    }

    public boolean isBlocked(String pkg) {
        String cur = getString(K_BLOCKED_PKGS, "");
        if (cur.isEmpty() || pkg == null) return false;
        for (String s : cur.split(",")) if (s.trim().equals(pkg)) return true;
        return false;
    }
}
