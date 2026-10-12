import copy
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor import (CollectionError, DashboardServer, Monitor, Node, SSHSession,
                     demo_output, validate_config)
from npu_parser import memory, parse_info

TESTS = Path(__file__).parent
FIXTURE = (TESTS / "fixtures" / "npu-smi-synthetic.txt").read_text(encoding="utf-8")


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


class ParserTests(unittest.TestCase):
    def test_synthetic_devices_health_and_processes(self):
        result = parse_info(FIXTURE)
        self.assertIsNone(result["warning"])
        self.assertEqual(result["version"], "TEST.0")
        self.assertEqual(len(result["devices"]), 8)
        self.assertEqual(result["devices"][1]["health"], "Critical")
        self.assertEqual(result["devices"][0]["hbm"]["total_mb"], 65536)
        self.assertAlmostEqual(result["devices"][0]["hbm"]["percent"],
                               result["devices"][0]["hbm"]["used_mb"] / 65536 * 100, places=2)
        self.assertEqual(len(result["processes"]), 9)
        self.assertEqual(result["processes"][0]["name"], "demo-worker")
        self.assertIsNone(result["processes"][0]["container_pid"])

    def test_classic_table_processes_and_ansi(self):
        result = parse_info("\x1b[32m" + demo_output(0, 1) + "\x1b[0m")
        self.assertEqual(len(result["devices"]), 8)
        self.assertEqual(len(result["processes"]), 8)
        self.assertEqual(result["processes"][0]["memory_mb"], 35500)

    def test_container_pid_and_process_name_with_spaces(self):
        result = parse_info("| NPU Chip | Process ID | Process name | Process memory(MB) | Process id in container |\n"
                            "| 2 0 | 100 | worker name | 512 | 33 |")
        self.assertEqual(result["processes"][0], {"npu_id": 2, "chip_id": 0, "pid": 100,
                                                  "name": "worker name", "memory_mb": 512,
                                                  "container_pid": 33})

    def test_missing_hbm_and_reordered_metrics(self):
        result = parse_info("| NPU Name | Health | Temp(C) Power(W) |\n"
                            "| Chip | Bus-Id | Memory-Usage(MB) AICore(%) |\n"
                            "| 0 310P | OK | 31 24.5 |\n"
                            "| 1 | 0000:01:00.0 | 1024 / 8192 12.5 |")
        device = result["devices"][0]
        self.assertEqual(device["aicore_percent"], 12.5)
        self.assertEqual(device["power_w"], 24.5)
        self.assertEqual(device["memory"]["percent"], 12.5)
        self.assertIsNone(device["hbm"]["percent"])

    def test_unknown_metrics_are_not_zero(self):
        result = parse_info("| NPU Name | Health | Power(W) Temp(C) |\n"
                            "| Chip | Bus-Id | AICore(%) Memory-Usage(MB) HBM-Usage(MB) |\n"
                            "| 0 910B | NA | NA NA |\n"
                            "| 0 | NA | NA 0 / 0 NA |")
        device = result["devices"][0]
        self.assertIsNone(device["aicore_percent"])
        self.assertIsNone(device["hbm"]["percent"])
        self.assertIsNone(device["memory"]["percent"])
        self.assertIsNone(device["temperature_c"])

    def test_unrecognized_format_warns(self):
        self.assertTrue(parse_info("unsupported device output")["warning"])
        self.assertIsNone(memory("0 / 0")["percent"])


class ConfigTests(unittest.TestCase):
    def test_defaults_preserve_ssh_configuration(self):
        result = validate_config({"nodes": [{"host": "alias"}]}, TESTS)
        self.assertEqual(result["interval"], 5)
        self.assertEqual(result["npu_smi_path"], "npu-smi")
        self.assertEqual(result["nodes"][0]["identity_file"], "")
        self.assertEqual(result["nodes"][0]["user"], "")
        self.assertIsNone(result["nodes"][0]["port"])

    def test_invalid_configuration_rejected(self):
        cases = [{"interval": 0}, {"interval": float("nan")}, {"timeout": True},
                 {"nodes": [{"host": "-oProxyCommand=bad"}]},
                 {"nodes": [{"host": "host;bad"}]},
                 {"nodes": [{"host": "host", "port": True}]},
                 {"nodes": [{"host": "host", "port": 70000}]},
                 {"nodes": [{"host": "host", "user": "a b"}]},
                 {"nodes": [{"host": "a", "name": "same"}, {"host": "b", "name": "same"}]},
                 {"identity_file": "id.pub"}, {"npu_smi_path": "npu-smi;bad"}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                validate_config(case, TESTS)

    def test_relative_private_key_and_per_node_override(self):
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary:
            base = Path(temporary)
            (base / "default-key").write_text("test placeholder")
            (base / "node-key").write_text("test placeholder")
            result = validate_config({"identity_file": "default-key", "nodes": [
                {"host": "host", "identity_file": "node-key", "npu_smi_path": "/opt/npu/bin/npu-smi"}]}, base)
            self.assertEqual(result["identity_file"], str((base / "default-key").resolve()))
            self.assertEqual(result["nodes"][0]["identity_file"], str((base / "node-key").resolve()))


class TransportTests(unittest.TestCase):
    def session(self, modes, timeout=2):
        original = subprocess.Popen
        calls = []
        modes = iter(modes)
        def spawn(args, **kwargs):
            calls.append(args)
            kwargs.pop("creationflags", None)
            return original([sys.executable, str(TESTS / "fake_ssh.py"), next(modes)], **kwargs)
        patcher = patch("monitor.subprocess.Popen", side_effect=spawn)
        patcher.start()
        self.addCleanup(patcher.stop)
        session = SSHSession(Node("test", "host", "example-user", 2222), "chosen-key", timeout)
        self.addCleanup(session.close)
        return session, calls

    def test_reuses_connection_and_closes_process(self):
        session, calls = self.session(["ok"])
        raw, rc = session.query()
        process = session.process
        raw2, rc2 = session.query()
        self.assertEqual((rc, rc2), (0, 0))
        self.assertEqual(raw, raw2)
        self.assertEqual(len(calls), 1)
        self.assertIs(session.process, process)
        self.assertIn("StrictHostKeyChecking=yes", calls[0])
        self.assertIn("IdentitiesOnly=yes", calls[0])
        self.assertIn("chosen-key", calls[0])
        self.assertIn("ClearAllForwardings=yes", calls[0])
        session.close()
        self.assertIsNotNone(process.poll())

    def test_command_failure_keeps_session(self):
        session, calls = self.session(["command-error"])
        self.assertEqual(session.query()[1], 127)
        self.assertEqual(session.query()[1], 127)
        self.assertEqual(len(calls), 1)

    def test_disconnect_reconnects(self):
        session, calls = self.session(["disconnect", "ok"])
        with self.assertRaises(CollectionError) as context:
            session.query()
        self.assertEqual(context.exception.exit_code, 255)
        self.assertIn("Permission denied", str(context.exception))
        self.assertIsNone(session.process)
        self.assertEqual(session.query()[1], 0)
        self.assertEqual(len(calls), 2)

    def test_timeout_kills_connection_then_recovers(self):
        session, calls = self.session(["timeout", "ok"], timeout=.3)
        with self.assertRaises(CollectionError) as context:
            session.query()
        self.assertIn("超过", str(context.exception))
        self.assertIsNone(session.process)
        self.assertEqual(session.query()[1], 0)
        self.assertEqual(len(calls), 2)

    def test_large_output_is_bounded(self):
        session, _ = self.session(["oversize"])
        with self.assertRaises(CollectionError) as context:
            session.query()
        self.assertIn("2 MiB", str(context.exception))


class WorkerTests(unittest.TestCase):
    def test_once_collects_exactly_one_sample(self):
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary:
            config = validate_config({"interval": 1, "nodes": [{"host": "demo"}]}, TESTS)
            monitor = Monitor(config, Path(temporary) / "nodes.json", demo=True, once=True)
            try:
                worker = monitor.workers["demo"]
                worker.thread.join(timeout=2)
                self.assertFalse(worker.thread.is_alive())
                self.assertEqual(worker.tick, 1)
                self.assertEqual(len(monitor.snapshot()["nodes"][0]["history"]), 1)
            finally:
                monitor.close()

    def test_interval_pause_and_runtime_reconfigure(self):
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary:
            config = validate_config({"interval": 1, "nodes": [{"name": "训练节点", "host": "demo"}]}, TESTS)
            monitor = Monitor(config, Path(temporary) / "nodes.json", demo=True)
            try:
                wait_until(lambda: monitor.snapshot()["nodes"][0]["updated_at"] is not None)
                worker = monitor.workers["训练节点"]
                monitor.control("pause")
                time.sleep(.08)
                tick = worker.tick
                time.sleep(1.05)
                self.assertEqual(worker.tick, tick)
                changed = copy.deepcopy(config)
                changed["interval"] = 3
                monitor.reconfigure(changed)
                self.assertIs(monitor.workers["训练节点"], worker)
                monitor.control("resume")
                wait_until(lambda: worker.tick > tick)
                monitor.reconfigure(validate_config({"nodes": []}, TESTS))
                self.assertEqual(monitor.snapshot()["nodes"], [])
                wait_until(lambda: not worker.thread.is_alive())
            finally:
                monitor.close()

    def test_one_node_failure_does_not_hide_healthy_node(self):
        config = validate_config({"interval": 5, "nodes": [{"host": "good"}, {"host": "bad"}]}, TESTS)
        def query(session):
            if session.node.host == "bad":
                raise CollectionError("host down", "connection failed", 255)
            return FIXTURE, 0
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary, patch.object(SSHSession, "query", query):
            monitor = Monitor(config, Path(temporary) / "nodes.json")
            try:
                wait_until(lambda: all(n["updated_at"] for n in monitor.snapshot()["nodes"]))
                good, bad = monitor.snapshot()["nodes"]
                self.assertEqual(good["status"], "ok")
                self.assertEqual(len(good["devices"]), 8)
                self.assertEqual(bad["status"], "error")
                self.assertEqual(bad["devices"], [])
                self.assertEqual(bad["exit_code"], 255)
            finally:
                monitor.close()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=TESTS)
        self.path = Path(self.temporary.name) / "nodes.json"
        self.monitor = Monitor(validate_config({"nodes": []}, TESTS), self.path)
        self.server = DashboardServer(0, self.monitor)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.monitor.close()
        self.temporary.cleanup()

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            conn.request(method, path, json.dumps(data) if data is not None else None, headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_local_assets_and_authenticated_api(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("GET", "/api/state")[0], 403)
        status, _, body = self.request("GET", "/api/state", headers={"X-Monitor-Token": self.server.token})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["nodes"], [])

    def test_cross_origin_host_and_invalid_config_rejected(self):
        token = {"X-Monitor-Token": self.server.token}
        self.assertEqual(self.request("POST", "/api/config", {"nodes": []},
                                     {**token, "Origin": "https://example.invalid"})[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Host": "example.invalid"})[0], 403)
        self.assertEqual(self.request("POST", "/api/config", {"interval": 0}, token)[0], 400)
        self.assertFalse(self.path.exists())

    def test_saves_and_applies_configuration(self):
        token = {"X-Monitor-Token": self.server.token}
        self.assertEqual(self.request("POST", "/api/config", {"interval": 9, "nodes": []}, token)[0], 200)
        self.assertEqual(json.loads(self.path.read_text())["interval"], 9)
        self.assertEqual(self.monitor.snapshot()["config"]["interval"], 9)
        self.assertEqual(self.request("POST", "/api/control", {"action": "pause"}, token)[0], 200)
        self.assertTrue(self.monitor.paused)

    def test_manual_node_addition_is_persisted_and_monitored(self):
        token = {"X-Monitor-Token": self.server.token}
        config = {"interval": 3600, "nodes": [
            {"name": "first", "host": "node-one"},
            {"name": "second", "host": "node-two", "user": "example-user", "port": 2222}]}
        with patch.object(SSHSession, "query", return_value=(FIXTURE, 0)):
            try:
                self.assertEqual(self.request("POST", "/api/config", config, token)[0], 200)
                wait_until(lambda: all(node["updated_at"] for node in self.monitor.snapshot()["nodes"]))
                saved = validate_config(json.loads(self.path.read_text()), self.path.parent)
                self.assertEqual([node["name"] for node in saved["nodes"]], ["first", "second"])
                self.assertEqual(saved["nodes"][1]["port"], 2222)
                status, _, body = self.request("GET", "/api/state", headers=token)
                self.assertEqual(status, 200)
                nodes = json.loads(body)["nodes"]
                self.assertEqual(len(nodes), 2)
                self.assertTrue(all(len(node["devices"]) == 8 for node in nodes))
                self.assertEqual(nodes[1]["devices"][1]["health"], "Critical")
            finally:
                self.monitor.close()


if __name__ == "__main__":
    unittest.main()
