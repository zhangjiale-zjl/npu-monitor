#!/usr/bin/env python3
"""Offline NPU dashboard. Python standard library + system OpenSSH only."""

import argparse
import copy
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import shlex
import shutil
import socket
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
from npu_platform import detect_platform, server_generation, system_product
from password_auth import PasswordBroker

ROOT = Path(__file__).resolve().parents[2]
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


@dataclass(frozen=True)
class Node:
    host: str
    user: str = ""
    port: int | None = None
    identity_file: str = ""
    auth_type: str = "key"
    password: str = field(default="", repr=False)
    address: str = ""
    ssh_port: int = 22

    @property
    def id(self):
        address = self.address or normalize_address(self.host)
        return f"[{address}]:{self.ssh_port}" if ":" in address else f"{address}:{self.ssh_port}"


def normalize_address(address):
    try:
        return str(ipaddress.ip_address(address.strip("[]")))
    except ValueError:
        return address.lower().rstrip(".")


def resolve_endpoint(host, user="", port=None):
    """Read local OpenSSH configuration without opening an SSH connection."""
    args = ["ssh", "-G", "-o", "CanonicalizeHostname=no"]
    if user:
        args += ["-l", user]
    if port:
        args += ["-p", str(port)]
    args.append(host)
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        result = subprocess.run(args, capture_output=True, timeout=3, **kwargs)
        if result.returncode == 0:
            fields = dict(line.split(None, 1) for line in result.stdout.decode("utf-8", "replace").splitlines() if " " in line)
            resolved_port = int(fields.get("port", port or 22))
            if 1 <= resolved_port <= 65535:
                return normalize_address(fields.get("hostname", host)), resolved_port
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return normalize_address(host), port or 22


def validate_config(data, base, current=None):
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")
    interval = bounded_number(data.get("interval", 5), "监控周期（秒）", 1, 3600)
    timeout = bounded_number(data.get("timeout", 15), "采集超时（秒）", 1, 300)
    identity = key_path(data.get("identity_file", ""), base)
    entries = data.get("nodes", [])
    if not isinstance(entries, list):
        raise ValueError("nodes 必须是数组")
    nodes, endpoints = [], set()
    resolved = {}
    def endpoint(host, user, port):
        key = (host, user, port)
        if key not in resolved:
            resolved[key] = resolve_endpoint(host, user, port)
        return resolved[key]
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
        port = entry.get("port")
        if port in (None, ""):
            port = None
        elif isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError(f"{host} 的端口须为 1–65535 的整数")
        address, ssh_port = endpoint(host, user, port)
        if (address, ssh_port) in endpoints:
            continue
        if len(nodes) >= MAX_NODES:
            raise ValueError(f"最多监控 {MAX_NODES} 个不同节点")
        endpoints.add((address, ssh_port))
        auth_type = entry.get("auth_type", "key")
        if auth_type not in ("key", "password"):
            raise ValueError(f"{host} 的认证方式须为 key 或 password")
        old = next((node for node in previous if node.get("user", "") == user and
                    endpoint(node["host"], node.get("user", ""), node.get("port")) == (address, ssh_port)), {})
        password = entry.get("password", old.get("password", ""))
        if not isinstance(password, str) or len(password.encode("utf-8")) > 4096 or any(c in password for c in "\r\n\x00"):
            raise ValueError(f"{host} 的密码须为不含换行或空字符的字符串，最多 4096 字节")
        if auth_type == "password" and not password:
            raise ValueError(f"请填写 {host} 的 SSH 登录密码")
        if auth_type == "key":
            password = ""
        identity_file = key_path(entry.get("identity_file", ""), base) if auth_type == "key" else ""
        nodes.append(Node(host=host, user=user, port=port, identity_file=identity_file,
                          auth_type=auth_type, password=password, address=address, ssh_port=ssh_port))
    return {"interval": interval, "timeout": timeout, "identity_file": identity,
            "nodes": [asdict(node) for node in nodes]}


def public_config(config):
    result = copy.deepcopy(config)
    for node in result["nodes"]:
        node["password_set"] = bool(node.pop("password", ""))
        node["id"] = Node(**{key: value for key, value in node.items() if key != "password_set"}).id
    return result


def stored_config(config):
    result = copy.deepcopy(config)
    for node in result["nodes"]:
        node.pop("address", None)
        node.pop("ssh_port", None)
    return result


class CollectionError(Exception):
    def __init__(self, message, raw="", exit_code=None, ssh_connected=False):
        super().__init__(message)
        self.raw = raw
        self.exit_code = exit_code
        self.ssh_connected = ssh_connected


class SSHSession:
    """A single SSH process carries successive framed npu-smi requests."""

    def __init__(self, node, identity, timeout, ssh="ssh"):
        self.node, self.identity, self.timeout, self.ssh = node, identity, timeout, ssh
        self.process = None
        self.lines = None
        self.lock = threading.Lock()
        self.request_lock = threading.Lock()
        self.closed = False
        self.broker = None
        self.address = None
        self.generation = 0

    def _start(self):
        # The remote command and the stdin protocol are fixed; node inputs never
        # enter a local shell or the remote shell script.
        script = (
            "PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/sbin:/usr/local/Ascend/driver/tools:$PATH; "
            "export PATH; LC_ALL=C; export LC_ALL; "
            "while read -r request npu chip scope; do "
            "case \"$request\" in NPU_MONITOR_*) "
            "printf '\\n%s BEGIN\\n' \"$request\"; "
            "printf 'NPU_MONITOR_ADDRESS %s\\n' \"$SSH_CONNECTION\"; "
            "npu-smi info </dev/null 2>&1; rc=$?; "
            "printf '\\n%s END %s\\n' \"$request\" \"$rc\";; "
            "NPU_BOARD_*) case \"$npu:$chip\" in *[!0-9:]*|:*|*:) exit 2;; esac; "
            "printf '\\n%s BEGIN\\n' \"$request\"; "
            "if [ \"$scope\" = device ]; then "
            "npu-smi info -t board -i \"$npu\" </dev/null 2>&1; "
            "else "
            "npu-smi info -t board -i \"$npu\" -c \"$chip\" </dev/null 2>&1; "
            "fi; rc=$?; printf '\\n%s END %s\\n' \"$request\" \"$rc\";; "
            "NPU_SYSTEM_*) printf '\\n%s BEGIN\\n' \"$request\"; "
            "printf 'NPU_MONITOR_SYSTEM_SOURCE dmidecode\\n'; "
            "dmidecode -s system-product-name </dev/null 2>&1; rc=$?; "
            "if [ \"$rc\" != 0 ] && [ -r /sys/class/dmi/id/product_name ]; then "
            "printf 'NPU_MONITOR_SYSTEM_SOURCE sysfs\\n'; "
            "cat /sys/class/dmi/id/product_name 2>&1; rc=$?; fi; "
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
            self.generation += 1
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

    def query(self, deadline=None):
        return self._request("NPU_MONITOR_" + secrets.token_hex(16), deadline=deadline)

    def query_board(self, device, deadline, device_scope=False):
        # IDs are parsed integers; only this fixed query can enter the protocol.
        npu, chip = int(device["npu_id"]), int(device["chip_id"])
        if npu < 0 or chip < 0:
            raise ValueError("NPU / Chip ID 无效")
        request = "NPU_BOARD_" + secrets.token_hex(16)
        scope = "device" if device_scope else "chip"
        return self._request(request, f" {npu} {chip} {scope}", deadline)

    def query_system(self, deadline):
        return self._request("NPU_SYSTEM_" + secrets.token_hex(16), deadline=deadline)

    def _request(self, request, arguments="", deadline=None):
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout
        if not self.request_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise CollectionError(f"等待 SSH 会话超过 {self.timeout:g} 秒，请稍后重试")
        try:
            return self._request_locked(request, arguments, deadline)
        finally:
            self.request_lock.release()

    def _request_locked(self, request, arguments, deadline):
        with self.lock:
            if self.closed:
                raise CollectionError("采集已停止")
            process, lines = self.process, self.lines
        raw, prelude, size = [], [], 0
        began = False
        try:
            if process is None:
                process, lines = self._start()
            process.stdin.write((request + arguments + "\n").encode("ascii"))
            process.stdin.flush()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CollectionError(f"采集超过 {self.timeout:g} 秒",
                                          "".join(raw or prelude), ssh_connected=began)
                try:
                    line = lines.get(timeout=remaining)
                except queue.Empty:
                    raise CollectionError(f"采集超过 {self.timeout:g} 秒",
                                          "".join(raw or prelude), ssh_connected=began) from None
                if line is None:
                    process.wait(timeout=5)
                    text = "".join(raw or prelude).strip()
                    raise CollectionError(text or "SSH 连接已关闭", text, process.returncode, began)
                size += len(line.encode("utf-8"))
                if size > MAX_OUTPUT:
                    raise CollectionError("npu-smi 输出超过 2 MiB 限制", "".join(raw or prelude), ssh_connected=began)
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
                    if line.startswith("NPU_MONITOR_ADDRESS "):
                        connection = line.split()
                        if len(connection) == 5:
                            try:
                                self.address = str(ipaddress.ip_address(connection[3]))
                            except ValueError:
                                pass
                    else:
                        raw.append(line)
                else:
                    prelude.append(line)
        except CollectionError:
            self._disconnect()
            raise
        except (OSError, ValueError) as exc:
            self._disconnect()
            raise CollectionError(f"SSH 采集失败：{exc}", "".join(raw or prelude), ssh_connected=began) from exc


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
    def __init__(self, monitor, node, identity, timeout, ssh):
        self.monitor, self.node = monitor, node
        self.signature = (node, identity, timeout)
        self.session = SSHSession(node, identity, timeout, ssh)
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self.run, name=f"npu-{node.id}", daemon=True)
        self.tick = 0
        self.next_platform_query = 0
        self.next_system_query = 0
        self.platform_generation = -1
        self.state = {"id": node.id, "name": f"未知环境 - {node.address or node.host}",
                      "host": node.host, "address": node.address or node.host, "platform": None,
                      "platform_error": None, "board_raw": "", "status": "pending",
                      "server_model": None, "server_model_source": None, "server_model_error": None,
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
            result = {}
            try:
                if self.monitor.demo:
                    index = list(self.monitor.workers).index(self.node.id)
                    raw, rc = demo_output(index, self.tick), 0
                    self.tick += 1
                else:
                    raw, rc = self.session.query()
                    if self.platform_generation != self.session.generation:
                        self.next_platform_query = 0
                        self.next_system_query = 0
                        self.platform_generation = self.session.generation
                address = self.session.address or self.node.address or self.node.host
                model = self.state["server_model"]
                deadline = start + self.session.timeout
                if self.monitor.demo:
                    model = f"演示服务器 {['A2', 'A3', 'A5'][index % 3]}"
                    result.update(server_model=model, server_model_source="demo", server_model_error=None)
                elif time.monotonic() >= self.next_system_query and deadline - time.monotonic() > 0.2:
                    try:
                        system_raw, system_rc = self.session.query_system(deadline)
                        if system_rc:
                            result["server_model_error"] = f"服务器型号查询返回 {system_rc}"
                        else:
                            model, source = system_product(system_raw)
                            result.update(server_model=model, server_model_source=source,
                                          server_model_error=None if model else "服务器型号暂未识别")
                    except CollectionError as exc:
                        result["server_model_error"] = f"服务器型号查询失败：{exc}"
                    self.next_system_query = float("inf") if model and not result.get("server_model_error") else time.monotonic() + 60
                platform = self.state["platform"] or server_generation(model)
                result.update(platform=platform, address=address, name=f"{platform or '未知环境'} - {address}")
                if rc:
                    raise CollectionError(f"npu-smi info 返回 {rc}，请查看原始输出", raw, rc)
                parsed = parse_info(raw)
                result.update({**parsed, "raw": raw, "status": "warning" if parsed["warning"] else "ok",
                               "error": None, "exit_code": 0})
                platform = detect_platform(parsed["devices"], self.state["board_raw"]) or platform
                if self.monitor.demo:
                    platform = ["A2", "A3", "A5"][index % 3]
                elif parsed["devices"] and time.monotonic() >= self.next_platform_query:
                    # Query once per connection, retry unresolved hardware at most once a minute.
                    if deadline - time.monotonic() > 0.2:
                        device = parsed["devices"][0]
                        device_scope = platform == "A5"
                        try:
                            board, board_rc = self.session.query_board(device, deadline, device_scope)
                            if board_rc and deadline - time.monotonic() > 0.2:
                                board, board_rc = self.session.query_board(device, deadline, not device_scope)
                            if board_rc:
                                result["platform_error"] = f"型号查询返回 {board_rc}"
                            else:
                                platform = detect_platform(parsed["devices"], board) or server_generation(model)
                                result.update(board_raw=board, platform_error=None if platform else "型号暂未识别")
                        except (CollectionError, ValueError) as exc:
                            result["platform_error"] = f"型号查询失败：{exc}"
                        self.next_platform_query = float("inf") if platform and not result.get("platform_error") else time.monotonic() + 60
                result.update(platform=platform, address=address,
                              name=f"{platform or '未知环境'} - {address}")
            except CollectionError as exc:
                result.update({"status": "error", "error": str(exc), "warning": None,
                          "exit_code": exc.exit_code, "raw": exc.raw,
                          "devices": [], "processes": [], "version": None})
            except Exception as exc:
                result.update({"status": "error", "error": f"采集失败：{exc}", "warning": None,
                               "exit_code": None, "raw": "", "devices": [], "processes": [], "version": None})
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
        self.test_slots = threading.BoundedSemaphore(4)
        self.test_sessions = set()
        self.closed = False
        self.reconfigure(config, persist=False)

    def reconfigure(self, config, persist=True):
        with self.change_lock:
            if persist and not self.demo:
                # Replace atomically; private key contents are never read.
                temporary = self.path.with_name(self.path.name + ".tmp")
                temporary.write_text(json.dumps(stored_config(config), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                if os.name != "nt":
                    temporary.chmod(0o600)
                os.replace(temporary, self.path)
            with self.lock:
                old, new, created = self.workers, {}, []
                for entry in config["nodes"]:
                    node = Node(**entry)
                    identity = (node.identity_file or config["identity_file"]) if node.auth_type == "key" else ""
                    signature = (node, identity, config["timeout"])
                    worker = old.get(node.id)
                    if not worker or worker.signature != signature:
                        worker = Worker(self, node, identity, config["timeout"], self.ssh)
                        created.append(worker)
                    new[node.id] = worker
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
            config = public_config(self.config)
            for node in config["nodes"]:
                node["name"] = self.workers[node["id"]].state["name"]
            return copy.deepcopy({"demo": self.demo, "paused": self.paused,
                                  "auth_password_supported": True,
                                  "auto_node_names": True,
                                  "connection_test_supported": True,
                                  "config": config, "server_time": now(),
                                  "nodes": [w.state for w in self.workers.values()]})

    def test_connection(self, data):
        if not isinstance(data, dict):
            raise ValueError("请求必须是 JSON 对象")
        if not self.test_slots.acquire(blocking=False):
            raise ValueError("已有 4 个节点正在测试，请稍后重试")
        session, owned = None, False
        started = time.monotonic()
        try:
            with self.lock:
                current = copy.deepcopy(self.config)
            config = validate_config({"timeout": data.get("timeout", current["timeout"]),
                                      "nodes": [data.get("node")]}, self.path.parent, current)
            node = Node(**config["nodes"][0])
            identity = ""
            if node.auth_type == "key":
                identity = node.identity_file or key_path(data.get("identity_file", current["identity_file"]), self.path.parent)
            if self.demo:
                return {"status": "warning", "ssh_ok": None, "npu_ok": None,
                        "message": "演示模式：不建立真实 SSH 连接", "detail": "", "exit_code": None}
            with self.lock:
                if self.closed:
                    raise ValueError("监控服务已停止")
                worker = self.workers.get(node.id)
                if worker and worker.signature == (node, identity, config["timeout"]):
                    session = worker.session
                else:
                    session = SSHSession(node, identity, config["timeout"], self.ssh)
                    owned = True
                    self.test_sessions.add(session)
            with session.lock:
                reused = not owned and session.process is not None and session.process.poll() is None
            try:
                raw, rc = session.query(deadline=time.monotonic() + config["timeout"])
                if rc:
                    result = {"status": "warning", "ssh_ok": True, "npu_ok": False,
                              "message": f"SSH 连接成功 · npu-smi info 返回 {rc}", "detail": raw, "exit_code": rc}
                else:
                    parsed = parse_info(raw)
                    devices = parsed["devices"]
                    unhealthy = sum(device["health"] != "OK" for device in devices)
                    message = f"SSH 连接成功 · 已读取 {len(devices)} 个 NPU / 芯片"
                    if unhealthy:
                        message += f" · {unhealthy} 个 Health 非 OK"
                    if parsed["warning"]:
                        message = "SSH 连接成功 · NPU 输出待确认"
                    result = {"status": "warning" if parsed["warning"] or unhealthy else "ok",
                              "ssh_ok": True, "npu_ok": not bool(parsed["warning"]),
                              "message": message, "detail": parsed["warning"] or "", "exit_code": 0}
            except (CollectionError, OSError, subprocess.SubprocessError) as exc:
                connected = getattr(exc, "ssh_connected", False)
                result = {"status": "warning" if connected else "error", "ssh_ok": connected, "npu_ok": False,
                          "message": "SSH 已连接 · NPU 命令未完成" if connected else "SSH 连接测试失败",
                          "detail": str(exc), "exit_code": getattr(exc, "exit_code", None)}
            if node.password:
                result["detail"] = result["detail"].replace(node.password, "[已隐藏]")
            result["detail"] = result["detail"][:4000]
            result["reused_connection"] = reused
            if reused:
                result["message"] += "（复用监控连接）"
            result["duration_ms"] = round((time.monotonic() - started) * 1000)
            return result
        finally:
            if owned and session:
                session.close()
                with self.lock:
                    self.test_sessions.discard(session)
            self.test_slots.release()

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
                self.closed = True
                workers = list(self.workers.values())
                test_sessions = list(self.test_sessions)
            for session in test_sessions:
                session.close()
            for worker in workers:
                worker.stop()
            for worker in workers:
                worker.thread.join(timeout=2)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

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
                    self.reply(200, {"ok": True, "duplicates_removed": len(data.get("nodes", [])) - len(config["nodes"])})
                    return
            elif path == "/api/control":
                if not isinstance(data, dict):
                    raise ValueError("请求必须是 JSON 对象")
                self.server.monitor.control(data.get("action"))
            elif path == "/api/test-connection":
                self.reply(200, self.server.monitor.test_connection(data))
                return
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
    parser.add_argument("--port", type=int, default=8765, help="本地看板端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    parser.add_argument("--demo", action="store_true", help="演示模式，完全不连接 SSH")
    parser.add_argument("--once", action="store_true", help="采集一次并向标准输出打印 JSON")
    args = parser.parse_args()
    path = args.config.expanduser().resolve()
    try:
        if args.demo:
            data = {"interval": 5, "nodes": [{"host": "demo-a2"}, {"host": "demo-a3"}, {"host": "demo-a5"}]}
        else:
            data = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {"nodes": []}
        if not isinstance(data, dict):
            raise ValueError("配置必须是 JSON 对象")
        for field, value in [("identity_file", args.identity), ("interval", args.interval),
                             ("timeout", args.timeout)]:
            if value is not None:
                data[field] = value
        for target in args.node:
            user, host = target.rsplit("@", 1) if "@" in target else ("", target)
            data.setdefault("nodes", []).append({"host": host, "user": user})
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
