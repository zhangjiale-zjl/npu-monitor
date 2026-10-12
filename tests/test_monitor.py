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

SOURCE = Path(__file__).resolve().parents[1] / "src" / "python"
sys.path.insert(0, str(SOURCE))
from monitor import (CollectionError, DashboardServer, Monitor, Node, SSHSession,
                     demo_output, normalize_address, resolve_endpoint, validate_config)
from npu_parser import memory, parse_info
from npu_platform import detect_platform, model_generation, server_generation, system_product

TESTS = Path(__file__).parent
FIXTURE = (TESTS / "fixtures" / "npu-smi-synthetic.txt").read_text(encoding="utf-8")
A3_FIXTURE = (TESTS / "fixtures" / "npu-smi-a3-synthetic.txt").read_text(encoding="utf-8")


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


class ParserTests(unittest.TestCase):
    def test_a3_eight_cards_sixteen_independent_dies(self):
        result = parse_info(A3_FIXTURE)
        devices = result["devices"]
        self.assertIsNone(result["warning"])
        self.assertEqual(len(devices), 16)
        self.assertEqual({device["npu_id"] for device in devices}, set(range(8)))
        self.assertEqual([(device["npu_id"], device["chip_id"]) for device in devices],
                         [(card, die) for card in range(8) for die in range(2)])
        self.assertEqual([device["phy_id"] for device in devices], list(range(16)))
        self.assertTrue(all(device["device_id"] is None for device in devices))
        self.assertEqual([device["aicore_percent"] for device in devices[:2]], [10, 20])
        self.assertEqual([device["health"] for device in devices[:2]], ["OK", "Critical"])
        self.assertEqual([device["hbm"]["used_mb"] for device in devices],
                         [1024 * (index + 1) for index in range(16)])
        self.assertEqual(devices[0]["hbm"]["percent"], 1.56)
        self.assertEqual(devices[1]["hbm"]["percent"], 3.12)
        self.assertEqual(devices[0]["power_w"], 100)
        self.assertIsNone(devices[1]["power_w"])
        self.assertEqual(devices[-1]["bus_id"], "0000:2f:00.0")
        self.assertEqual(len(result["processes"]), 17)
        self.assertEqual({(process["npu_id"], process["chip_id"]) for process in result["processes"]},
                         {(device["npu_id"], device["chip_id"]) for device in devices})

    def test_chip_device_column_and_noncontiguous_ids(self):
        result = parse_info("| NPU Name | Health | Power(W) Temp(C) |\n"
                            "| Chip Device | Bus-Id | AICore(%) HBM-Usage(MB) |\n"
                            "| 7 DemoNPU | OK | 123 40 |\n"
                            "| 1 42 | NA | 75 8192 / 32768 |")
        self.assertIsNone(result["warning"])
        device = result["devices"][0]
        self.assertEqual((device["npu_id"], device["chip_id"], device["device_id"]), (7, 1, 42))
        self.assertIsNone(device["phy_id"])
        self.assertEqual(device["hbm"]["percent"], 25)

    def test_incomplete_die_warns_instead_of_reporting_complete_success(self):
        result = parse_info(A3_FIXTURE.replace("| 1 15 |", "| unsupported |"))
        self.assertEqual(len(result["devices"]), 15)
        self.assertIn("1 条", result["warning"])

    def test_demo_a3_has_both_dies_per_card(self):
        devices = parse_info(demo_output(1, 1))["devices"]
        self.assertEqual(len(devices), 16)
        self.assertEqual({device["npu_id"] for device in devices}, set(range(8)))
        self.assertEqual([device["phy_id"] for device in devices], list(range(16)))

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
    def test_same_endpoint_with_different_names_users_and_credentials_is_merged(self):
        data = {"nodes": [{"host": "HOST", "name": "first", "user": "first-user"},
                          {"host": "host", "name": "second", "user": "other-user", "port": 22}]}
        result = validate_config(data, TESTS)
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["nodes"][0]["user"], "first-user")
        self.assertNotIn("name", result["nodes"][0])

    def test_ssh_aliases_are_merged_and_ports_remain_distinct(self):
        with patch("monitor.resolve_endpoint", side_effect=lambda host, user, port: ("127.0.0.1", port)):
            result = validate_config({"nodes": [{"host": "alias"}, {"host": "127.0.0.1", "port": 22},
                                                 {"host": "another-alias", "port": 2200}]}, TESTS)
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual([node["ssh_port"] for node in result["nodes"]], [22, 2200])

    def test_limit_applies_after_deduplication(self):
        data = {"nodes": [{"host": f"synthetic-node-{index}"} for index in range(64)]}
        with patch("monitor.resolve_endpoint", side_effect=lambda host, user, port: (host, port or 22)):
            data["nodes"].append({"host": "synthetic-node-0", "name": "different-label"})
            self.assertEqual(len(validate_config(data, TESTS)["nodes"]), 64)
            data["nodes"].append({"host": "synthetic-extra-node"})
            with self.assertRaises(ValueError):
                validate_config(data, TESTS)

    def test_password_is_preserved_by_endpoint_and_user_and_not_by_name(self):
        with patch("monitor.resolve_endpoint", return_value=("127.0.0.1", 22)):
            current = validate_config({"nodes": [{"host": "alias-one", "user": "account", "auth_type": "password", "password": "synthetic-password"}]}, TESTS)
            result = validate_config({"nodes": [{"host": "alias-two", "user": "account", "auth_type": "password"}]}, TESTS, current)
            self.assertEqual(result["nodes"][0]["password"], "synthetic-password")
            with self.assertRaises(ValueError):
                validate_config({"nodes": [{"host": "alias-two", "user": "other-account", "auth_type": "password"}]}, TESTS, current)
        with self.assertRaises(ValueError):
            validate_config({"nodes": [{"host": "new-host", "name": "same", "auth_type": "password"}]}, TESTS,
                            {"nodes": [{"host": "old-host", "name": "same", "auth_type": "password", "password": "synthetic-password"}]})

    def test_ipv6_and_hostname_normalization(self):
        self.assertEqual(normalize_address("0:0:0:0:0:0:0:1"), "::1")
        self.assertEqual(normalize_address("EXAMPLE-NODE."), "example-node")

    def test_resolver_reads_effective_ssh_hostname_and_port(self):
        reply = subprocess.CompletedProcess([], 0, b"hostname 127.0.0.1\nport 2200\n", b"")
        with patch("monitor.subprocess.run", return_value=reply) as run:
            self.assertEqual(resolve_endpoint("alias", "account", 2200), ("127.0.0.1", 2200))
        self.assertEqual(run.call_args.args[0], ["ssh", "-G", "-o", "CanonicalizeHostname=no", "-l", "account", "-p", "2200", "alias"])

    def test_defaults_use_root_and_22_with_ssh_key_inheritance(self):
        result = validate_config({"nodes": [{"host": "alias"}]}, TESTS)
        self.assertEqual(result["interval"], 5)
        self.assertNotIn("npu_smi_path", result)
        self.assertEqual(result["nodes"][0]["identity_file"], "")
        self.assertEqual(result["nodes"][0]["user"], "root")
        self.assertEqual(result["nodes"][0]["port"], 22)

    def test_blank_and_explicit_login_defaults_are_identical(self):
        with patch("monitor.resolve_endpoint", return_value=("127.0.0.1", 22)) as resolve:
            variants = [{}, {"user": "", "port": None}, {"user": "  ", "port": ""}, {"user": "root", "port": 22}]
            configs = [validate_config({"nodes": [{"host": "alias", **values}]}, TESTS) for values in variants]
            self.assertTrue(all(config == configs[0] for config in configs))
            self.assertTrue(all(call.args == ("alias", "root", 22) for call in resolve.call_args_list))

    def test_manual_order_keeps_live_ids_and_removes_duplicates_and_deleted_nodes(self):
        data = {"nodes": [{"host": "order-first"}, {"host": "order-second"}],
            "node_order": {"Synthetic Model": ["order-second:22", "deleted-node:22", "order-second:22", "order-first:22"],
                           "Removed Model": ["deleted-node:22"]}}
        with patch("monitor.resolve_endpoint", side_effect=lambda host, user, port: (host, port)):
            config = validate_config(data, TESTS)
        self.assertEqual(config["node_order"], {"Synthetic Model": ["order-second:22", "order-first:22"]})

    def test_invalid_manual_order_is_rejected(self):
        for order in [[], {"": []}, {"Model": "node"}, {"Model": [1]}, {"Model": ["node"] * 65}]:
            with self.subTest(order=order), self.assertRaises(ValueError):
                validate_config({"node_order": order}, TESTS)

    def test_model_order_is_deduplicated_and_default_is_alphabetical(self):
        self.assertEqual(validate_config({}, TESTS)["model_order"], [])
        config = validate_config({"model_order": ["Synthetic Z", "Synthetic A", "Synthetic Z"]}, TESTS)
        self.assertEqual(config["model_order"], ["Synthetic Z", "Synthetic A"])

    def test_invalid_model_order_is_rejected(self):
        for order in [{}, "Model", [None], [""], [" "], ["X" * 201], ["Model"] * 65]:
            with self.subTest(order=order), self.assertRaises(ValueError):
                validate_config({"model_order": order}, TESTS)

    def test_default_login_overrides_ssh_user_and_port_configuration(self):
        reply = subprocess.CompletedProcess([], 0, b"hostname 127.0.0.1\nuser other-account\nport 2200\n", b"")
        with patch("monitor.subprocess.run", return_value=reply) as run:
            self.assertEqual(resolve_endpoint("alias", "", None), ("127.0.0.1", 22))
        self.assertEqual(run.call_args.args[0], ["ssh", "-G", "-o", "CanonicalizeHostname=no", "-l", "root", "-p", "22", "alias"])

    def test_default_login_password_is_preserved_with_blank_and_explicit_fields(self):
        with patch("monitor.resolve_endpoint", return_value=("127.0.0.1", 22)):
            current = validate_config({"nodes": [{"host": "alias", "user": "", "port": None,
                "auth_type": "password", "password": "synthetic-default-password"}]}, TESTS)
            legacy = copy.deepcopy(current)
            legacy["nodes"][0].update(user="", port=None)
            for previous in [current, legacy]:
                result = validate_config({"nodes": [{"host": "alias", "user": "root", "port": 22,
                    "auth_type": "password"}]}, TESTS, previous)
                self.assertEqual(result["nodes"][0]["password"], "synthetic-default-password")

    def test_invalid_configuration_rejected(self):
        cases = [{"interval": 0}, {"interval": float("nan")}, {"timeout": True},
                 {"nodes": [{"host": "-oProxyCommand=bad"}]},
                 {"nodes": [{"host": "host;bad"}]},
                 {"nodes": [{"host": "host", "port": True}]},
                 {"nodes": [{"host": "host", "port": 70000}]},
                 {"nodes": [{"host": "host", "user": "a b"}]},
                 {"identity_file": "id.pub"}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                validate_config(case, TESTS)

    def test_relative_private_key_and_per_node_override(self):
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary:
            base = Path(temporary)
            (base / "default-key").write_text("test placeholder")
            (base / "node-key").write_text("test placeholder")
            result = validate_config({"identity_file": "default-key", "nodes": [
                {"host": "host", "identity_file": "node-key"}]}, base)
            self.assertEqual(result["identity_file"], str((base / "default-key").resolve()))
            self.assertEqual(result["nodes"][0]["identity_file"], str((base / "node-key").resolve()))

    def test_command_path_fields_are_not_part_of_connection_config(self):
        result = validate_config({"npu_smi_path": "/unused/old-command", "nodes": [
            {"host": "host", "npu_smi_path": "/unused/node-command"}]}, TESTS)
        self.assertNotIn("npu_smi_path", result)
        self.assertNotIn("npu_smi_path", result["nodes"][0])


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
        session = SSHSession(Node("host", "example-user", 2222), "chosen-key", timeout)
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
        self.assertIn("npu-smi info </dev/null", calls[0][-1])
        session.close()
        self.assertIsNotNone(process.poll())

    def test_hardware_query_reuses_connection_and_receives_server_ip(self):
        session, calls = self.session(["board-a3"])
        raw, rc = session.query()
        self.assertEqual(rc, 0)
        self.assertEqual(session.address, "127.0.0.1")
        self.assertNotIn("NPU_MONITOR_ADDRESS", raw)
        system, rc = session.query_system(time.monotonic() + 2)
        self.assertEqual(rc, 0)
        self.assertEqual(system_product(system), ("Synthetic Server", "dmidecode"))
        board, rc = session.query_board(parse_info(raw)["devices"][0], time.monotonic() + 2)
        self.assertEqual(rc, 0)
        self.assertEqual(detect_platform(parse_info(raw)["devices"], board), "A3")
        self.assertEqual(session.query()[1], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(session.generation, 1)

    def test_board_command_error_keeps_monitoring_connection(self):
        session, calls = self.session(["board-error"])
        raw, _ = session.query()
        self.assertEqual(session.query_board(parse_info(raw)["devices"][0], time.monotonic() + 2)[1], 2)
        self.assertEqual(session.query()[1], 0)
        self.assertEqual(len(calls), 1)

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
    def setUp(self):
        patcher = patch.object(SSHSession, "query_board", return_value=("Chip Name : 910B4", 0))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(SSHSession, "query_system", return_value=("Synthetic Server", 0))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_once_collects_exactly_one_sample(self):
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary:
            config = validate_config({"interval": 1, "nodes": [{"host": "demo"}]}, TESTS)
            monitor = Monitor(config, Path(temporary) / "nodes.json", demo=True, once=True)
            try:
                worker = monitor.workers["demo:22"]
                worker.thread.join(timeout=2)
                self.assertFalse(worker.thread.is_alive())
                self.assertEqual(worker.tick, 1)
                self.assertEqual(len(monitor.snapshot()["nodes"][0]["history"]), 1)
            finally:
                monitor.close()

    def test_node_order_is_persisted_without_replacing_existing_workers(self):
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary, patch.object(SSHSession, "query", return_value=(FIXTURE, 0)):
            base = Path(temporary)
            config = validate_config({"nodes": [{"host": host} for host in ["order-first", "order-second", "order-third"]]}, base)
            monitor = Monitor(config, base / "nodes.json", once=True)
            try:
                for worker in monitor.workers.values():
                    worker.thread.join(timeout=2)
                workers = dict(monitor.workers)
                public = monitor.snapshot()["config"]
                public["nodes"] = [public["nodes"][2], *public["nodes"][:2]]
                public["node_order"] = {"Synthetic Server": [node["id"] for node in public["nodes"]]}
                public["model_order"] = ["Synthetic Server Z", "Synthetic Server A"]
                monitor.reconfigure(validate_config(public, base, config))
                expected = ["order-third", "order-first", "order-second"]
                self.assertEqual([node["host"] for node in monitor.snapshot()["nodes"]], expected)
                self.assertEqual([node["host"] for node in json.loads(monitor.path.read_text())["nodes"]], expected)
                self.assertEqual(json.loads(monitor.path.read_text())["node_order"], public["node_order"])
                loaded = validate_config(json.loads(monitor.path.read_text()), base)
                self.assertEqual(loaded["node_order"], public["node_order"])
                self.assertEqual(loaded["model_order"], public["model_order"])
                self.assertEqual(monitor.snapshot()["config"]["model_order"], public["model_order"])
                self.assertTrue(all(monitor.workers[key] is worker for key, worker in workers.items()))
            finally:
                monitor.close()

    def test_adding_public_node_config_preserves_existing_password_and_order(self):
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary, patch.object(SSHSession, "query", return_value=(FIXTURE, 0)):
            base = Path(temporary)
            config = validate_config({"nodes": [{"host": "key-first"},
                {"host": "password-second", "auth_type": "password", "password": "synthetic-append-password"}]}, base)
            monitor = Monitor(config, base / "nodes.json", once=True)
            try:
                for worker in monitor.workers.values():
                    worker.thread.join(timeout=2)
                workers = dict(monitor.workers)
                public = monitor.snapshot()["config"]
                self.assertNotIn("password", public["nodes"][1])
                public["nodes"].append({"host": "added-third"})
                monitor.reconfigure(validate_config(public, base, config))
                self.assertEqual([node["host"] for node in monitor.config["nodes"]], ["key-first", "password-second", "added-third"])
                self.assertEqual(monitor.config["nodes"][1]["password"], "synthetic-append-password")
                self.assertTrue(all(monitor.workers[key] is worker for key, worker in workers.items()))
            finally:
                monitor.close()

    def test_demo_model_and_die_count_are_stable_after_reordering(self):
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary:
            config = validate_config({"nodes": [{"host": f"demo-{index}"} for index in range(6)]}, TESTS)
            monitor = Monitor(config, Path(temporary) / "nodes.json", demo=True)
            try:
                wait_until(lambda: all(node["updated_at"] for node in monitor.snapshot()["nodes"]))
                before = {node["id"]: (node["server_model"], len(node["devices"])) for node in monitor.snapshot()["nodes"]}
                self.assertEqual(before["demo-4:22"][1], 16)
                ticks = {key: worker.tick for key, worker in monitor.workers.items()}
                changed = copy.deepcopy(config)
                changed["nodes"].reverse()
                monitor.reconfigure(changed)
                wait_until(lambda: all(worker.tick > ticks[key] for key, worker in monitor.workers.items()))
                self.assertEqual({node["id"]: (node["server_model"], len(node["devices"])) for node in monitor.snapshot()["nodes"]}, before)
                changed["timeout"] = 7
                monitor.reconfigure(changed)
                wait_until(lambda: all(node["updated_at"] for node in monitor.snapshot()["nodes"]))
                self.assertEqual({node["id"]: (node["server_model"], len(node["devices"])) for node in monitor.snapshot()["nodes"]}, before)
            finally:
                monitor.close()

    def test_automatic_name_and_identity_survive_config_save(self):
        config = validate_config({"interval": 3600, "nodes": [{"host": "demo", "name": "arbitrary"}]}, TESTS)
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary, patch.object(SSHSession, "query", return_value=(FIXTURE, 0)):
            monitor = Monitor(config, Path(temporary) / "nodes.json")
            try:
                wait_until(lambda: monitor.snapshot()["nodes"][0]["updated_at"])
                state = monitor.snapshot()
                self.assertEqual(state["nodes"][0]["name"], "A2 - demo")
                self.assertEqual(state["nodes"][0]["platform"], "A2")
                self.assertEqual(state["nodes"][0]["server_model"], "Synthetic Server")
                worker = monitor.workers[state["nodes"][0]["id"]]
                monitor.reconfigure(validate_config(state["config"], TESTS, config))
                self.assertIs(monitor.workers[worker.node.id], worker)
                self.assertNotIn("name", json.loads(monitor.path.read_text())["nodes"][0])
            finally:
                monitor.close()

    def test_a3_worker_preserves_all_dies_processes_and_average(self):
        config = validate_config({"nodes": [{"host": "demo"}]}, TESTS)
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary, \
                patch.object(SSHSession, "query", return_value=(A3_FIXTURE, 0)), \
                patch.object(SSHSession, "query_system", return_value=("Synthetic Server A3", 0)), \
                patch.object(SSHSession, "query_board", return_value=("unsupported", 2)):
            monitor = Monitor(config, Path(temporary) / "nodes.json", once=True)
            try:
                worker = next(iter(monitor.workers.values()))
                worker.thread.join(timeout=2)
                self.assertFalse(worker.thread.is_alive())
                node = monitor.snapshot()["nodes"][0]
                self.assertEqual(node["platform"], "A3")
                self.assertEqual(node["status"], "ok")
                self.assertEqual(len(node["devices"]), 16)
                self.assertEqual(len(node["processes"]), 17)
                self.assertEqual(node["history"][0]["value"], 43.75)
            finally:
                monitor.close()

    def test_board_failure_does_not_hide_valid_utilization(self):
        config = validate_config({"nodes": [{"host": "demo"}]}, TESTS)
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary, patch.object(SSHSession, "query", return_value=(FIXTURE, 0)), \
                patch.object(SSHSession, "query_board", return_value=("unsupported", 2)):
            monitor = Monitor(config, Path(temporary) / "nodes.json", once=True)
            try:
                wait_until(lambda: monitor.snapshot()["nodes"][0]["updated_at"])
                node = monitor.snapshot()["nodes"][0]
                self.assertEqual(node["status"], "ok")
                self.assertEqual(node["name"], "未知环境 - demo")
                self.assertEqual(len(node["devices"]), 8)
                self.assertIn("2", node["platform_error"])
            finally:
                monitor.close()

    def test_server_model_survives_npu_failure_and_queries_are_cached(self):
        config = validate_config({"interval": 3600, "nodes": [{"host": "demo"}]}, TESTS)
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary, \
                patch.object(SSHSession, "query", return_value=("driver unavailable", 187)), \
                patch.object(SSHSession, "query_system", return_value=("Synthetic Server A3", 0)) as system:
            monitor = Monitor(config, Path(temporary) / "nodes.json")
            try:
                wait_until(lambda: monitor.snapshot()["nodes"][0]["updated_at"])
                node = monitor.snapshot()["nodes"][0]
                self.assertEqual(node["server_model"], "Synthetic Server A3")
                self.assertEqual(node["name"], "A3 - demo")
                self.assertEqual(node["exit_code"], 187)
                self.assertEqual(node["devices"], [])
                samples = len(node["history"])
                monitor.control("refresh")
                wait_until(lambda: len(monitor.snapshot()["nodes"][0]["history"]) > samples)
                self.assertEqual(system.call_count, 1)
            finally:
                monitor.close()

    def test_server_model_failure_preserves_utilization(self):
        config = validate_config({"nodes": [{"host": "demo"}]}, TESTS)
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary, \
                patch.object(SSHSession, "query", return_value=(FIXTURE, 0)), \
                patch.object(SSHSession, "query_system", return_value=("denied", 1)):
            monitor = Monitor(config, Path(temporary) / "nodes.json", once=True)
            try:
                wait_until(lambda: monitor.snapshot()["nodes"][0]["updated_at"])
                node = monitor.snapshot()["nodes"][0]
                self.assertEqual(node["status"], "ok")
                self.assertIsNone(node["server_model"])
                self.assertIn("1", node["server_model_error"])
                self.assertEqual(len(node["devices"]), 8)
            finally:
                monitor.close()

    def test_interval_pause_and_runtime_reconfigure(self):
        with tempfile.TemporaryDirectory(dir=TESTS) as temporary:
            config = validate_config({"interval": 1, "nodes": [{"name": "训练节点", "host": "demo"}]}, TESTS)
            monitor = Monitor(config, Path(temporary) / "nodes.json", demo=True)
            try:
                wait_until(lambda: monitor.snapshot()["nodes"][0]["updated_at"] is not None)
                worker = monitor.workers["demo:22"]
                monitor.control("pause")
                time.sleep(.08)
                tick = worker.tick
                time.sleep(1.05)
                self.assertEqual(worker.tick, tick)
                changed = copy.deepcopy(config)
                changed["interval"] = 3
                monitor.reconfigure(changed)
                self.assertIs(monitor.workers["demo:22"], worker)
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
        patcher = patch.object(SSHSession, "query_board", return_value=("Chip Name : 910B4", 0))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(SSHSession, "query_system", return_value=("Synthetic Server", 0))
        patcher.start()
        self.addCleanup(patcher.stop)
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

    def test_only_one_dashboard_can_listen_on_a_port(self):
        with self.assertRaises(OSError):
            DashboardServer(self.server.server_port, self.monitor)

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

    def test_duplicate_addition_reports_merge_and_persists_one_node(self):
        self.monitor.control("pause")
        config = {"nodes": [{"host": "duplicate-node", "name": "one"},
                             {"host": "duplicate-node", "name": "two", "port": 22}]}
        status, _, body = self.request("POST", "/api/config", config, {"X-Monitor-Token": self.server.token})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["duplicates_removed"], 1)
        self.assertEqual(len(json.loads(self.path.read_text())["nodes"]), 1)
        self.assertEqual(len(self.monitor.workers), 1)
        self.assertNotIn('data-field="name"', self.request("GET", "/")[2].decode())

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
                self.assertEqual([node["host"] for node in saved["nodes"]], ["node-one", "node-two"])
                self.assertTrue(all("name" not in node for node in saved["nodes"]))
                self.assertEqual(saved["nodes"][1]["port"], 2222)
                status, _, body = self.request("GET", "/api/state", headers=token)
                self.assertEqual(status, 200)
                nodes = json.loads(body)["nodes"]
                self.assertEqual(len(nodes), 2)
                self.assertTrue(all(len(node["devices"]) == 8 for node in nodes))
                self.assertEqual(nodes[1]["devices"][1]["health"], "Critical")
            finally:
                self.monitor.close()


class PlatformTests(unittest.TestCase):
    def test_system_product_and_readonly_fallback(self):
        self.assertEqual(system_product("# dmidecode\nNPU_MONITOR_SYSTEM_SOURCE dmidecode\n Synthetic   Server A2 \n"),
                         ("Synthetic Server A2", "dmidecode"))
        self.assertEqual(system_product("dmidecode: denied\nNPU_MONITOR_SYSTEM_SOURCE sysfs\nSynthetic Server"),
                         ("Synthetic Server", "sysfs"))
        self.assertEqual(server_generation("Synthetic Server A2"), "A2")
        self.assertIsNone(system_product("To Be Filled By O.E.M.")[0])
        self.assertIsNone(server_generation("Synthetic A234"))

    def test_official_board_fields_identify_a2_a3_a5(self):
        boards = [("Chip Type : Ascend\nChip Name : 910B4", "A2"),
                  ("Chip Name : Ascend910\nNPU Name : 9392", "A3"),
                  ("Chip Name : Ascend950PR\nNPU Name : 9579", "A5"),
                  ("Chip Name : Ascend950DT\nNPU Name : 9589", "A5")]
        for board, expected in boards:
            with self.subTest(board=board):
                self.assertEqual(detect_platform([], board), expected)

    def test_model_fallback_and_unknown_are_explicit(self):
        self.assertEqual(model_generation("Ascend 910B4"), "A2")
        self.assertEqual(model_generation("Ascend910_9382"), "A3")
        self.assertEqual(model_generation("910C"), "A3")
        self.assertEqual(model_generation("950PR"), "A5")
        self.assertIsNone(model_generation("Ascend910"))
        self.assertIsNone(detect_platform([{"name": "DemoNPU"}]))
        self.assertIsNone(detect_platform([{"name": "910B4"}, {"name": "950PR"}]))


if __name__ == "__main__":
    unittest.main()
