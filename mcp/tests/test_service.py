import asyncio
import base64
import json
import socket

import pytest
from PIL import Image
from websockets.asyncio.server import serve

from spriteloom_mcp.service import ServiceClient, ServiceError


def _client(port):
    client = ServiceClient()
    client.port = port
    client.url = f"ws://127.0.0.1:{port}"
    client.allow_start = False
    return client


def _raw(image):
    return {"w": image.width, "h": image.height,
            "px": base64.b64encode(image.tobytes()).decode("ascii")}


def test_request_translates_progress_and_rgba_result():
    async def go():
        received = []
        image = Image.new("RGBA", (2, 2), (12, 34, 56, 78))

        async def handler(ws):
            async for raw in ws:
                message = json.loads(raw)
                if message.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong", "model": "ready",
                                              "service": "spriteloom", "protocol": 1}))
                else:
                    received.append(message)
                    await ws.send(json.dumps({"id": message["id"], "type": "progress",
                                              "value": 0.5, "stage": "Generating"}))
                    await ws.send(json.dumps({"id": message["id"], "type": "result",
                                              "images": [_raw(image)], "seeds": [42]}))

        async with serve(handler, "127.0.0.1", 0) as server:
            client = _client(server.sockets[0].getsockname()[1])
            progress = []

            async def on_progress(value, stage):
                progress.append((value, stage))

            images, seeds = await client.request({"mode": "generate", "prompt": "coin",
                                                   "target_size": [2, 2]}, on_progress)
            assert images[0].tobytes() == image.tobytes()
            assert seeds == [42]
            assert received[0]["mode"] == "generate"
            assert received[0]["id"]
            assert progress == [(0.5, "Generating")]
            assert client.protocol == 1

    asyncio.run(go())


def test_service_error_is_reported():
    async def go():
        async def handler(ws):
            async for raw in ws:
                message = json.loads(raw)
                if message.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong", "model": "ready"}))
                else:
                    await ws.send(json.dumps({"id": message["id"], "type": "error",
                                              "message": "out of VRAM"}))

        async with serve(handler, "127.0.0.1", 0) as server:
            client = _client(server.sockets[0].getsockname()[1])
            with pytest.raises(ServiceError, match="out of VRAM"):
                await client.request({"mode": "generate", "prompt": "coin",
                                      "target_size": [2, 2]})

    asyncio.run(go())


def test_busy_port_with_wrong_service_is_rejected():
    async def go():
        async def handler(ws):
            async for _ in ws:
                await ws.send(json.dumps({"type": "pong", "model": "ready",
                                          "service": "something-else"}))

        async with serve(handler, "127.0.0.1", 0) as server:
            client = _client(server.sockets[0].getsockname()[1])
            with pytest.raises(ServiceError, match="different service"):
                await client.ensure_ready()

    asyncio.run(go())


def test_attached_service_is_not_stopped():
    client = _client(8765)
    assert client.proc is None
    client.stop()
    assert client.proc is None


def test_stop_terminates_only_owned_process():
    class OwnedProcess:
        def __init__(self):
            self.terminated = False
            self.waited = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            assert timeout == 5
            self.waited = True

    client = _client(8765)
    owned = OwnedProcess()
    client.proc = owned
    client.stop()
    assert owned.terminated and owned.waited
    assert client.proc is None


def test_offline_service_without_autostart_is_actionable():
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
    client = _client(port)
    with pytest.raises(ServiceError, match="start it in the launcher"):
        asyncio.run(client.ensure_ready())
