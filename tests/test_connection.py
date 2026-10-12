import copy
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "src" / "python"
sys.path.insert(0, str(SOURCE))
from monitor import CollectionError, DashboardServer, Monitor, Node, SSHSession, validate_config

TESTS = Path(__file__).parent
PASSWORD = "  密码$'`\\; abc  "  # Synthetic fixture credential.


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=TESTS)
        self.base = Path(self.temporary.name)
        self.path = self.base / "nodes.json"
        self.monitor = Monitor(validate_config({"nodes": []}, self.base), self.path)
        self.monitor.paused = True
        self.calls = []
        self.processes = []
        original = subprocess.Popen

        def spawn(args, **kwargs):
            self.calls.append(args)
            kwargs.pop("creationflags", None)
            process = original([sys.executable, str(TESTS / "fake_ssh.py"), self.mode], **kwargs)
            self.processes.append(process)
            return process

        self.mode = "ok"
        self.spawn_patch = patch("monitor.subprocess.Popen", side_effect=spawn)
        self.endpoint_patch = patch("monitor.resolve_endpoint", side_effect=lambda host, user="", port=None: (host, port or 22))
        self.spawn_patch.start()
        self.endpoint_patch.start()

    def tearDown(self):
        self.monitor.close()
        self.spawn_patch.stop()
        self.endpoint_patch.stop()
        self.temporary.cleanup()

    def probe(self, **kwargs):
        return self.monitor.test_connection({"node": {"host": "test-node", **kwargs}})

    def test_unsaved_node_is_tested_and_session_is_closed(self):
        before = copy.deepcopy(self.monitor.config)
        result = self.probe()
        self.assertTrue(result["ssh_ok"])
        self.assertTrue(result["npu_ok"])
        self.assertFalse(result["reused_connection"])
        self.assertEqual(result["status"], "warning")  # Fixture has one Critical card.
        self.assertIn("8", result["message"])
        self.assertIn("1 个 Health 非 OK", result["message"])
        self.assertEqual(self.monitor.config, before)
        self.assertTrue(self.monitor.paused)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.monitor.workers)
        self.assertFalse(self.monitor.test_sessions)
        self.assertIsNotNone(self.processes[0].poll())

    def test_existing_session_is_reused_without_changing_history(self):
        config = validate_config({"nodes": [{"host": "test-node"}]}, self.base)
        self.monitor.reconfigure(config, persist=False)
        worker = next(iter(self.monitor.workers.values()))
        worker.session.query()
        process = worker.session.process
        before = self.monitor.snapshot()["nodes"]
        result = self.probe()
        self.assertTrue(result["ssh_ok"])
        self.assertTrue(result["reused_connection"])
        self.assertEqual(len(self.calls), 1)
        self.assertIs(worker.session.process, process)
        self.assertFalse(worker.session.closed)
        self.assertEqual(self.monitor.snapshot()["nodes"], before)

    def test_node_key_overrides_default_and_unused_default_is_ignored(self):
        key = self.base / "node-key"
        key.touch()
        result = self.monitor.test_connection({"node": {"host": "test-node", "identity_file": str(key)},
                                               "identity_file": "missing-default-key"})
        self.assertTrue(result["ssh_ok"])
        self.assertIn(str(key), self.calls[0])
        self.assertIn("IdentitiesOnly=yes", self.calls[0])

    def test_saved_password_is_used_without_exposing_or_replacing_it(self):
        self.mode = "password-ok"
        key = self.base / "default-key"
        key.touch()
        config = validate_config({"identity_file": str(key), "nodes": [
            {"host": "test-node", "user": "example-user", "auth_type": "password", "password": PASSWORD}]}, self.base)
        self.monitor.reconfigure(config, persist=False)
        before = copy.deepcopy(self.monitor.config)
        result = self.monitor.test_connection({"node": {"host": "test-node", "user": "example-user", "auth_type": "password"},
                                               "identity_file": "unused-missing-key"})
        self.assertTrue(result["ssh_ok"])
        self.assertNotIn(PASSWORD, json.dumps(result))
        self.assertEqual(self.monitor.config, before)
        self.assertIn("PreferredAuthentications=password", self.calls[0])
        self.assertNotIn("-i", self.calls[0])
        self.assertFalse(next(iter(self.monitor.workers.values())).session.closed)

    def test_command_error_is_distinct_from_ssh_failure(self):
        self.mode = "command-error"
        result = self.probe()
        self.assertTrue(result["ssh_ok"])
        self.assertFalse(result["npu_ok"])
        self.assertEqual(result["exit_code"], 127)
        self.assertIn("command not found", result["detail"])
        self.mode = "disconnect"
        result = self.probe()
        self.assertFalse(result["ssh_ok"])
        self.assertEqual(result["exit_code"], 255)
        self.assertIn("Permission denied", result["detail"])

    def test_command_timeout_remembers_ssh_was_connected(self):
        self.mode = "command-timeout"
        result = self.monitor.test_connection({"node": {"host": "test-node"}, "timeout": 1})
        self.assertTrue(result["ssh_ok"])
        self.assertFalse(result["npu_ok"])
        self.assertEqual(result["status"], "warning")
        self.assertIsNotNone(self.processes[0].poll())

    def test_missing_password_and_public_key_path_fail_before_connecting(self):
        with self.assertRaises(ValueError):
            self.probe(auth_type="password")
        with self.assertRaises(ValueError):
            self.probe(identity_file="test.pub")
        self.assertEqual(self.calls, [])

    def test_password_is_redacted_even_if_an_error_echoes_it(self):
        with patch.object(SSHSession, "query", side_effect=CollectionError("Error: " + PASSWORD, exit_code=255)):
            result = self.probe(auth_type="password", password=PASSWORD)
        self.assertNotIn(PASSWORD, json.dumps(result, ensure_ascii=False))
        self.assertIn("[已隐藏]", result["detail"])

    def test_demo_and_busy_tests_do_not_open_connections(self):
        self.monitor.demo = True
        self.assertIsNone(self.probe()["ssh_ok"])
        self.monitor.demo = False
        for _ in range(4):
            self.monitor.test_slots.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "正在测试"):
                self.probe()
        finally:
            for _ in range(4):
                self.monitor.test_slots.release()
        self.assertEqual(self.calls, [])

    def test_concurrent_queries_share_one_session_without_mixing_frames(self):
        self.mode = "slow"
        session = SSHSession(Node("test-node"), "", 2)
        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(lambda _: session.query(), range(6)))
            self.assertEqual(len(self.calls), 1)
            self.assertTrue(all(rc == 0 and "TEST.0" in raw for raw, rc in results))
            self.assertEqual(session.query()[1], 0)
        finally:
            session.close()

    def test_waiting_request_timeout_keeps_running_session_alive(self):
        self.mode = "slow"
        session = SSHSession(Node("test-node"), "", 2)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(session.query)
                deadline = time.monotonic() + 1
                while not session.request_lock.locked() and time.monotonic() < deadline:
                    time.sleep(.001)
                with self.assertRaisesRegex(CollectionError, "等待 SSH"):
                    session.query(deadline=time.monotonic() + .005)
                self.assertEqual(future.result()[1], 0)
            self.assertEqual(session.query()[1], 0)
            self.assertEqual(len(self.calls), 1)
        finally:
            session.close()

    def test_http_probe_requires_token_and_does_not_persist(self):
        server = DashboardServer(0, self.monitor)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def request(headers):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                try:
                    conn.request("POST", "/api/test-connection", json.dumps({"node": {"host": "test-node"}}), headers)
                    response = conn.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    conn.close()
            self.assertEqual(request({})[0], 403)
            token = {"X-Monitor-Token": server.token}
            self.assertEqual(request({**token, "Origin": "https://example.invalid"})[0], 403)
            status, result = request(token)
            self.assertEqual(status, 200)
            self.assertTrue(result["ssh_ok"])
            self.assertFalse(self.path.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
