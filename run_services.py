"""Run the API and Discord bot as supervised processes in one container."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType
from dotenv import load_dotenv

load_dotenv(override=True)

ROOT = Path(__file__).resolve().parent
STOP_REQUESTED = False
SHUTDOWN_TIMEOUT = 20
POLL_INTERVAL = 0.5


def request_shutdown(signum: int, _frame: FrameType | None) -> None:
    global STOP_REQUESTED
    logging.info("Received signal %s; stopping services.", signum)
    STOP_REQUESTED = True


def terminate_processes(processes: list[tuple[str, subprocess.Popen[bytes]]]) -> None:
    for name, process in processes:
        if process.poll() is None:
            logging.info("Stopping %s (pid %s).", name, process.pid)
            process.terminate()

    deadline = time.monotonic() + SHUTDOWN_TIMEOUT
    for name, process in processes:
        if process.poll() is not None:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            logging.warning("%s did not stop in time; killing it.", name)
            process.kill()
            process.wait()


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] supervisor: %(message)s",
        stream=sys.stdout,
    )
    try:
        port = int(os.getenv("PORT", "8000"))
    except ValueError:
        logging.error("PORT must be an integer.")
        return 2
    if not 1 <= port <= 65535:
        logging.error("PORT must be between 1 and 65535.")
        return 2

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    services: list[tuple[str, list[str], Path]] = [
        (
            "proxy",
            [
                sys.executable, "-m", "uvicorn", "proxy:app",
                "--host", "0.0.0.0", "--port", str(port), "--no-access-log",
            ],
            ROOT,
        ),
        ("bot", [sys.executable, "main.py"], ROOT / "bot"),
    ]
    processes: list[tuple[str, subprocess.Popen[bytes]]] = []
    exit_code = 0
    try:
        for name, command, cwd in services:
            logging.info("Starting %s.", name)
            process = subprocess.Popen(command, cwd=cwd)
            processes.append((name, process))

        while not STOP_REQUESTED:
            for name, process in processes:
                status = process.poll()
                if status is not None:
                    logging.error("%s exited unexpectedly with status %s.", name, status)
                    exit_code = status if status != 0 else 1
                    return exit_code
            time.sleep(POLL_INTERVAL)
        return exit_code
    except OSError:
        logging.exception("Could not start a required service.")
        return 1
    finally:
        terminate_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
