"""POSIX askpass client. Only native SSH password prompts are answered."""

import os
import sys
import urllib.request


def main():
    try:
        if len(sys.argv) != 2 or not any(word in sys.argv[1].lower() for word in ("password", "passphrase")):
            return 1
        port = int(os.environ["NPU_MONITOR_ASKPASS_PORT"])
        if not 1 <= port <= 65535:
            return 1
        request = urllib.request.Request(f"http://127.0.0.1:{port}/password",
            headers={"X-Askpass-Token": os.environ["NPU_MONITOR_ASKPASS_TOKEN"]})
        # Ambient proxy settings must never route credentials away from loopback.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=3) as response:
            password = response.read(16384).decode("utf-8")
        sys.stdout.buffer.write(password.encode("utf-8") + b"\n")
        return 0
    except (OSError, ValueError, KeyError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
