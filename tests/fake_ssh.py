"""Subprocess fixture for transport tests; never connects to the network."""
from pathlib import Path
import os
import subprocess
import sys
import time

mode = sys.argv[1]
raw = (Path(__file__).parent / "fixtures" / "npu-smi-synthetic.txt").read_text(encoding="utf-8")
if mode == "a3":
    raw = (Path(__file__).parent / "fixtures" / "npu-smi-a3-synthetic.txt").read_text(encoding="utf-8")
if mode == "a5":
    raw = (Path(__file__).parent / "fixtures" / "npu-smi-a5-synthetic.txt").read_text(encoding="utf-8")
if mode == "password-host-changed":
    print("WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\nHost key verification failed.", flush=True)
    raise SystemExit(255)
if mode.startswith("password"):
    reply = subprocess.run([os.environ["SSH_ASKPASS"], "example-user@host's password:"], capture_output=True)
    expected = "  密码$'`\\; abc  " if mode == "password-ok" else "different-password"
    if reply.returncode or reply.stdout.decode("utf-8").rstrip("\r\n") != expected:
        print("Permission denied (password).", flush=True)
        raise SystemExit(255)
for request in sys.stdin:
    request = request.split()[0]
    if mode == "slow":
        time.sleep(.05)
    if mode == "disconnect":
        print("Permission denied (publickey).", flush=True)
        raise SystemExit(255)
    if mode == "timeout":
        time.sleep(60)
        continue
    print(request + " BEGIN", flush=True)
    if mode == "command-timeout":
        time.sleep(60)
        continue
    if request.startswith("NPU_SYSTEM_"):
        if mode == "system-error":
            print("dmidecode: permission denied", flush=True)
            print(request + " END 1", flush=True)
        else:
            print("NPU_MONITOR_SYSTEM_SOURCE dmidecode\nSynthetic Server", flush=True)
            print(request + " END 0", flush=True)
    elif request.startswith("NPU_BOARD_"):
        if mode == "board-error":
            print("unsupported board query", flush=True)
            print(request + " END 2", flush=True)
        else:
            boards = {"a5": "Chip Name : Ascend950PR\nNPU Name : 9579",
                      "board-a3": "Chip Name : Ascend910\nNPU Name : 9392",
                      "board-a5": "Chip Name : Ascend950PR\nNPU Name : 9579"}
            print(boards.get(mode, "Chip Type : Ascend\nChip Name : 910B4"), flush=True)
            print(request + " END 0", flush=True)
    elif mode == "command-error":
        print("sh: npu-smi: command not found", flush=True)
        print(request + " END 127", flush=True)
    elif mode == "oversize":
        print("x" * (2 * 1024 * 1024 + 1), flush=True)
        print(request + " END 0", flush=True)
    else:
        print("NPU_MONITOR_ADDRESS 127.0.0.1 12345 127.0.0.1 22", flush=True)
        print(raw, flush=True)
        print(request + " END 0", flush=True)
