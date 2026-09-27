"""
server.py —— 手机端（APK）联系后端的入口。

三个 HTTP 接口 + 一个 UDP 发现服务：

  GET  /api/ping        → {"ok": true, "msg": "..."}
  POST /api/force_stop  → {"pkg": "com.xxx"}        后端直接 adb am force-stop
  POST /api/blocked     → {"pkg": "...", "action": "..."}   记一笔处置日志
  GET  /api/directive   → 后端当前想对手机下的指令（冻结/杀谁）

  UDP  8721             → 收到 "ADBGUARD?" 就回 "ADBGUARD <本机IP> <HTTP端口>"

手机 APK 用「申请后端 → 自动发现」就能拿到本机 IP，老人不用手输。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

import config
import remediate


# ----------------------------------------------------------------------
# 共享状态
# ----------------------------------------------------------------------

class State:
    def __init__(self, adb=None):
        self.adb = adb
        self.lock = threading.Lock()
        self.actions: List[Dict[str, Any]] = []      # 处置日志
        self.pending_kill: List[str] = []            # 待执行的 kill 队列
        self.freeze_requested = False
        self.last_seen: Dict[str, Any] = {}

    def log_action(self, pkg: str, action: str, extra: str = "") -> None:
        with self.lock:
            self.actions.append({
                "ts": time.time(),
                "pkg": pkg,
                "action": action,
                "extra": extra,
            })
            if len(self.actions) > 500:
                self.actions = self.actions[-500:]


STATE = State()


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    server_version = "AdbGuardBackend/1.0"

    # ---------- 工具 ----------

    def _send(self, code: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _read_json(self) -> Dict[str, Any]:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            raw = self.rfile.read(n).decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            return {}

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # 静音掉默认的每请求一行，改成只在需要时打印
        if "--verbose" in _SERVER_FLAGS:
            print(f"[http] {self.address_string()} {fmt % args}")

    # ---------- 路由 ----------

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path == "/api/ping":
            adb = STATE.adb
            device = ""
            if adb is not None:
                try:
                    device = f"{adb.model()} / Android {adb.android_version()}"
                except Exception:
                    device = "设备已断开"
            self._send(200, {
                "ok": True,
                "msg": f"后端在线 · {device}" if device else "后端在线（未连接设备）",
                "device": device,
                "ts": time.time(),
            })
            return

        if path == "/api/directive":
            with STATE.lock:
                kill = list(STATE.pending_kill)
                STATE.pending_kill.clear()
                freeze = STATE.freeze_requested
            self._send(200, {
                "ok": True,
                "kill": kill,
                "freeze": freeze,
                "ts": time.time(),
            })
            return

        if path == "/api/actions":
            with STATE.lock:
                self._send(200, {"ok": True, "actions": STATE.actions[-100:]})
            return

        self._send(404, {"ok": False, "msg": "unknown endpoint"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        data = self._read_json()

        if path == "/api/force_stop":
            pkg = str(data.get("pkg", "")).strip()
            if not pkg:
                self._send(400, {"ok": False, "msg": "缺少 pkg"})
                return
            adb = STATE.adb
            if adb is None:
                self._send(503, {"ok": False, "msg": "后端没有连接设备"})
                return
            try:
                ok = remediate.kill(adb, pkg)
                STATE.log_action(pkg, "force_stop", f"ok={ok}")
                print(f"[后端] 手机请求强杀 → {pkg} : {'成功' if ok else '失败'}")
                self._send(200, {"ok": bool(ok), "msg": f"已 force-stop {pkg}"})
            except Exception as e:  # noqa: BLE001
                STATE.log_action(pkg, "force_stop", f"error={e}")
                self._send(500, {"ok": False, "msg": f"执行失败: {e}"})
            return

        if path == "/api/blocked":
            pkg = str(data.get("pkg", "")).strip()
            action = str(data.get("action", "unknown")).strip()
            model = str(data.get("model", "")).strip()
            STATE.log_action(pkg, action, model)
            print(f"[后端] 手机上报：{action} ← {pkg}  ({model})")
            self._send(200, {"ok": True})
            return

        if path == "/api/quarantine":
            pkg = str(data.get("pkg", "")).strip()
            adb = STATE.adb
            if not pkg or adb is None:
                self._send(400, {"ok": False, "msg": "缺少 pkg 或没有设备"})
                return
            try:
                res = remediate.quarantine(adb, pkg)
                STATE.log_action(pkg, "quarantine", "; ".join(res.steps))
                self._send(200, {"ok": res.ok, "steps": res.steps})
            except Exception as e:  # noqa: BLE001
                self._send(500, {"ok": False, "msg": str(e)})
            return

        if path == "/api/request_kill":
            pkg = str(data.get("pkg", "")).strip()
            if pkg:
                with STATE.lock:
                    STATE.pending_kill.append(pkg)
            self._send(200, {"ok": True})
            return

        self._send(404, {"ok": False, "msg": "unknown endpoint"})


_SERVER_FLAGS: List[str] = []


# ----------------------------------------------------------------------
# UDP 自动发现
# ----------------------------------------------------------------------

def _local_ip() -> str:
    """拿本机在局域网里的 IP（不会真的发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def start_udp_responder(stop_event: threading.Event, http_port: int = config.HTTP_PORT) -> threading.Thread:
    def loop() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", config.UDP_PORT))
        except OSError as e:
            print(f"[udp] 绑定 {config.UDP_PORT} 失败：{e}（自动发现不可用）")
            return
        sock.settimeout(1.0)
        ip = _local_ip()
        print(f"[udp] 自动发现已开启，监听 {config.UDP_PORT}（本机 {ip}）")
        while not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            msg = data.decode("utf-8", "replace").strip()
            if msg.startswith(config.UDP_MAGIC.rstrip("?")):
                reply = f"ADBGUARD {ip} {http_port}".encode("utf-8")
                try:
                    sock.sendto(reply, addr)
                    print(f"[udp] 回应发现请求 → {addr[0]}")
                except OSError:
                    pass
        sock.close()

    t = threading.Thread(target=loop, name="udp-discover", daemon=True)
    t.start()
    return t


# ----------------------------------------------------------------------
# 启动
# ----------------------------------------------------------------------

def serve(adb=None, port: int = config.HTTP_PORT, verbose: bool = False) -> None:
    """阻塞式启动后端。"""
    global _SERVER_FLAGS
    _SERVER_FLAGS = ["--verbose"] if verbose else []

    if adb is None:
        try:
            adb = __import__("adbutil").Adb.auto()
        except Exception as e:  # noqa: BLE001
            print(f"[!] 没有找到可用设备：{e}")
            print("    后端仍会启动，但 /api/force_stop 会返回 503。")
    STATE.adb = adb

    stop = threading.Event()
    start_udp_responder(stop, port)

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpd.daemon_threads = True

    print()
    print("=" * 62)
    print(f"  守护喵后端已启动")
    print(f"  HTTP : http://{_local_ip()}:{port}")
    print(f"  UDP  : {config.UDP_PORT}  （手机端「自动发现」用）")
    print(f"  设备 : {adb!r}")
    print("=" * 62)
    print("  手机端操作：守护喵 → ② 申请后端（ADB）→ 自动发现")
    print("  Ctrl+C 退出")
    print()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭…")
    finally:
        stop.set()
        httpd.server_close()
