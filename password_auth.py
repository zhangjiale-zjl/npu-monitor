"""Local OpenSSH askpass bridge. Credentials stay out of argv and environment."""

import hmac
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HELPERS = Path(__file__).resolve().parent / "helpers"
BUILD_LOCK = threading.Lock()


def helper_path():
    if os.name != "nt":
        path = HELPERS / "npu-askpass.sh"
        if not os.access(path, os.X_OK):
            path.chmod(path.stat().st_mode | 0o100)
        return path
    target = HELPERS / "npu-askpass.exe"
    with BUILD_LOCK:
        if target.is_file():
            return target
        windows = Path(os.environ.get("WINDIR", "C:/Windows"))
        compiler = next((path for path in (
            windows / "Microsoft.NET/Framework64/v4.0.30319/csc.exe",
            windows / "Microsoft.NET/Framework/v4.0.30319/csc.exe") if path.is_file()), None)
        if compiler is None:
            raise OSError("缺少 helpers/npu-askpass.exe，且未找到 Windows .NET Framework 编译器")
        temporary = target.with_name("npu-askpass-" + secrets.token_hex(8) + ".exe")
        try:
            result = subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:anycpu",
                                     "/out:" + str(temporary), str(HELPERS / "Askpass.cs")],
                                    capture_output=True, timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise OSError("无法生成本地密码辅助程序，请检查 helpers/Askpass.cs 与 .NET Framework")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


class PasswordBroker:
    """One authenticated, loopback-only password request per SSH connection."""

    def __init__(self, password):
        self.password = password
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.used = False
        self.server = None
        self.thread = None

    def start(self):
        helper = helper_path()
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.connection.settimeout(3)
                supplied = self.headers.get("X-Askpass-Token", "")
                allowed = (self.path == "/password" and not self.headers.get("Origin")
                           and hmac.compare_digest(supplied.encode("utf-8"), broker.token.encode("ascii")))
                with broker.lock:
                    allowed = allowed and not broker.used
                    if allowed:
                        broker.used = True
                        data = broker.password.encode("utf-8")
                    else:
                        data = b""
                self.send_response(200 if allowed else 403)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()
        return {"SSH_ASKPASS": str(helper), "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": "npu-monitor:0",
                "NPU_MONITOR_ASKPASS_PORT": str(self.server.server_port),
                "NPU_MONITOR_ASKPASS_TOKEN": self.token,
                "NPU_MONITOR_PYTHON": sys.executable,
                "NPU_MONITOR_ASKPASS_SCRIPT": str(HELPERS / "askpass.py")}

    def close(self):
        if self.server:
            if self.thread and self.thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
            if self.thread:
                self.thread.join(timeout=2)
            self.server = None
        with self.lock:
            self.password = ""
            self.token = ""

