package top.adbguard;

import android.os.Build;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * BackendClient —— 手机端联系 Python 后端的唯一出口。
 *
 * 三个接口（和后端 server.py 一一对应）：
 *   GET  /api/ping         探活
 *   POST /api/force_stop   {"pkg": "com.xxx.yyy"}  → 后端执行 adb am force-stop
 *   POST /api/blocked      {"pkg": "...", "action": "..."}  上报处置记录
 *
 * 另外提供 UDP 广播发现：手机和后端在同一 WiFi 下时，
 * 后端监听 UDP 8721，手机广播一句 "ADBGUARD?" 就能自动拿到后端 IP，
 * 省得老人手输地址。
 *
 * 全部走后台线程，UI 永远不卡。
 */
public final class BackendClient {

    private static final String TAG = "AdbGuard/Net";

    public static final int UDP_DISCOVER_PORT = 8721;
    private static final String DISCOVER_MAGIC = "ADBGUARD?";

    private static final ExecutorService POOL = Executors.newFixedThreadPool(2);

    /* ------------------------------------------------------------------ */

    public interface Callback {
        void onResult(boolean ok, String message);
    }

    public static void ping(final Prefs prefs, final Callback cb) {
        POOL.execute(new Runnable() {
            @Override public void run() {
                try {
                    String body = get(prefs, "/api/ping");
                    JSONObject o = new JSONObject(body);
                    boolean ok = o.optBoolean("ok", true);
                    if (cb != null) cb.onResult(ok, o.optString("msg", "后端在线"));
                } catch (Throwable t) {
                    if (cb != null) cb.onResult(false, "连不上后端：" + t.getMessage());
                }
            }
        });
    }

    /** 让后端去 force-stop（异步，不等结果，失败了本地兜底已经做了）。 */
    public static void requestForceStop(final Prefs prefs, final String pkg) {
        if (pkg == null || pkg.isEmpty()) return;
        POOL.execute(new Runnable() {
            @Override public void run() {
                try {
                    JSONObject req = new JSONObject();
                    req.put("pkg", pkg);
                    req.put("action", "force_stop");
                    String r = post(prefs, "/api/force_stop", req.toString());
                    Log.i(TAG, "后端 force-stop 返回: " + r);
                } catch (Throwable t) {
                    Log.w(TAG, "后端不可用，仅本地处置: " + t.getMessage());
                }
            }
        });
    }

    /** 把"冻住/跳过广告/失败"这类处置记录上报给后端，方便家长看日志。 */
    public static void reportBlocked(final Prefs prefs, final String pkg, final String action) {
        if (pkg == null || pkg.isEmpty()) return;
        POOL.execute(new Runnable() {
            @Override public void run() {
                try {
                    JSONObject req = new JSONObject();
                    req.put("pkg", pkg);
                    req.put("action", action);
                    req.put("model", Build.MANUFACTURER + " " + Build.MODEL);
                    req.put("ts", System.currentTimeMillis());
                    post(prefs, "/api/blocked", req.toString());
                } catch (Throwable ignore) { }
            }
        });
    }

    /** 后端主动下发的"当前是否需要冻结"（后端也在跑检测时用得上）。 */
    public static void pollDirective(final Prefs prefs, final Callback cb) {
        POOL.execute(new Runnable() {
            @Override public void run() {
                try {
                    String body = get(prefs, "/api/directive");
                    if (cb != null) cb.onResult(true, body);
                } catch (Throwable t) {
                    if (cb != null) cb.onResult(false, t.getMessage());
                }
            }
        });
    }

    /* ------------------------------------------------------------------ */
    /*  UDP 自动发现后端                                                    */
    /* ------------------------------------------------------------------ */

    public static void discover(final Prefs prefs, final Callback cb) {
        POOL.execute(new Runnable() {
            @Override public void run() {
                DatagramSocket socket = null;
                try {
                    socket = new DatagramSocket();
                    socket.setBroadcast(true);
                    socket.setSoTimeout(2500);

                    byte[] send = DISCOVER_MAGIC.getBytes(StandardCharsets.UTF_8);
                    DatagramPacket out = new DatagramPacket(send, send.length,
                            InetAddress.getByName("255.255.255.255"), UDP_DISCOVER_PORT);
                    socket.send(out);

                    byte[] buf = new byte[512];
                    DatagramPacket in = new DatagramPacket(buf, buf.length);
                    socket.receive(in);

                    String reply = new String(in.getData(), 0, in.getLength(), StandardCharsets.UTF_8).trim();
                    // 后端回 "ADBGUARD 192.168.1.7 8720"
                    String[] parts = reply.split("\\s+");
                    if (parts.length >= 2) {
                        prefs.putString(Prefs.K_BACKEND_HOST, parts[1]);
                        if (parts.length >= 3) {
                            try { prefs.putInt(Prefs.K_BACKEND_PORT, Integer.parseInt(parts[2])); }
                            catch (Throwable ignore) { }
                        }
                        if (cb != null) cb.onResult(true, "已发现后端 " + parts[1]);
                        return;
                    }
                    if (cb != null) cb.onResult(false, "广播回了奇怪的响应：" + reply);
                } catch (Throwable t) {
                    if (cb != null) cb.onResult(false, "没发现后端：" + t.getMessage());
                } finally {
                    if (socket != null) socket.close();
                }
            }
        });
    }

    /* ------------------------------------------------------------------ */
    /*  底层 HTTP                                                          */
    /* ------------------------------------------------------------------ */

    private static String baseUrl(Prefs p) {
        String host = p.backendHost();
        if (host == null || host.isEmpty()) throw new IllegalStateException("未配置后端地址");
        return "http://" + host + ":" + p.backendPort();
    }

    private static String get(Prefs p, String path) throws Exception {
        HttpURLConnection c = open(p, path, "GET");
        try {
            int code = c.getResponseCode();
            String body = read(code >= 400 ? c.getErrorStream() : c.getInputStream());
            if (code >= 400) throw new IllegalStateException("HTTP " + code + " " + body);
            return body;
        } finally {
            c.disconnect();
        }
    }

    private static String post(Prefs p, String path, String json) throws Exception {
        HttpURLConnection c = open(p, path, "POST");
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        try {
            OutputStream os = c.getOutputStream();
            os.write(json.getBytes(StandardCharsets.UTF_8));
            os.flush();
            os.close();
            int code = c.getResponseCode();
            String body = read(code >= 400 ? c.getErrorStream() : c.getInputStream());
            if (code >= 400) throw new IllegalStateException("HTTP " + code + " " + body);
            return body;
        } finally {
            c.disconnect();
        }
    }

    private static HttpURLConnection open(Prefs p, String path, String method) throws Exception {
        URL url = new URL(baseUrl(p) + path);
        HttpURLConnection c = (HttpURLConnection) url.openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(2000);
        c.setReadTimeout(4000);
        return c;
    }

    private static String read(InputStream is) throws Exception {
        if (is == null) return "";
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[2048];
        int n;
        while ((n = is.read(buf)) > 0) bos.write(buf, 0, n);
        is.close();
        return new String(bos.toByteArray(), StandardCharsets.UTF_8);
    }

    private BackendClient() { }
}
