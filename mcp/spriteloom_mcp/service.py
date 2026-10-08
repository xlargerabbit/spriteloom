"""Local WebSocket adapter and optional ownership of the GPU process."""

import asyncio
import atexit
import json
import socket
import subprocess
import sys
import uuid
from typing import Awaitable, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .config import autostart, port, project_root
from .images import raw_image


class ServiceError(Exception):
    pass


Progress = Callable[[float, str | None], Awaitable[None]]


class ServiceClient:
    def __init__(self):
        self.port = port()
        self.url = f"ws://127.0.0.1:{self.port}"
        self.root = project_root()
        self.allow_start = autostart()
        self.proc: subprocess.Popen | None = None
        self.job = None
        self.protocol: int | None = None
        self._start_lock = asyncio.Lock()
        atexit.register(self.stop)

    async def ping(self) -> dict | None:
        try:
            async with connect(self.url, open_timeout=3, close_timeout=2,
                               ping_interval=None) as ws:
                await ws.send(json.dumps({"type": "ping"}))
                msg = json.loads(await asyncio.wait_for(ws.recv(), 3))
        except (OSError, TimeoutError, asyncio.TimeoutError, WebSocketException):
            return None
        except Exception as exc:
            raise ServiceError(f"could not probe Spriteloom on {self.url}: {exc}") from exc
        if not isinstance(msg, dict) or msg.get("type") != "pong" or msg.get("model") not in ("ready", "loading"):
            raise ServiceError(f"port {self.port} is occupied by a different service")
        if msg.get("service", "spriteloom") != "spriteloom":
            raise ServiceError(f"port {self.port} is occupied by a different service")
        self.protocol = msg.get("protocol")
        return msg

    def _start(self) -> None:
        if sys.platform != "win32":
            raise ServiceError(
                "Spriteloom autostart is supported on Windows; start the service manually or set SPRITELOOM_MCP_AUTOSTART=0"
            )
        python = self.root / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            raise ServiceError("Spriteloom GPU environment is missing; complete Setup in the launcher")
        if str(self.root) not in sys.path:
            sys.path.insert(0, str(self.root))
        from launcher.server_proc import assign_to_job, close_job, make_kill_on_close_job
        from .config import user_dir
        log_path = user_dir() / "mcp-service.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("ab", buffering=0) as log_file:
                self.proc = subprocess.Popen(
                    [str(python), "-u", "-m", "server.main"],
                    cwd=self.root, stdout=log_file, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            self.job = make_kill_on_close_job()
            if not assign_to_job(self.job, self.proc.pid):
                close_job(self.job)
                self.job = None
                self.stop()
                raise ServiceError("could not assign Spriteloom to a Windows cleanup job")
        except OSError as exc:
            raise ServiceError(f"could not start Spriteloom: {exc}") from exc

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if self.job:
            from launcher.server_proc import close_job
            close_job(self.job)
            self.job = None

    async def ensure_ready(self) -> None:
        async with self._start_lock:
            pong = await self.ping()
            if pong and pong["model"] == "ready":
                return
            if pong is None:
                with socket.socket() as sock:
                    sock.settimeout(1)
                    busy = sock.connect_ex(("127.0.0.1", self.port)) == 0
                if not busy:
                    if not self.allow_start:
                        raise ServiceError("Spriteloom is offline; start it in the launcher")
                    if self.proc is None or self.proc.poll() is not None:
                        self.stop()
                        self._start()
            deadline = asyncio.get_running_loop().time() + 300
            while asyncio.get_running_loop().time() < deadline:
                if self.proc and self.proc.poll() is not None:
                    from .config import user_dir
                    raise ServiceError(f"Spriteloom exited during startup; see {user_dir() / 'mcp-service.log'}")
                await asyncio.sleep(2)
                pong = await self.ping()
                if pong and pong["model"] == "ready":
                    return
            raise ServiceError(f"Spriteloom did not answer as ready on port {self.port} within five minutes")

    async def request(self, payload: dict, on_progress: Progress | None = None) -> tuple[list, list[int]]:
        await self.ensure_ready()
        req_id = uuid.uuid4().hex
        payload = {**payload, "id": req_id}
        try:
            async with connect(self.url, open_timeout=10, close_timeout=2,
                               max_size=64 * 2**20, ping_interval=None) as ws:
                await ws.send(json.dumps(payload))
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 600))
                    if msg.get("id") not in (req_id, ""):
                        continue
                    if msg.get("type") == "progress":
                        if on_progress:
                            await on_progress(float(msg.get("value", 0)), msg.get("stage"))
                    elif msg.get("type") == "error":
                        raise ServiceError(msg.get("message") or "generation failed")
                    elif msg.get("type") == "result":
                        images = [raw_image(item) for item in msg["images"]]
                        seeds = msg.get("seeds", [])
                        if not images or len(seeds) != len(images):
                            raise ServiceError("Spriteloom returned an incomplete result")
                        return images, seeds
        except ServiceError:
            raise
        except (OSError, TimeoutError, asyncio.TimeoutError, WebSocketException) as exc:
            raise ServiceError(f"lost connection to Spriteloom: {exc}") from exc
