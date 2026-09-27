package top.adbguard;

import android.graphics.Rect;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * UiHeuristics —— 可疑页面打分引擎（Java 侧）。
 *
 * 打分规则与 Python 后端 detector.py 保持一致，方便两端交叉验证：
 *   大片空白            +50
 *   命中诱导关键词       +30（每类一次，最多 +60）
 *   假关闭按钮聚簇       +30
 *   大量无文字可点节点   +25
 *   极少节点 + 全屏图    +40
 *   高危包名             +40
 *
 * 判定动作： score >= 80 || (blankLarge && keywordHit) || (score >= 60 && riskyPkg)
 */
public final class UiHeuristics {

    /** 屏幕上的一个节点（从 AccessibilityNodeInfo 或 uiautomator XML 抽出来的精简结构）。 */
    public static final class Node {
        public String text = "";
        public String cls = "";
        public String pkg = "";
        public boolean clickable;
        public boolean scrollable;
        public int x1, y1, x2, y2;

        public int width() { return Math.max(0, x2 - x1); }
        public int height() { return Math.max(0, y2 - y1); }
        public long area() { return (long) width() * (long) height(); }
        public boolean isEmptyText() { return text.trim().isEmpty(); }
        public boolean isImageLike() {
            String c = cls.toLowerCase(Locale.ROOT);
            return c.contains("imageview") || c.contains("imagebutton");
        }
    }

    public static final class Result {
        public int score;
        public boolean blankLarge;
        public boolean keywordHit;
        public boolean riskyPkg;
        public double blankRatio;
        public int nodeCount;
        public int textNodeCount;
        public int fakeCloseCount;
        public final List<String> reasons = new ArrayList<>();

        public boolean shouldAct() {
            return score >= 80 || (blankLarge && keywordHit) || (score >= 60 && riskyPkg);
        }

        @Override public String toString() {
            return "score=" + score + " blank=" + String.format(Locale.ROOT, "%.2f", blankRatio)
                    + " nodes=" + nodeCount + " text=" + textNodeCount
                    + " fakeClose=" + fakeCloseCount + " -> " + reasons;
        }
    }

    /* ------------------------------------------------------------------ */
    /*  关键词表                                                            */
    /* ------------------------------------------------------------------ */

    /** 诱导退出类：假"关闭"变成"是否退出？" */
    private static final String[] KW_EXIT = {
            "是否退出", "确认退出", "确定退出", "退出应用", "再想想", "狠心离开",
            "真的要离开", "确认离开", "退出将", "退出后", "放弃", "不要走"
    };

    /** 清理优化类：伪装成系统工具 */
    private static final String[] KW_CLEAN = {
            "手机清理", "一键清理", "立即清理", "深度清理", "垃圾清理", "扫描垃圾",
            "内存不足", "加速", "优化大师", "清理大师", "手机管家", "病毒", "风险",
            "检测到", "存在风险", "发热", "清理缓存", "立即优化", "免费清理"
    };

    /** 奖励诱饵类 */
    private static final String[] KW_REWARD = {
            "奖励", "礼物", "红包", "领取", "恭喜", "免费领", "金币", "元宝",
            "提现", "翻倍", "点击领取", "抽奖", "中奖", "福利", "限时"
    };

    /** 付款/权限诱导 */
    private static final String[] KW_TRAP = {
            "开通会员", "自动续费", "仅需", "0元", "输入手机号", "验证码",
            "允许访问", "通讯录", "相册", "定位权限"
    };

    private static final String[][] KEYWORD_GROUPS = {KW_EXIT, KW_CLEAN, KW_REWARD, KW_TRAP};

    /** 已知高危包名特征（伪装成系统工具的常见命名套路） */
    private static final String[] RISKY_PKG_HINTS = {
            "clean", "boost", "optimize", "optimizer", "speedup", "ram",
            "vpn", "lock", "battery", "tools", "scan", "master", "guard",
            "temple", "island", "media", "toolbox", "pack", "booster"
    };

    /* ------------------------------------------------------------------ */

    private static final Pattern BOUNDS = Pattern.compile("\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]");

    /** 从 uiautomator dump 的 XML 里解析节点（后端 Python 侧用同一套正则）。 */
    public static List<Node> parseNodes(String xml, String attrPatternTemplate) {
        List<Node> out = new ArrayList<>();
        if (xml == null) return out;
        // 这里刻意用最朴素的方式解析，避免在无障碍线程里跑重型 XML 解析
        int idx = 0;
        while (true) {
            int start = xml.indexOf("<node", idx);
            if (start < 0) break;
            int end = xml.indexOf('>', start);
            if (end < 0) break;
            String tag = xml.substring(start, end);
            idx = end + 1;

            // 只取有界面的节点
            String bounds = attrOf(tag, "bounds");
            if (bounds == null) continue;
            Matcher m = BOUNDS.matcher(bounds);
            if (!m.find()) continue;

            Node n = new Node();
            n.x1 = Integer.parseInt(m.group(1));
            n.y1 = Integer.parseInt(m.group(2));
            n.x2 = Integer.parseInt(m.group(3));
            n.y2 = Integer.parseInt(m.group(4));
            n.text = unesc(attrOf(tag, "text"));
            n.cls = unesc(attrOf(tag, "class"));
            n.pkg = unesc(attrOf(tag, "package"));
            n.clickable = "true".equals(attrOf(tag, "clickable"));
            n.scrollable = "true".equals(attrOf(tag, "scrollable"));
            out.add(n);
        }
        return out;
    }

    private static String attrOf(String tag, String name) {
        String key = name + "=\"";
        int i = tag.indexOf(key);
        if (i < 0) return null;
        int s = i + key.length();
        int e = tag.indexOf('"', s);
        if (e < 0) return null;
        return tag.substring(s, e);
    }

    private static String unesc(String s) {
        if (s == null) return "";
        return s.replace("&quot;", "\"").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#10;", " ");
    }

    /* ------------------------------------------------------------------ */

    /** 主入口：给一批节点 + 屏幕尺寸 + 前台包名，出分。 */
    public static Result score(List<Node> nodes, int screenW, int screenH, String foregroundPkg) {
        Result r = new Result();
        r.nodeCount = nodes.size();
        if (screenW <= 0 || screenH <= 0 || nodes.isEmpty()) {
            r.reasons.add("空布局");
            return r;
        }

        // ---- 统计 ----
        StringBuilder allText = new StringBuilder();
        int textNodes = 0;
        int textlessClickable = 0;
        int smallTextlessClickable = 0;
        long fullScreenThreshold = (long) screenW * screenH * 7 / 10;
        boolean hasFullScreenImage = false;
        int scrollableNodes = 0;

        List<Node> clickableGuys = new ArrayList<>();

        for (Node n : nodes) {
            if (!n.isEmptyText()) {
                textNodes++;
                allText.append(n.text).append('\u0001');
            } else if (n.clickable) {
                textlessClickable++;
                long a = n.area();
                // 小尺寸 = 疑似假关闭按钮（X / ✕ / 图标）
                if (a > 0 && a < (long) screenW * screenH * 12 / 100) {
                    smallTextlessClickable++;
                    clickableGuys.add(n);
                }
            }
            if (n.isImageLike() && n.area() >= fullScreenThreshold) hasFullScreenImage = true;
            if (n.scrollable) scrollableNodes++;
        }
        r.textNodeCount = textNodes;

        // ---- 1. 空白率 ----
        r.blankRatio = computeBlankRatio(nodes, screenW, screenH, 24, 48, fullScreenThreshold);
        if (r.blankRatio >= 0.60) {
            r.blankLarge = true;
            r.score += 50;
            r.reasons.add("大片空白 " + pct(r.blankRatio));
        }

        // ---- 2. 关键词 ----
        String text = allText.toString();
        int groupsHit = 0;
        for (String[] g : KEYWORD_GROUPS) {
            boolean hit = false;
            for (String k : g) {
                if (text.contains(k)) { hit = true; break; }
            }
            if (hit) {
                groupsHit++;
                r.score += 30;
            }
        }
        if (groupsHit > 0) {
            r.keywordHit = true;
            r.reasons.add("诱导关键词命中 " + groupsHit + " 类");
        }

        // ---- 3. 假关闭按钮聚簇 ----
        r.fakeCloseCount = smallTextlessClickable;
        if (smallTextlessClickable >= 3 && hasCluster(clickableGuys, screenW, screenH)) {
            r.score += 30;
            r.reasons.add("假关闭按钮聚簇 x" + smallTextlessClickable);
        } else if (smallTextlessClickable >= 8) {
            r.score += 20;
            r.reasons.add("大量无文字按钮 x" + smallTextlessClickable);
        }

        // ---- 4. 大量无文字可点节点 ----
        if (textlessClickable >= 8) {
            r.score += 25;
            r.reasons.add("无文字可点节点 x" + textlessClickable);
        }

        // ---- 5. 极少节点 + 全屏图（假截图页面）----
        if (hasFullScreenImage && nodes.size() <= 8 && textNodes <= 2) {
            r.score += 40;
            r.reasons.add("伪装截图页面");
        }

        // ---- 6. 高危包名 ----
        String p = foregroundPkg == null ? "" : foregroundPkg.toLowerCase(Locale.ROOT);
        for (String hint : RISKY_PKG_HINTS) {
            if (p.contains(hint)) {
                r.riskyPkg = true;
                r.score += 40;
                r.reasons.add("高危包名特征:" + hint);
                break;
            }
        }

        // ---- 7. 完全没有可滚动容器的"界面"多半是广告壳 ----
        if (scrollableNodes == 0 && textlessClickable >= 5 && nodes.size() >= 10) {
            r.score += 15;
            r.reasons.add("无可滚动容器");
        }

        if (r.score > 100) r.score = 100;
        return r;
    }

    /**
     * 空白率：把屏幕切成 gx*gy 网格，被"有实质内容"的节点覆盖的格子算非空白。
     * 全屏图片/全屏容器不算"有内容"（否则假截图页面空白率恒为 0）。
     */
    private static double computeBlankRatio(List<Node> nodes, int w, int h,
                                            int gx, int gy, long fullScreenThreshold) {
        boolean[][] grid = new boolean[gy][gx];
        int covered = 0;
        for (Node n : nodes) {
            long a = n.area();
            if (a <= 0) continue;
            boolean substantive = !n.isEmptyText() || (n.clickable && a < fullScreenThreshold);
            if (!substantive) continue;

            int cx1 = clamp((int) ((long) n.x1 * gx / w), 0, gx - 1);
            int cx2 = clamp((int) ((long) n.x2 * gx / w), 0, gx - 1);
            int cy1 = clamp((int) ((long) n.y1 * gy / h), 0, gy - 1);
            int cy2 = clamp((int) ((long) n.y2 * gy / h), 0, gy - 1);
            for (int y = cy1; y <= cy2; y++) {
                for (int x = cx1; x <= cx2; x++) {
                    if (!grid[y][x]) { grid[y][x] = true; covered++; }
                }
            }
        }
        return 1.0 - (double) covered / (double) (gx * gy);
    }

    /** 假关闭按钮是否扎堆：把它们按 6x12 粗网格分桶，某桶 >=3 就认为是"一堆假X"。 */
    private static boolean hasCluster(List<Node> guys, int w, int h) {
        if (guys.isEmpty()) return false;
        java.util.HashMap<String, Integer> buckets = new java.util.HashMap<>();
        for (Node n : guys) {
            int bx = clamp((int) ((long) (n.x1 + n.x2) / 2 * 6 / Math.max(1, w)), 0, 5);
            int by = clamp((int) ((long) (n.y1 + n.y2) / 2 * 12 / Math.max(1, h)), 0, 11);
            String key = bx + "," + by;
            Integer c = buckets.get(key);
            buckets.put(key, c == null ? 1 : c + 1);
        }
        for (Integer v : buckets.values()) if (v >= 3) return true;
        return false;
    }

    private static int clamp(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

    private static String pct(double d) { return String.format(Locale.ROOT, "%.0f%%", d * 100); }

    /** 把屏幕上一个矩形并进 nodes（供 overlay 复用）。 */
    public static Node rect(String cls, String pkg, Rect rr) {
        Node n = new Node();
        n.cls = cls; n.pkg = pkg;
        n.x1 = rr.left; n.y1 = rr.top; n.x2 = rr.right; n.y2 = rr.bottom;
        return n;
    }

    /** 去重后的关键词（给通知文案用）。 */
    public static Set<String> matchedKeywords(String text) {
        Set<String> hit = new LinkedHashSet<>();
        if (text == null) return hit;
        for (String[] g : KEYWORD_GROUPS)
            for (String k : g) if (text.contains(k)) hit.add(k);
        return hit;
    }

    private UiHeuristics() {}
}
