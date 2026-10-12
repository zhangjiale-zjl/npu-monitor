import copy
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor import CollectionError, DashboardServer, Monitor, Node, SSHSession, validate_config
from password_auth import PasswordBroker, helper_path

TESTS = Path(__file__).parent
PASSWORD = "  密码$'`\\; abc  "  # Synthetic test value, never a node credential.


class PasswordTests(unittest.TestCase):
    def test_broker_requires_token_and_serves_only_once(self):
        broker = PasswordBroker(PASSWORD)
        env = broker.start()
        try:
            def request(token, origin=None):
                connection = http.client.HTTPConnection("127.0.0.1", int(env["NPU_MONITOR_ASKPASS_PORT"]), timeout=3)
                try:
                    headers = {"X-Askpass-Token": token}
                    if origin:
                        headers["Origin"] = origin
                    connection.request("GET", "/password", headers=headers)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()
            self.assertEqual(broker.server.server_address[0], "127.0.0.1")
            self.assertEqual(request("wrong"), (403, b""))
            self.assertEqual(request(env["NPU_MONITOR_ASKPASS_TOKEN"], "https://example.invalid"), (403, b""))
            self.assertEqual(request(env["NPU_MONITOR_ASKPASS_TOKEN"]), (200, PASSWORD.encode()))
            self.assertEqual(request(env["NPU_MONITOR_ASKPASS_TOKEN"]), (403, b""))
            self.assertNotIn(PASSWORD, env.values())
        finally:
            broker.close()
        self.assertEqual(broker.password, "")
        self.assertFalse(broker.thread.is_alive())

    def test_native_openssh_consumes_askpass_utf8_without_console(self):
        if not shutil.which("ssh-keygen"):
            self.skipTest("OpenSSH is unavailable")
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary:
            key = Path(temporary) / "encrypted-test-key"
            result = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", PASSWORD, "-f", str(key)],
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            broker = PasswordBroker(PASSWORD)
            try:
                env = {**os.environ, **broker.start()}
                result = subprocess.run(["ssh-keygen", "-y", "-f", str(key)], env=env,
                                        capture_output=True, timeout=10,
                                        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                self.assertTrue(result.stdout.startswith(b"ssh-ed25519 "))
                self.assertTrue(broker.used)
                self.assertNotIn(PASSWORD.encode(), result.stdout + result.stderr)
            finally:
                broker.close()

    def test_python_helper_preserves_password_and_ignores_proxies(self):
        broker = PasswordBroker(PASSWORD)
        try:
            env = {**os.environ, **broker.start(), "http_proxy": "http://127.0.0.1:9",
                   "HTTP_PROXY": "http://127.0.0.1:9", "NO_PROXY": "", "no_proxy": ""}
            helper = TESTS.parent / "helpers" / "askpass.py"
            result = subprocess.run([sys.executable, str(helper), "example-user@host's password:"],
                                    env=env, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, PASSWORD.encode() + b"\n")
            self.assertTrue(broker.used)
        finally:
            broker.close()

    def test_password_authentication_reuses_ssh_and_hides_secret(self):
        helper_path()  # Build once before patching the shared subprocess module.
        original = subprocess.Popen
        calls = []
        def spawn(args, **kwargs):
            calls.append((args, kwargs.get("env", {})))
            kwargs.pop("creationflags", None)
            return original([sys.executable, str(TESTS / "fake_ssh.py"), "password-ok"], **kwargs)
        session = SSHSession(Node("test", "host", auth_type="password", password=PASSWORD), "unused-key", 3)
        try:
            with patch("monitor.subprocess.Popen", side_effect=spawn):
                self.assertEqual(session.query()[1], 0)
                process = session.process
                self.assertEqual(session.query()[1], 0)
                self.assertIs(session.process, process)
            self.assertEqual(len(calls), 1)
            args, env = calls[0]
            self.assertIn("BatchMode=no", args)
            self.assertIn("PreferredAuthentications=password", args)
            self.assertIn("StrictHostKeyChecking=yes", args)
            self.assertIn("PubkeyAuthentication=no", args)
            self.assertNotIn("unused-key", args)
            self.assertNotIn(PASSWORD, " ".join(args))
            self.assertNotIn(PASSWORD, env.values())
            self.assertIsNone(session.broker)
            self.assertNotIn(PASSWORD, repr(session.node))
        finally:
            session.close()

    def test_wrong_password_reports_failure_and_closes_broker(self):
        helper_path()
        original = subprocess.Popen
        def spawn(args, **kwargs):
            kwargs.pop("creationflags", None)
            return original([sys.executable, str(TESTS / "fake_ssh.py"), "password-wrong"], **kwargs)
        session = SSHSession(Node("test", "host", auth_type="password", password=PASSWORD), "", 3)
        try:
            with patch("monitor.subprocess.Popen", side_effect=spawn), self.assertRaises(CollectionError) as context:
                session.query()
            self.assertIn("Permission denied", str(context.exception))
            self.assertNotIn(PASSWORD, str(context.exception))
            self.assertIsNone(session.process)
            self.assertIsNone(session.broker)
        finally:
            session.close()

    def test_rejects_missing_and_invalid_password(self):
        for value in ("", None, "line\nbreak", "nul\x00", "x" * 4097):
            with self.subTest(value=repr(value[:10]) if isinstance(value, str) else None):
                with self.assertRaises(ValueError):
                    validate_config({"nodes": [{"host": "host", "auth_type": "password", "password": value}]}, TESTS)

    def test_http_masks_preserves_replaces_and_clears_password(self):
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary:
            path = Path(temporary) / "nodes.json"
            monitor = Monitor(validate_config({"nodes": []}, TESTS), path, demo=True)
            monitor.control("pause")
            monitor.demo = False  # Paused: persist without opening any SSH connection.
            server = DashboardServer(0, monitor)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(method, route, data=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                try:
                    connection.request(method, route, json.dumps(data) if data is not None else None,
                                       {"X-Monitor-Token": server.token})
                    response = connection.getresponse()
                    body = response.read()
                    self.assertEqual(response.status, 200, body)
                    return json.loads(body)
                finally:
                    connection.close()
            try:
                data = {"nodes": [{"name": "node", "host": "host", "auth_type": "password", "password": PASSWORD}]}
                request("POST", "/api/config", data)
                state = request("GET", "/api/state")
                self.assertTrue(state["auth_password_supported"])
                self.assertTrue(state["config"]["nodes"][0]["password_set"])
                self.assertNotIn("password", state["config"]["nodes"][0])
                self.assertNotIn(PASSWORD, json.dumps(state, ensure_ascii=False))
                changed = copy.deepcopy(state["config"])
                changed["interval"] = 23
                changed["nodes"][0]["name"] = "renamed"
                request("POST", "/api/config", changed)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["nodes"][0]["password"], PASSWORD)
                changed["nodes"][0]["password"] = "replacement"
                request("POST", "/api/config", changed)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["nodes"][0]["password"], "replacement")
                changed = request("GET", "/api/state")["config"]
                changed["nodes"][0]["auth_type"] = "key"
                request("POST", "/api/config", changed)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["nodes"][0]["password"], "")
                self.assertFalse(request("GET", "/api/state")["config"]["nodes"][0]["password_set"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                monitor.close()


if __name__ == "__main__":
    unittest.main()
