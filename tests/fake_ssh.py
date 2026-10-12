"""Subprocess fixture for transport tests; never connects to the network."""
from pathlib import Path
import os
import subprocess
import sys
import time

mode = sys.argv[1]
raw = (Path(__file__).parent / "fixtures" / "npu-smi-synthetic.txt").read_text(encoding="utf-8")
if mode.startswith("password"):
    reply = subprocess.run([os.environ["SSH_ASKPASS"], "example-user@host's password:"], capture_output=True)
    expected = "  密码$'`\\; abc  " if mode == "password-ok" else "different-password"
    if reply.returncode or reply.stdout.decode("utf-8").rstrip("\r\n") != expected:
        print("Permission denied (password).", flush=True)
        raise SystemExit(255)
for request in sys.stdin:
    request = request.strip()
    if mode == "disconnect":
        print("Permission denied (publickey).", flush=True)
        raise SystemExit(255)
    if mode == "timeout":
        time.sleep(60)
        continue
    print(request + " BEGIN", flush=True)
    if mode == "command-error":
        print("sh: npu-smi: command not found", flush=True)
        print(request + " END 127", flush=True)
    elif mode == "oversize":
        print("x" * (2 * 1024 * 1024 + 1), flush=True)
        print(request + " END 0", flush=True)
    else:
        print(raw, flush=True)
        print(request + " END 0", flush=True)
