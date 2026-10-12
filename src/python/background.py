"""Start the local monitor with independent Windows handles and log files."""

import os
from pathlib import Path
import subprocess
import sys


def main():
    stdout_path, stderr_path, *arguments = sys.argv[1:]
    source = Path(__file__).resolve().with_name("monitor.py")
    options = {"start_new_session": True}
    if os.name == "nt":
        options = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    with open(stdout_path, "wb") as stdout, open(stderr_path, "wb") as stderr:
        process = subprocess.Popen([sys.executable, "-u", str(source), *arguments],
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   close_fds=True, **options)
    print(process.pid)


if __name__ == "__main__":
    main()
