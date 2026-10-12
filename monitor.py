#!/usr/bin/env python3
"""Offline NPU dashboard. Python standard library + system OpenSSH only."""

import argparse
import copy
import hmac
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from npu_parser import parse_info
from password_auth import PasswordBroker

ROOT = Path(__file__).resolve().parent
MAX_OUTPUT = 2 * 1024 * 1024
MAX_NODES = 64
HOST = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:\-]*$")
USER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@\-]*$")


def now():
    return datetime.now(timezone.utc).isoformat()


def bounded_number(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须是数字")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label}须在 {minimum}–{maximum} 之间")
    return float(value)


def key_path(value, base):
    if not isinstance(value, str):
        raise ValueError("私钥路径必须是字符串")
    if not value.strip():
        return ""
    path = Path(os.path.expandvars(os.path.expanduser(value.strip())))
    if path.suffix.lower() == ".pub":
        raise ValueError("请指定私钥文件，而不是 .pub 公钥文件")
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"私钥文件不存在：{path}")
    return str(path)


def command_path(value, optional=False):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError("npu-smi 路径必须是不含控制字符的字符串")
    value = value.strip()
    if optional and not value:
        return ""
    if value != "npu-smi" and not value.startswith("/"):
        raise ValueError("npu-smi 路径须为 npu-smi 或远端的绝对路径")
    return value


@dataclass(frozen=True)
class Node:
    name: str
    host: str
    user: str = ""
    port: int | None = None
    identity_file: str = ""
    npu_smi_path: str = ""
    auth_type: str = "key"
    password: str = field(default="", repr=False)


def validate_config(data, base, current=None):
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")
    interval = bounded_number(data.get("interval", 5), "监控周期（秒）", 1, 3600)
    timeout = bounded_number(data.get("timeout", 15), "采集超时（秒）", 1, 300)
    identity = key_path(data.get("identity_file", ""), base)
    executable = command_path(data.get("npu_smi_path", "npu-smi"))
    entries = data.get("nodes", [])
    if not isinstance(entries, list) or len(entries) > MAX_NODES:
        raise ValueError(f"nodes 必须是数组，最多 {MAX_NODES} 个节点")
    nodes, names = [], set()
    previous = (current or {}).get("nodes", [])
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("每个节点必须是 JSON 对象")
        host = entry.get("host", "")
        user = entry.get("user", "")
        if not isinstance(host, str) or not HOST.fullmatch(host):
            raise ValueError("节点地址须为 IP、主机名或 SSH 配置中的 Host 别名")
        if not isinstance(user, str) or (user and not USER.fullmatch(user)):
            raise ValueError(f"{host} 的 SSH 用户名无效")
        name = entry.get("name", host)
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("节点名称须为 1–100 字符")
        name = name.strip()
        if name in names:
            raise ValueError(f"节点名称重复：{name}")
        names.add(name)
        port = entry.get("port")
        if port in (None, ""):
            port = None
        elif isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError(f"{name} 的端口须为 1–65535 的整数")
        auth_type = entry.get("auth_type", "key")
        if auth_type not in ("key", "password"):
            raise ValueError(f"{name} 的认证方式须为 key 或 password")
        old = next((node for node in previous if node["name"] == name), None)
        if old is None:
            old = next((node for node in previous if (node["host"], node["user"], node["port"]) == (host, user, port)), {})
        password = entry.get("password", old.get("password", ""))
        if not isinstance(password, str) or len(password.encode("utf-8")) > 4096 or any(c in password for c in "\r\n\x00"):
            raise ValueError(f"{name} 的密码须为不含换行或空字符的字符串，最多 4096 字节")
        if auth_type == "password" and not password:
            raise ValueError(f"请填写 {name} 的 SSH 登录密码")
        if auth_type == "key":
            password = ""
        identity_file = key_path(entry.get("identity_file", ""), base) if auth_type == "key" else ""
        nodes.append(Node(name, host, user, port, identity_file,
                          command_path(entry.get("npu_smi_path", ""), optional=True), auth_type, password))
    return {"interval": interval, "timeout": timeout, "identity_file": identity,
            "npu_smi_path": executable, "nodes": [asdict(node) for node in nodes]}


def public_config(config):
    result = copy.deepcopy(config)
    for node in result["nodes"]:
        node["password_set"] = bool(node.pop("password", ""))
    return result


class CollectionError(Exception):
    def __init__(self, message, raw="", exit_code=None):
        super().__init__(message)
        self.raw = raw
        self.exit_code = exit_code


class SSHSession:
    """A single SSH process carries successive framed npu-smi requests."""

    def __init__(self, node, identity, timeout, ssh="ssh", executable="npu-smi"):
        self.node, self.identity, self.timeout, self.ssh = node, identity, timeout, ssh
        self.executable = executable
        self.process = None
        self.lines = None
        self.lock = threading.Lock()
        self.closed = False
        self.broker = None

    def _start(self):
        # The remote command and the stdin protocol are fixed; node inputs never
        # enter a local shell or the remote shell script.
        script = (
            "PATH=/usr/local/bin:/usr/local/sbin:/usr/local/Ascend/driver/tools:$PATH; "
            "export PATH; LC_ALL=C; export LC_ALL; "
            "while IFS= read -r request; do "
            "case \"$request\" in NPU_MONITOR_*) "
            "printf '\\n%s BEGIN\\n' \"$request\"; "
            f"{shlex.quote(self.executable)} info </dev/null 2>&1; rc=$?; "
            "printf '\\n%s END %s\\n' \"$request\" \"$rc\";; "
            "*) exit 2;; esac; done"
        )
        args = [self.ssh, "-T", "-o", "BatchMode=" + ("no" if self.node.auth_type == "password" else "yes"), "-o", "StrictHostKeyChecking=yes",
                "-o", f"ConnectTimeout={max(1, math.ceil(self.timeout))}",
                "-o", "ConnectionAttempts=1", "-o", "ServerAliveInterval=5",
                "-o", "ServerAliveCountMax=2", "-o", "LogLevel=ERROR",
                "-o", "ClearAllForwardings=yes", "-o", "ForwardAgent=no",
                "-o", "ControlMaster=no", "-o", "ControlPath=none"]
        if self.node.port:
            args += ["-p", str(self.node.port)]
        if self.node.user:
            args += ["-l", self.node.user]
        if self.node.auth_type == "password":
            args += ["-o", "PreferredAuthentications=password", "-o", "PasswordAuthentication=yes",
                     "-o", "PubkeyAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
                     "-o", "NumberOfPasswordPrompts=1"]
        elif self.identity:
            args += ["-i", self.identity, "-o", "IdentitiesOnly=yes"]
        args += [self.node.host, "sh -c " + shlex.quote(script)]
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        if os.name != "nt" and self.node.auth_type == "password":
            kwargs["start_new_session"] = True
        broker = None
        try:
            if self.node.auth_type == "password":
                broker = PasswordBroker(self.node.password)
                kwargs["env"] = {**os.environ, **broker.start()}
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, **kwargs)
        except BaseException:
            if broker:
                broker.close()
            raise
        lines = queue.Queue(maxsize=512)
        with self.lock:
            if self.closed:
                process.kill()
                process.communicate()
                if broker:
                    broker.close()
                raise CollectionError("采集已停止")
            self.process, self.lines, self.broker = process, lines, broker
        threading.Thread(target=self._read, args=(process, lines), daemon=True).start()
        return process, lines

    @staticmethod
    def _read(process, lines):
        try:
            while True:
                chunk = process.stdout.readline(65536)
                if not chunk:
                    break
                try:
                    lines.put(chunk.decode("utf-8", errors="replace"), timeout=0.5)
                except queue.Full:
                    process.kill()
                    break
        finally:
            # The reader is the only stdout owner and closes it after EOF.
            process.stdout.close()
            try:
                lines.put(None, timeout=0.5)
            except queue.Full:
                pass

    def _disconnect(self):
        with self.lock:
            process, self.process = self.process, None
            broker, self.broker = self.broker, None
            self.lines = None
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stdin:
                process.stdin.close()
        if broker:
            broker.close()

    def _finish_auth(self):
        with self.lock:
            broker, self.broker = self.broker, None
        if broker:
            broker.close()

    def close(self):
        with self.lock:
            self.closed = True
        self._disconnect()

    def query(self):
        with self.lock:
            if self.closed:
                raise CollectionError("采集已停止")
            process, lines = self.process, self.lines
        raw, prelude, size = [], [], 0
        try:
            if process is None:
                process, lines = self._start()
            request = "NPU_MONITOR_" + secrets.token_hex(16)
            process.stdin.write((request + "\n").encode("ascii"))
            process.stdin.flush()
            deadline = time.monotonic() + self.timeout
            began = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CollectionError(f"采集超过 {self.timeout:g} 秒，下一周期自动重连",
                                          "".join(raw or prelude))
                try:
                    line = lines.get(timeout=remaining)
                except queue.Empty:
                    raise CollectionError(f"采集超过 {self.timeout:g} 秒，下一周期自动重连",
                                          "".join(raw or prelude)) from None
                if line is None:
                    process.wait(timeout=5)
                    text = "".join(raw or prelude).strip()
                    raise CollectionError(text or "SSH 连接已关闭", text, process.returncode)
                size += len(line.encode("utf-8"))
                if size > MAX_OUTPUT:
                    raise CollectionError("npu-smi 输出超过 2 MiB 限制", "".join(raw or prelude))
                marker = line.strip()
                if marker == request + " BEGIN":
                    began = True
                    self._finish_auth()
                elif began and re.fullmatch(re.escape(request) + r" END \d+", marker):
                    rc = int(marker.rsplit(" ", 1)[1])
                    result = "".join(raw).strip()
                    if rc:
                        # A command error is framed correctly; keep the SSH session.
                        return result, rc
                    return result, 0
                elif began:
                    raw.append(line)
                else:
                    prelude.append(line)
        except CollectionError:
            self._disconnect()
            raise
        except (OSError, ValueError) as exc:
            self._disconnect()
            raise CollectionError(f"SSH 采集失败：{exc}", "".join(raw or prelude)) from exc


def demo_output(index, tick):
    rows = ["| npu-smi TEST.DEMO Version: TEST.DEMO |",
            "| NPU Name | Health | Power(W) Temp(C) Hugepages-Usage(page) |",
            "| Chip | Bus-Id | AICore(%) Memory-Usage(MB) HBM-Usage(MB) |"]
    for chip in range(8):
        util = int((math.sin(tick / 3 + chip) + 1) * 14 + 48) if index == 0 else (4 if chip < 2 else 0)
        used = (36000 + chip * 760) if index == 0 else (640 if chip < 2 else 0)
        rows += [f"| {chip} DemoNPU | OK | {165 + util * .8:.1f} {42 + util // 9} 0 / 0 |",
                 f"| 0 | 0000:{chip + 32:02x}:00.0 | {util} 1420 / 15137 {used} / 65536 |"]
    rows.append("| NPU Chip Process ID Process name Process memory(MB) |")
    if index == 0:
        rows += [f"| {chip} 0 {32000 + chip} python {35500 + chip * 760} |" for chip in range(8)]
    return "\n".join(rows)


class Worker:
    def __init__(self, monitor, node, identity, timeout, ssh, executable):
        self.monitor, self.node = monitor, node
        self.signature = (node, identity, timeout, executable)
        self.session = SSHSession(node, identity, timeout, ssh, executable)
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self.run, name=f"npu-{node.name}", daemon=True)
        self.tick = 0
        self.state = {"name": node.name, "host": node.host, "status": "pending",
                      "error": None, "warning": None, "exit_code": None, "raw": "",
                      "devices": [], "processes": [], "version": None, "history": [], "updated_at": None,
                      "last_success_at": None, "collecting": False, "duration_ms": None}

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        self.session.close()

    def run(self):
        while not self.stop_event.is_set():
            self.wake.clear()
            with self.monitor.lock:
                paused = self.monitor.paused
                interval = self.monitor.config["interval"]
            if paused:
                self.wake.wait()
                continue
            start = time.monotonic()
            with self.monitor.lock:
                self.state["collecting"] = True
            try:
                if self.monitor.demo:
                    index = 0 if self.node.name == "训练节点" else 1
                    raw, rc = demo_output(index, self.tick), 0
                    self.tick += 1
                else:
                    raw, rc = self.session.query()
                if rc:
                    raise CollectionError(f"npu-smi info 返回 {rc}，请查看原始输出", raw, rc)
                parsed = parse_info(raw)
                result = {**parsed, "raw": raw, "status": "warning" if parsed["warning"] else "ok",
                          "error": None, "exit_code": 0}
            except CollectionError as exc:
                result = {"status": "error", "error": str(exc), "warning": None,
                          "exit_code": exc.exit_code, "raw": exc.raw,
                          "devices": [], "processes": [], "version": None}
            except Exception as exc:
                result = {"status": "error", "error": f"采集失败：{exc}", "warning": None,
                          "exit_code": None, "raw": "", "devices": [], "processes": [], "version": None}
            if self.stop_event.is_set():
                break
            stamp = now()
            with self.monitor.lock:
                self.state.update(result, updated_at=stamp, collecting=False,
                                  duration_ms=round((time.monotonic() - start) * 1000))
                if result["status"] == "ok":
                    self.state["last_success_at"] = stamp
                values = [d["aicore_percent"] for d in result["devices"] if d["aicore_percent"] is not None]
                self.state["history"].append({"time": stamp, "value": round(sum(values) / len(values), 2) if values else None})
                self.state["history"] = self.state["history"][-120:]
                interval = self.monitor.config["interval"]
            if self.monitor.once:
                break
            # Start-to-start cadence; a slow request never overlaps another one.
            self.wake.wait(max(0.25, interval - (time.monotonic() - start)))


class Monitor:
    def __init__(self, config, path, demo=False, ssh="ssh", once=False):
        self.config, self.path, self.demo, self.ssh = config, path, demo, ssh
        self.lock, self.change_lock = threading.RLock(), threading.RLock()
        self.paused = False
        self.once = once
        self.workers = {}
        self.reconfigure(config, persist=False)

    def reconfigure(self, config, persist=True):
        with self.change_lock:
            if persist and not self.demo:
                # Replace atomically; private key contents are never read.
                temporary = self.path.with_name(self.path.name + ".tmp")
                temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                if os.name != "nt":
                    temporary.chmod(0o600)
                os.replace(temporary, self.path)
            with self.lock:
                old, new, created = self.workers, {}, []
                for entry in config["nodes"]:
                    node = Node(**entry)
                    identity = node.identity_file or config["identity_file"]
                    executable = node.npu_smi_path or config["npu_smi_path"]
                    signature = (node, identity, config["timeout"], executable)
                    worker = old.get(node.name)
                    if not worker or worker.signature != signature:
                        worker = Worker(self, node, identity, config["timeout"], self.ssh, executable)
                        created.append(worker)
                    new[node.name] = worker
                self.config, self.workers = config, new
                retired = [worker for worker in old.values() if worker not in new.values()]
            for worker in retired:
                worker.stop()
            for worker in created:
                worker.thread.start()
            for worker in new.values():
                if worker not in created:
                    worker.wake.set()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({"demo": self.demo, "paused": self.paused,
                                  "auth_password_supported": True,
                                  "config": public_config(self.config), "server_time": now(),
                                  "nodes": [w.state for w in self.workers.values()]})

    def control(self, action):
        with self.change_lock:
            with self.lock:
                if action == "pause":
                    self.paused = True
                elif action == "resume":
                    self.paused = False
                elif action != "refresh":
                    raise ValueError("不支持的操作")
                workers = list(self.workers.values())
            for worker in workers:
                worker.wake.set()

    def close(self):
        with self.change_lock:
            with self.lock:
                workers = list(self.workers.values())
            for worker in workers:
                worker.stop()
            for worker in workers:
                worker.thread.join(timeout=2)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, monitor):
        super().__init__(("127.0.0.1", port), Handler)
        self.monitor = monitor
        self.token = secrets.token_urlsafe(32)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.origin = "http://" + self.authority


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, data, content_type="application/json; charset=utf-8"):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, api=False):
        if self.headers.get("Host") != self.server.authority:
            self.reply(403, {"error": "仅允许本机访问"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self.reply(403, {"error": "请求来源无效"})
            return False
        if api and not hmac.compare_digest(self.headers.get("X-Monitor-Token", ""), self.server.token):
            self.reply(403, {"error": "请刷新本地看板后重试"})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.allowed(api=path.startswith("/api/")):
            return
        if path == "/api/state":
            self.reply(200, self.server.monitor.snapshot())
            return
        files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                 "/style.css": ("style.css", "text/css")}
        if path not in files:
            self.reply(404, {"error": "页面不存在"})
            return
        filename, content_type = files[path]
        data = (ROOT / "static" / filename).read_bytes()
        if path == "/":
            data = data.replace(b"__MONITOR_TOKEN__", self.server.token.encode("ascii"))
        self.reply(200, data, content_type + "; charset=utf-8")

    def do_POST(self):
        if not self.allowed(api=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 128 * 1024:
                raise ValueError("请求体大小无效")
            self.connection.settimeout(5)
            data = json.loads(self.rfile.read(length))
            path = urlsplit(self.path).path
            if path == "/api/config":
                with self.server.monitor.change_lock:
                    config = validate_config(data, self.server.monitor.path.parent, self.server.monitor.config)
                    self.server.monitor.reconfigure(config)
            elif path == "/api/control":
                if not isinstance(data, dict):
                    raise ValueError("请求必须是 JSON 对象")
                self.server.monitor.control(data.get("action"))
            else:
                self.reply(404, {"error": "接口不存在"})
                return
            self.reply(200, {"ok": True})
        except (ValueError, UnicodeError) as exc:
            self.reply(400, {"error": str(exc)})
        except OSError as exc:
            self.reply(500, {"error": f"无法保存配置：{exc}"})


def main():
    parser = argparse.ArgumentParser(description="本地 NPU 监控看板，无第三方依赖")
    parser.add_argument("--config", type=Path, default=ROOT / "nodes.json", help="节点配置 JSON")
    parser.add_argument("--node", action="append", default=[], help="追加节点，格式 [user@]host；可重复")
    parser.add_argument("--identity", help="所有节点默认使用的 SSH 私钥文件")
    parser.add_argument("--interval", type=float, help="监控周期，1–3600 秒")
    parser.add_argument("--timeout", type=float, help="单次采集总超时，1–300 秒")
    parser.add_argument("--npu-smi", help="远端 npu-smi 可执行文件路径")
    parser.add_argument("--port", type=int, default=8765, help="本地看板端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    parser.add_argument("--demo", action="store_true", help="演示模式，完全不连接 SSH")
    parser.add_argument("--once", action="store_true", help="采集一次并向标准输出打印 JSON")
    args = parser.parse_args()
    path = args.config.expanduser().resolve()
    try:
        if args.demo:
            data = {"interval": 5, "nodes": [{"name": "训练节点", "host": "demo-training"},
                                             {"name": "空闲节点", "host": "demo-idle"}]}
        else:
            data = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {"nodes": []}
        if not isinstance(data, dict):
            raise ValueError("配置必须是 JSON 对象")
        for field, value in [("identity_file", args.identity), ("interval", args.interval),
                             ("timeout", args.timeout), ("npu_smi_path", args.npu_smi)]:
            if value is not None:
                data[field] = value
        for target in args.node:
            user, host = target.rsplit("@", 1) if "@" in target else ("", target)
            data.setdefault("nodes", []).append({"name": host, "host": host, "user": user})
        config = validate_config(data, path.parent)
        if not args.demo and not shutil.which("ssh"):
            raise ValueError("未找到 ssh，请先启用系统 OpenSSH 客户端")
        if args.once and not config["nodes"]:
            raise ValueError("请至少配置一个节点")
        if not 0 <= args.port <= 65535:
            raise ValueError("看板端口须为 0–65535；0 表示自动分配")
    except (ValueError, OSError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    monitor = Monitor(config, path, args.demo, once=args.once)
    server = None
    try:
        if args.once:
            deadline = time.monotonic() + config["timeout"] + 5
            while time.monotonic() < deadline:
                snapshot = monitor.snapshot()
                if all(node["updated_at"] for node in snapshot["nodes"]):
                    break
                time.sleep(0.05)
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            return 0 if all(node["status"] == "ok" for node in snapshot["nodes"]) else 1
        server = DashboardServer(args.port, monitor)
        print(f"本地看板：{server.origin}", flush=True)
        print("演示模式：不连接任何节点" if args.demo else f"监控 {len(config['nodes'])} 个节点；周期 {config['interval']:g} 秒", flush=True)
        print("按 Ctrl+C 停止并关闭 SSH 连接", flush=True)
        if not args.no_browser:
            webbrowser.open(server.origin)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n监控已停止，SSH 连接已关闭。")
    except OSError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2
    finally:
        if server:
            server.server_close()
        monitor.close()
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
