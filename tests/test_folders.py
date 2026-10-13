"""Custom directories are metadata; edits preserve monitoring connections."""

import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/python"))
from monitor import DashboardServer, Monitor, validate_config


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.path = self.base / "nodes.json"
        self.key = self.base / "synthetic-key"
        self.key.touch()
        self.resolver = patch("monitor.resolve_endpoint", side_effect=lambda host, user, port: (host, port))
        self.resolver.start()
        self.monitor = Monitor(validate_config({"nodes": []}, self.base), self.path)
        self.monitor.control("pause")
        self.monitor.reconfigure(validate_config({"interval": 17, "timeout": 19, "identity_file": str(self.key),
            "nodes": [{"host": "node-a", "auth_type": "password", "password": "synthetic-folder-password"},
                      {"host": "node-b"}], "model_order": ["Synthetic Model"],
            "node_order": {"Synthetic Model": ["node-a:22", "node-b:22"]}}, self.base), persist=False)
        self.server = DashboardServer(0, self.monitor)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.monitor.close()
        self.resolver.stop()
        self.temporary.cleanup()

    def request(self, data=None, path="/api/folders", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            conn.request("POST", path, json.dumps(data) if data is not None else None,
                         {"X-Monitor-Token": self.server.token} if headers is None else headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def create(self, name):
        status, response = self.request({"action": "create", "name": name})
        self.assertEqual(status, 200, response)
        return response["folder_id"]

    def test_multifolder_membership_persists_without_changing_connections(self):
        before = copy.deepcopy(self.monitor.config)
        workers = dict(self.monitor.workers)
        histories = [copy.deepcopy(worker.state["history"]) for worker in workers.values()]
        first, second = self.create("Custom A"), self.create("Custom B")
        for folder_id in (first, second, first):
            status, _ = self.request({"action": "assign", "folder_id": folder_id, "node_id": "node-a:22"})
            self.assertEqual(status, 200)
        folders = self.monitor.config["folders"]
        self.assertEqual([folder["node_ids"] for folder in folders], [["node-a:22"], ["node-a:22"]])
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        loaded = validate_config(saved, self.base)
        self.assertEqual(loaded["folders"], folders)
        restarted = Monitor(validate_config({"nodes": []}, self.base), self.path)
        try:
            restarted.control("pause")
            restarted.reconfigure(loaded, persist=False)
            self.assertEqual(restarted.snapshot()["config"]["folders"], folders)
        finally:
            restarted.close()
        self.assertEqual(saved["nodes"][0]["password"], "synthetic-folder-password")
        self.assertEqual({key: value for key, value in self.monitor.config.items() if key != "folders"},
                         {key: value for key, value in before.items() if key != "folders"})
        self.assertTrue(self.monitor.paused)
        self.assertTrue(all(self.monitor.workers[key] is worker for key, worker in workers.items()))
        self.assertEqual([worker.state["history"] for worker in workers.values()], histories)

    def test_rename_reorder_remove_and_delete_preserve_other_memberships(self):
        first, second = self.create("Custom A"), self.create("Custom B")
        self.assertEqual(self.request({"action": "set_membership", "node_id": "node-b:22",
                                       "folder_ids": [first, second]})[0], 200)
        self.assertEqual(self.request({"action": "rename", "folder_id": first, "name": "Renamed"})[0], 200)
        self.assertEqual(self.request({"action": "reorder", "folder_ids": [second, first]})[0], 200)
        self.assertEqual(self.monitor.config["folders"][1]["name"], "Renamed")
        self.assertEqual(self.request({"action": "remove", "folder_id": first, "node_id": "node-b:22"})[0], 200)
        self.assertEqual(self.monitor.config["folders"][0]["node_ids"], ["node-b:22"])
        self.assertEqual(self.monitor.config["folders"][1]["node_ids"], [])
        self.assertEqual(self.request({"action": "delete", "folder_id": first})[0], 200)
        self.assertEqual(len(self.monitor.config["nodes"]), 2)
        self.assertEqual(self.monitor.config["folders"][0]["node_ids"], ["node-b:22"])
        self.assertEqual(self.request({"action": "set_membership", "node_id": "node-b:22", "folder_ids": []})[0], 200)
        self.assertEqual(self.monitor.config["folders"][0]["node_ids"], [])

    def test_batch_assign_preserves_existing_members_and_other_directories(self):
        first, second = self.create("Custom A"), self.create("Custom B")
        self.request({"action": "assign", "folder_id": first, "node_id": "node-a:22"})
        self.request({"action": "assign", "folder_id": second, "node_id": "node-b:22"})
        before = copy.deepcopy(self.monitor.config)
        workers = dict(self.monitor.workers)
        histories = [copy.deepcopy(worker.state["history"]) for worker in workers.values()]
        payload = {"action": "assign_many", "folder_id": first,
                   "node_ids": ["node-b:22", "node-a:22", "node-b:22"]}
        for _ in range(2):
            status, response = self.request(payload)
            self.assertEqual(status, 200, response)
            self.assertEqual(response["folders"][0]["node_ids"], ["node-a:22", "node-b:22"])
            self.assertEqual(response["folders"][1]["node_ids"], ["node-b:22"])
        loaded = validate_config(json.loads(self.path.read_text(encoding="utf-8")), self.base)
        self.assertEqual(loaded["folders"], self.monitor.config["folders"])
        self.assertEqual({key: value for key, value in self.monitor.config.items() if key != "folders"},
                         {key: value for key, value in before.items() if key != "folders"})
        self.assertTrue(self.monitor.paused)
        self.assertTrue(all(self.monitor.workers[key] is worker for key, worker in workers.items()))
        self.assertEqual([worker.state["history"] for worker in workers.values()], histories)

    def test_invalid_batch_is_rejected_atomically(self):
        first = self.create("Custom A")
        previous = self.path.read_bytes()
        for selected in (None, "node-a:22", [], [1], ["node-a:22", "missing-node"], ["node-a:22"] * 65):
            with self.subTest(selected=selected):
                self.assertEqual(self.request({"action": "assign_many", "folder_id": first,
                                               "node_ids": selected})[0], 400)
                self.assertEqual(self.monitor.config["folders"][0]["node_ids"], [])
                self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(self.request({"action": "assign_many", "folder_id": "missing-folder",
                                       "node_ids": ["node-a:22"]})[0], 400)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_batch_write_failure_keeps_members_unchanged(self):
        first = self.create("Custom A")
        previous = copy.deepcopy(self.monitor.config)
        saved = self.path.read_bytes()
        with patch.object(self.monitor, "save_config", side_effect=OSError("synthetic write failure")):
            self.assertEqual(self.request({"action": "assign_many", "folder_id": first,
                                           "node_ids": ["node-a:22", "node-b:22"]})[0], 500)
        self.assertEqual(self.monitor.config, previous)
        self.assertEqual(self.path.read_bytes(), saved)

    def test_token_origin_and_invalid_operations_are_rejected_without_writes(self):
        self.assertEqual(self.request(headers={})[0], 403)
        self.assertEqual(self.request(headers={"X-Monitor-Token": self.server.token,
                                              "Origin": "https://example.invalid"})[0], 403)
        first = self.create("Custom A")
        previous = self.path.read_bytes()
        invalid = [[], {}, {"action": []}, {"action": "unsupported"},
            {"action": "create", "name": " "}, {"action": "create", "name": "Custom A"},
            {"action": "create", "name": "x" * 81}, {"action": "create", "name": "one\nline"},
            {"action": "assign", "folder_id": first, "node_id": "missing-node"},
            {"action": "assign", "folder_id": "missing-folder", "node_id": "node-a:22"},
            {"action": "set_membership", "node_id": "node-a:22", "folder_ids": ["missing-folder"]},
            {"action": "reorder", "folder_ids": []}, {"action": "reorder", "folder_ids": [first, first]}]
        for data in invalid:
            with self.subTest(data=data):
                self.assertEqual(self.request(data)[0], 400)
                self.assertEqual(self.path.read_bytes(), previous)

    def test_save_failure_keeps_in_memory_configuration_and_workers(self):
        first = self.create("Custom A")
        previous = copy.deepcopy(self.monitor.config)
        workers = dict(self.monitor.workers)
        with patch.object(self.monitor, "save_config", side_effect=OSError("synthetic write failure")):
            self.assertEqual(self.request({"action": "assign", "folder_id": first, "node_id": "node-a:22"})[0], 500)
        self.assertEqual(self.monitor.config, previous)
        self.assertTrue(all(self.monitor.workers[key] is worker for key, worker in workers.items()))

    def test_config_edits_preserve_folders_and_prune_removed_nodes(self):
        first = self.create("Custom A")
        self.request({"action": "set_membership", "node_id": "node-a:22", "folder_ids": [first]})
        public = self.monitor.snapshot()["config"]
        public["folders"] = []
        self.assertEqual(self.request(public, "/api/config")[0], 200)
        self.assertEqual(self.monitor.config["folders"][0]["node_ids"], ["node-a:22"])
        public["nodes"] = [node for node in public["nodes"] if node["host"] == "node-b"]
        self.assertEqual(self.request(public, "/api/config")[0], 200)
        self.assertEqual(self.monitor.config["folders"][0]["node_ids"], [])
        self.monitor.add_nodes({"nodes": [{"host": "node-c"}]})
        self.assertEqual(self.monitor.config["folders"][0]["id"], first)

    def test_config_folder_validation_and_unknown_members(self):
        data = {"nodes": [{"host": "node-a"}], "folders": [
            {"id": "a", "name": " Custom A ", "node_ids": ["node-a:22", "node-a:22", "removed-node:22"]},
            {"id": "b", "name": "Custom B", "node_ids": ["node-a:22"]}]}
        result = validate_config(data, self.base)
        self.assertEqual([folder["node_ids"] for folder in result["folders"]], [["node-a:22"], ["node-a:22"]])
        self.assertEqual(result["folders"][0]["name"], "Custom A")
        for folders in ([{"id": "a", "name": "Same"}, {"id": "b", "name": "same"}],
                        [{"id": "a", "name": "One"}, {"id": "a", "name": "Two"}],
                        [{"id": "bad id", "name": "One"}], [{"id": "a", "name": "One", "node_ids": [1]}]):
            with self.assertRaises(ValueError):
                validate_config({**data, "folders": folders}, self.base)
