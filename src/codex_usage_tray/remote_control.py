"""Sanitized, timeout-bounded communication with Codex Remote Control."""

from __future__ import annotations

import base64
import json
import os
import select
import struct
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import IO, Protocol

from . import __version__
from . import codex_executable
from .codex_executable import CodexExecutableResolutionError
from .pairing import PairingArtifact, PairingError


class RemoteControlError(RuntimeError):
    """Base class for sanitized Remote Control failures."""


class RemoteControlUnavailableError(RemoteControlError):
    """The installed Codex CLI does not expose the required surface."""


class RemoteControlTimeoutError(RemoteControlError):
    """A bounded Codex operation timed out."""


class RemoteControlProtocolError(RemoteControlError):
    """Codex returned malformed or unsupported output."""


class RemoteControlCommandError(RemoteControlError):
    """Codex returned a non-zero exit status."""


class RemoteControlPhase(Enum):
    CHECKING = "checking"
    STOPPED = "stopped"
    STARTING = "starting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RemoteControlState:
    phase: RemoteControlPhase
    server_name: str | None = None

    @property
    def is_active(self) -> bool:
        return self.phase is RemoteControlPhase.CONNECTED


def parse_remote_control_status(payload: object) -> RemoteControlState:
    if not isinstance(payload, Mapping):
        raise RemoteControlProtocolError("Remote Control status was not a JSON object")
    status = payload.get("status")
    server_name = payload.get("serverName")
    if server_name is not None and not isinstance(server_name, str):
        server_name = None
    phases = {
        "disabled": RemoteControlPhase.STOPPED,
        "stopped": RemoteControlPhase.STOPPED,
        "connecting": RemoteControlPhase.DISCONNECTED,
        "connected": RemoteControlPhase.CONNECTED,
        "errored": RemoteControlPhase.ERROR,
        "error": RemoteControlPhase.ERROR,
    }
    if not isinstance(status, str) or status not in phases:
        raise RemoteControlProtocolError("Remote Control returned an unknown status")
    return RemoteControlState(phases[status], server_name or None)


def parse_start_output(payload: object) -> RemoteControlState:
    return parse_remote_control_status(payload)


class CompletedCommand(Protocol):
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str], float], CompletedCommand]


def _run_command(command: Sequence[str], timeout: float) -> CompletedCommand:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RemoteControlTimeoutError("Codex Remote Control command timed out") from None
    except (OSError, ValueError):
        raise RemoteControlCommandError("Codex Remote Control command could not start") from None


class ProxyProcess(Protocol):
    stdin: IO[bytes] | None
    stdout: IO[bytes] | None
    stderr: IO[bytes] | None
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


ProxyFactory = Callable[[Sequence[str]], ProxyProcess]


def _spawn_proxy(command: Sequence[str]) -> ProxyProcess:
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except (OSError, ValueError):
        raise RemoteControlCommandError("Codex control socket proxy could not start") from None


def _looks_unavailable(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(
        marker in lowered
        for marker in (
            "unrecognized subcommand",
            "unexpected argument",
            "no such command",
            "unknown command",
            "remote-control is not",
        )
    )


def _looks_daemon_stopped(stderr: str) -> bool:
    lowered = stderr.lower()
    return "not running" in lowered or "no running daemon" in lowered


class RemoteControlClient:
    """Use official one-shot CLI actions and a passive control-socket status read."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner = _run_command,
        proxy_factory: ProxyFactory = _spawn_proxy,
        timeout: float = 12.0,
        executable: str | None = None,
        executable_resolver: Callable[[], str] | None = None,
    ) -> None:
        self._command_runner = command_runner
        self._proxy_factory = proxy_factory
        self._timeout = timeout
        self._executable = executable
        self._executable_resolver = executable_resolver

    def _codex(self) -> str:
        if self._executable is not None:
            return self._executable
        resolver = (
            self._executable_resolver
            or codex_executable.resolve_codex_executable
        )
        try:
            return resolver()
        except (CodexExecutableResolutionError, OSError, ValueError):
            raise RemoteControlUnavailableError(
                "Codex executable was not found in PATH"
            ) from None

    def _json_command(self, arguments: Sequence[str], *, secret: bool = False) -> object:
        result = self._command_runner((self._codex(), *arguments), self._timeout)
        if result.returncode != 0:
            if _looks_unavailable(result.stderr):
                raise RemoteControlUnavailableError(
                    "Installed Codex CLI does not support Remote Control"
                )
            raise RemoteControlCommandError("Codex Remote Control command failed")
        try:
            return json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            raise RemoteControlProtocolError(
                "Codex Remote Control returned invalid JSON"
            ) from None

    def start(self) -> RemoteControlState:
        return parse_start_output(
            self._json_command(("remote-control", "start", "--json"))
        )

    def stop(self) -> RemoteControlState:
        payload = self._json_command(("remote-control", "stop", "--json"))
        if not isinstance(payload, Mapping):
            raise RemoteControlProtocolError("Remote Control stop response was invalid")
        status = payload.get("status")
        if status in ("stopped", "notRunning", "not_running"):
            return RemoteControlState(RemoteControlPhase.STOPPED)
        raise RemoteControlProtocolError("Remote Control stop response was unsupported")

    def pair(self) -> PairingArtifact:
        try:
            return PairingArtifact.from_mapping(
                self._json_command(
                    ("remote-control", "pair", "--json"), secret=True
                )
            )
        except PairingError as error:
            raise RemoteControlProtocolError(str(error)) from None

    def read_status(self) -> RemoteControlState:
        version = self._command_runner(
            (self._codex(), "app-server", "daemon", "version"), self._timeout
        )
        if version.returncode != 0:
            if _looks_unavailable(version.stderr):
                raise RemoteControlUnavailableError(
                    "Installed Codex CLI does not support the app-server daemon"
                )
            if _looks_daemon_stopped(version.stderr):
                return RemoteControlState(RemoteControlPhase.STOPPED)
            raise RemoteControlCommandError("Codex app-server daemon status check failed")
        try:
            version_payload = json.loads(version.stdout)
        except (json.JSONDecodeError, TypeError):
            raise RemoteControlProtocolError(
                "Codex app-server daemon returned invalid version JSON"
            ) from None
        if not isinstance(version_payload, Mapping):
            raise RemoteControlProtocolError(
                "Codex app-server daemon version response was invalid"
            )
        payload = self._proxy_request("remoteControl/status/read")
        return parse_remote_control_status(payload)

    def pairing_claimed(self, artifact: PairingArtifact) -> bool:
        if artifact.pairing_code:
            params = {"pairingCode": artifact.pairing_code}
        else:
            params = {"manualPairingCode": artifact.manual_code}
        payload = self._proxy_request("remoteControl/pairing/status", params)
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("claimed"), bool
        ):
            raise RemoteControlProtocolError("Pairing status response was invalid")
        return bool(payload["claimed"])

    def _proxy_request(
        self, method: str, params: Mapping[str, object] | None = None
    ) -> object:
        process = self._proxy_factory((self._codex(), "app-server", "proxy"))
        if process.stdin is None or process.stdout is None:
            self._dispose_proxy(process)
            raise RemoteControlCommandError("Codex control socket proxy has no pipes")
        deadline = time.monotonic() + self._timeout
        connection = _WebSocketPipe(process.stdin, process.stdout, deadline)
        try:
            connection.handshake()
            connection.send_json(
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "codex_usage_tray",
                            "title": "Codex Usage Tray",
                            "version": __version__,
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                }
            )
            connection.read_response(1)
            connection.send_json({"method": "initialized", "params": {}})
            request: dict[str, object] = {"id": 2, "method": method}
            if params is not None:
                request["params"] = dict(params)
            connection.send_json(request)
            return connection.read_response(2)
        except RemoteControlError:
            raise
        except (OSError, ValueError, json.JSONDecodeError):
            raise RemoteControlProtocolError(
                "Codex control socket returned an invalid response"
            ) from None
        finally:
            self._dispose_proxy(process)

    @staticmethod
    def _dispose_proxy(process: ProxyProcess) -> None:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass


class _WebSocketPipe:
    """Minimal RFC 6455 client for the local app-server proxy only."""

    def __init__(self, writer: IO[bytes], reader: IO[bytes], deadline: float) -> None:
        self._writer = writer
        self._reader = reader
        self._deadline = deadline
        self._buffer = bytearray()

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise RemoteControlTimeoutError("Codex control socket request timed out")
        return remaining

    def _read_more(self) -> None:
        ready, _, _ = select.select([self._reader], [], [], self._remaining())
        if not ready:
            raise RemoteControlTimeoutError("Codex control socket request timed out")
        chunk = os.read(self._reader.fileno(), 65536)
        if not chunk:
            raise RemoteControlCommandError("Codex control socket closed unexpectedly")
        self._buffer.extend(chunk)

    def _take(self, length: int) -> bytes:
        while len(self._buffer) < length:
            self._read_more()
        value = bytes(self._buffer[:length])
        del self._buffer[:length]
        return value

    def handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET /rpc HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self._writer.write(request)
        self._writer.flush()
        marker = b"\r\n\r\n"
        while marker not in self._buffer:
            self._read_more()
        end = self._buffer.index(marker) + len(marker)
        headers = bytes(self._buffer[:end])
        del self._buffer[:end]
        if not headers.startswith(b"HTTP/1.1 101"):
            raise RemoteControlCommandError("Codex control socket handshake failed")

    def send_json(self, payload: Mapping[str, object]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send_frame(0x1, data)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length <= 65535:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self._writer.write(header + mask + masked)
        self._writer.flush()

    def _read_frame(self) -> tuple[int, bytes]:
        first, second = self._take(2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._take(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._take(8))[0]
        mask = self._take(4) if second & 0x80 else None
        payload = self._take(length)
        if mask:
            payload = bytes(
                value ^ mask[index % 4] for index, value in enumerate(payload)
            )
        return opcode, payload

    def read_response(self, request_id: int) -> object:
        while True:
            opcode, payload = self._read_frame()
            if opcode == 0x8:
                raise RemoteControlCommandError("Codex control socket closed unexpectedly")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode != 0x1:
                continue
            message = json.loads(payload.decode("utf-8"))
            if not isinstance(message, Mapping) or message.get("id") != request_id:
                continue
            if "error" in message:
                error = message.get("error")
                if isinstance(error, Mapping) and error.get("code") == -32601:
                    raise RemoteControlUnavailableError(
                        "Managed Codex daemon does not support Remote Control status"
                    )
                raise RemoteControlCommandError("Codex control socket request failed")
            if "result" not in message:
                raise RemoteControlProtocolError("Codex control socket response was incomplete")
            return message["result"]
