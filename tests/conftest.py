"""A real Warrant process for the integration tests.

Only the tests start Warrant. Drift itself never does — it talks to Warrant over HTTP and nothing
else (`scripts/dev.sh` starts it for the Make targets). Each process here gets an in-memory store,
a random token and a free port, and is stopped afterwards.
"""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

WARRANT_DIR = Path(__file__).resolve().parent.parent / "warrant"
STARTUP_SECONDS = 15


@dataclass
class WarrantProcess:
    url: str
    token: str
    proc: subprocess.Popen

    def get(self, path: str) -> httpx.Response:
        return httpx.get(self.url + path, headers={"Authorization": f"Bearer {self.token}"},
                         timeout=5)

    def events(self) -> list[dict]:
        return self.get("/events?limit=1000").json()["events"]

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_warrant(**env: str) -> WarrantProcess:
    deno = shutil.which("deno")
    if deno is None:
        pytest.fail("Deno is required for the integration tests (https://deno.com)")
    port, token = free_port(), secrets.token_hex(32)
    stderr = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        [deno, "run", "--allow-net", "--allow-read", "--allow-write", "--allow-env",
         "src/server.ts"],
        cwd=WARRANT_DIR, stdout=subprocess.DEVNULL, stderr=stderr,
        env={**os.environ, "PORT": str(port), "WARRANT_STORE": "memory",
             "WARRANT_TOKEN": token, **env})
    w = WarrantProcess(f"http://127.0.0.1:{port}", token, proc)
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr.seek(0)
            pytest.fail(f"Warrant exited during startup: {stderr.read().decode()[-2000:]}")
        try:
            if httpx.get(w.url + "/health", timeout=1).status_code == 200:
                return w
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    w.stop()
    pytest.fail(f"Warrant did not answer /health within {STARTUP_SECONDS}s")


@pytest.fixture(scope="session")
def warrant() -> Iterator[WarrantProcess]:
    """One Warrant for the session. Tests share its ledger, so they count events as deltas."""
    w = start_warrant()
    yield w
    w.stop()


@pytest.fixture
def warrant_factory() -> Iterator:
    """Start extra Warrants (for example with a short approval TTL); all stopped afterwards."""
    started: list[WarrantProcess] = []

    def make(**env: str) -> WarrantProcess:
        started.append(start_warrant(**env))
        return started[-1]

    yield make
    for w in started:
        w.stop()
