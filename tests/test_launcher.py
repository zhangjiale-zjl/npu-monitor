"""Background lifecycle checks use local demo servers and temporary projects."""

import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def api(instance, path="/api/state", method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", urlsplit(instance["origin"]).port, timeout=2)
    try:
        conn.request(method, path, "{}" if method == "POST" else None,
                     {"X-Monitor-Token": instance["token"]})
        response = conn.getresponse()
        data = response.read()
        if response.status != 200:
            raise AssertionError(f"Unexpected HTTP status {response.status}")
        return json.loads(data)
    finally:
        conn.close()


def read_ready(path, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            instance = json.loads(path.read_text(encoding="utf-8-sig"))
            api(instance)
            return instance
        except (OSError, ValueError, http.client.HTTPException):
            time.sleep(.1)
    raise AssertionError("Demo server did not become ready")


class LifecycleTests(unittest.TestCase):
    def check_shutdown(self, replace_record=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            process = subprocess.Popen([sys.executable, str(ROOT / "src/python/monitor.py"),
                                        "--demo", "--no-browser", "--port", "0", "--instance-file", str(path)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
            try:
                instance = read_ready(path)
                self.assertEqual(instance["pid"], process.pid)
                self.assertEqual(Path(instance["root"]), ROOT)
                replacement = {**instance, "token": "synthetic-replacement-token"}
                if replace_record:
                    path.write_text(json.dumps(replacement), encoding="utf-8")
                api(instance, "/api/shutdown", "POST")
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr.decode("utf-8"))
                self.assertIn("SSH 连接已关闭", stdout.decode("utf-8"))
                if replace_record:
                    self.assertEqual(json.loads(path.read_text()), replacement)
                else:
                    self.assertFalse(path.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    def test_shutdown_removes_owned_record_after_cleanup(self):
        self.check_shutdown()

    def test_shutdown_preserves_replacement_record(self):
        self.check_shutdown(replace_record=True)

    def test_single_collection_keeps_json_output(self):
        result = subprocess.run([sys.executable, str(ROOT / "src/python/monitor.py"), "--demo", "--once"],
                                capture_output=True, timeout=15, creationflags=NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(len(json.loads(result.stdout)["nodes"]), 3)


@unittest.skipUnless(os.name == "nt", "Windows launch scripts")
class WindowsLauncherTests(unittest.TestCase):
    def test_start_repeat_restart_stop_with_spaced_paths(self):
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="launcher test & ", dir=output) as directory:
            project = Path(directory)
            for name in ("start.cmd", "restart.cmd", "stop.cmd", "start.ps1"):
                shutil.copy2(ROOT / name, project / name)
            shutil.copytree(ROOT / "src", project / "src", ignore=shutil.ignore_patterns("__pycache__"))
            state = project / "output/background-0.json"
            instance = None

            def launch(script, *arguments, returncode=0):
                result = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                                         str(project / script), *arguments], stdin=subprocess.DEVNULL,
                                        capture_output=True, timeout=45, creationflags=NO_WINDOW)
                self.assertEqual(result.returncode, returncode, result.stdout.decode(errors="replace") +
                                 result.stderr.decode(errors="replace"))
                return result.stdout.decode(errors="replace")

            try:
                self.assertIn("See ", launch("start.cmd", "-Demo", "--no-browser", "--port", "0",
                                              "--interval", "0", returncode=2))
                self.assertFalse(state.exists())
                config = str(project / "config with spaces.json")
                text = launch("start.cmd", "-Demo", "--no-browser", "--port", "0", "--config", config)
                self.assertIn("Background monitor started", text)
                instance = read_ready(state)
                self.assertIn(config, instance["launch_arguments"])
                self.assertTrue(Path(instance["stdout_log"]).is_file())
                self.assertTrue(Path(instance["stderr_log"]).is_file())
                self.assertIn("后台监控已启动", Path(instance["stdout_log"]).read_text(encoding="utf-8"))
                original = instance
                self.assertIn("already running", launch("start.cmd", "--no-browser", "--port", "0"))
                self.assertEqual(read_ready(state)["pid"], original["pid"])
                launch("restart.cmd", "--port", "0", "--interval", "7")
                instance = read_ready(state)
                self.assertNotEqual(instance["pid"], original["pid"])
                self.assertIn(config, instance["launch_arguments"])
                self.assertIn("--demo", instance["launch_arguments"])
                self.assertEqual(api(instance)["config"]["interval"], 7)
                self.assertIn("stopped", launch("stop.cmd", "--port=0"))
                self.assertFalse(state.exists())
                self.assertIn("SSH 连接已关闭", Path(instance["stdout_log"]).read_text(encoding="utf-8"))
                self.assertIn("No managed monitor", launch("stop.cmd", "--port", "0"))
                instance = None
            finally:
                if instance:
                    try:
                        api(instance, "/api/shutdown", "POST")
                        deadline = time.monotonic() + 5
                        while state.exists() and time.monotonic() < deadline:
                            time.sleep(.1)
                    except (OSError, http.client.HTTPException):
                        pass
