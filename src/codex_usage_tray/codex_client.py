"""Thread-safe client for the narrow Codex app-server rate-limit surface."""

from __future__ import annotations

import json
import subprocess
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from math import isfinite
from threading import Event, Lock, RLock, Thread, current_thread
from typing import IO, Protocol, TypeVar, cast

from . import codex_executable
from .codex_executable import CodexExecutableResolutionError
from .models import (
    RateLimitSnapshot,
    RateLimitValidationError,
    deduplicate_snapshots,
)

RateLimitSnapshots = tuple[RateLimitSnapshot, ...]
Message = dict[str, object]

_T = TypeVar("_T")
_MISSING = object()


class CodexClientError(RuntimeError):
    """Base class for sanitized Codex client failures."""


class CodexExecutableNotFoundError(CodexClientError):
    """Raised when a usable Codex executable cannot be located."""


class CodexProcessStartError(CodexClientError):
    """Raised when the app-server process cannot be started."""


class CodexProtocolError(CodexClientError):
    """Raised when app-server returns an invalid or error response."""


class CodexMalformedMessageError(CodexClientError):
    """Raised when app-server emits malformed JSON or a non-object message."""


class CodexConnectionClosedError(CodexClientError):
    """Raised when app-server closes its output unexpectedly."""


class CodexRequestTimeoutError(CodexClientError):
    """Raised when a request does not finish within its configured timeout."""


class CodexClientStateError(CodexClientError):
    """Raised when an operation is not valid for the current client state."""


class CodexClientClosedError(CodexClientError):
    """Raised when shutdown interrupts an operation."""


@dataclass(frozen=True, slots=True)
class CodexAccount:
    account_type: str
    email: str | None = None
    plan_type: str | None = None


@dataclass(frozen=True, slots=True)
class CodexAccountState:
    account: CodexAccount | None
    requires_openai_auth: bool

    @property
    def is_signed_out(self) -> bool:
        return self.requires_openai_auth and self.account is None


@dataclass(frozen=True, slots=True)
class CodexLoginStart:
    login_id: str
    auth_url: str


@dataclass(frozen=True, slots=True)
class CodexLoginCompletion:
    login_id: str | None
    success: bool
    failed: bool


class CodexShutdownError(CodexClientError):
    """Raised when forced process termination does not complete in time."""


class ProcessLike(Protocol):
    """Minimal subprocess surface used by the client and test doubles."""

    stdin: IO[str] | None
    stdout: IO[str] | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[[tuple[str, ...]], ProcessLike]


class _ClientState(Enum):
    NEW = auto()
    STARTING = auto()
    READY = auto()
    CLOSING = auto()
    CLOSED = auto()
    FAILED = auto()


@dataclass(slots=True)
class _PendingRequest:
    parser: Callable[[object], object]
    event: Event = field(default_factory=Event)
    result: object = None
    error: CodexClientError | None = None


def _validate_timeout(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a positive number")

    timeout = float(value)
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError(f"{field_name} must be a positive number")
    return timeout


def build_codex_command(
    *, executable_resolver: Callable[[], str] | None = None
) -> tuple[str, ...]:
    """Build the app-server command with the shared executable resolver."""

    resolver = executable_resolver or codex_executable.resolve_codex_executable
    try:
        executable = resolver()
    except (CodexExecutableResolutionError, OSError, ValueError):
        raise CodexExecutableNotFoundError(
            "Codex executable was not found in PATH"
        ) from None
    return (executable, "-c", "analytics.enabled=false", "app-server")


def _spawn_process(command: tuple[str, ...]) -> ProcessLike:
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except (OSError, ValueError):
        raise CodexProcessStartError(
            "Codex app-server process could not be started"
        ) from None


def _narrow_window(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CodexProtocolError("Rate-limit response validation failed")

    narrowed: dict[str, object] = {}
    for key in ("usedPercent", "windowDurationMins", "resetsAt"):
        if key in value:
            narrowed[key] = value[key]
    return narrowed


def _narrow_snapshot(value: object, fallback_limit_id: object = None) -> Message:
    if not isinstance(value, Mapping):
        raise CodexProtocolError("Rate-limit response validation failed")

    narrowed: Message = {}
    for key in ("limitId", "limitName"):
        if key in value:
            narrowed[key] = value[key]

    if ("limitId" not in narrowed or narrowed["limitId"] is None) and isinstance(
        fallback_limit_id, str
    ):
        narrowed["limitId"] = fallback_limit_id

    for key in ("primary", "secondary"):
        if key in value:
            narrowed[key] = _narrow_window(value[key])
    return narrowed


def _snapshot_from_value(
    value: object, fallback_limit_id: object = None
) -> RateLimitSnapshot:
    try:
        return RateLimitSnapshot.from_mapping(
            _narrow_snapshot(value, fallback_limit_id)
        )
    except RateLimitValidationError:
        raise CodexProtocolError("Rate-limit response validation failed") from None


def parse_rate_limit_result(payload: object) -> RateLimitSnapshots:
    """Reduce a response or update payload to validated rate-limit snapshots."""

    if not isinstance(payload, Mapping):
        raise CodexProtocolError("Rate-limit response validation failed")

    snapshots: list[RateLimitSnapshot] = []
    default_snapshot = payload.get("rateLimits")
    if default_snapshot is not None:
        snapshots.append(_snapshot_from_value(default_snapshot))

    by_limit_id = payload.get("rateLimitsByLimitId")
    if by_limit_id is not None:
        if not isinstance(by_limit_id, Mapping):
            raise CodexProtocolError("Rate-limit response validation failed")
        for limit_id, value in by_limit_id.items():
            if value is not None:
                snapshots.append(_snapshot_from_value(value, limit_id))

    return deduplicate_snapshots(snapshots)


def parse_account_result(payload: object) -> CodexAccountState:
    """Parse the narrow account/read result without retaining raw JSON."""

    if not isinstance(payload, Mapping):
        raise CodexProtocolError("Account response validation failed")
    requires = payload.get("requiresOpenaiAuth")
    if not isinstance(requires, bool):
        raise CodexProtocolError("Account response validation failed")
    raw_account = payload.get("account")
    if raw_account is None:
        return CodexAccountState(account=None, requires_openai_auth=requires)
    if not isinstance(raw_account, Mapping):
        raise CodexProtocolError("Account response validation failed")
    account_type = raw_account.get("type")
    if not isinstance(account_type, str) or not account_type:
        raise CodexProtocolError("Account response validation failed")
    email = raw_account.get("email")
    if email is not None and not isinstance(email, str):
        raise CodexProtocolError("Account response validation failed")
    plan_type = raw_account.get("planType")
    if plan_type is not None and not isinstance(plan_type, str):
        raise CodexProtocolError("Account response validation failed")
    return CodexAccountState(
        account=CodexAccount(account_type=account_type, email=email, plan_type=plan_type),
        requires_openai_auth=requires,
    )


def parse_login_start_result(payload: object) -> CodexLoginStart:
    if not isinstance(payload, Mapping):
        raise CodexProtocolError("Login response validation failed")
    if payload.get("type") != "chatgpt":
        raise CodexProtocolError("Login response validation failed")
    login_id = payload.get("loginId")
    auth_url = payload.get("authUrl")
    if not isinstance(login_id, str) or not login_id:
        raise CodexProtocolError("Login response validation failed")
    if not isinstance(auth_url, str) or not auth_url.startswith(("http://", "https://")):
        raise CodexProtocolError("Login response validation failed")
    return CodexLoginStart(login_id=login_id, auth_url=auth_url)


def parse_login_completion(payload: object) -> CodexLoginCompletion:
    if not isinstance(payload, Mapping):
        raise CodexProtocolError("Login completion validation failed")
    login_id = payload.get("loginId")
    if login_id is not None and not isinstance(login_id, str):
        raise CodexProtocolError("Login completion validation failed")
    success = payload.get("success")
    if not isinstance(success, bool):
        raise CodexProtocolError("Login completion validation failed")
    return CodexLoginCompletion(login_id=login_id, success=success, failed=not success)

def _parse_initialize_result(payload: object) -> None:
    if not isinstance(payload, Mapping):
        raise CodexProtocolError("App-server initialization response was invalid")
    return None


class CodexAppServerClient:
    """Long-lived, thread-safe client for Codex account rate-limit snapshots."""

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        process_factory: ProcessFactory | None = None,
        request_timeout: float = 5.0,
        shutdown_timeout: float = 2.0,
        client_name: str = "codex_usage_tray",
        client_title: str = "Codex Usage Tray",
        client_version: str = "0.0.0",
    ) -> None:
        if command is not None and not command:
            raise ValueError("command must not be empty")
        if not all(
            isinstance(value, str) and value
            for value in (client_name, client_title, client_version)
        ):
            raise ValueError("client metadata must contain non-empty strings")

        self._command = tuple(command) if command is not None else None
        self._process_factory = process_factory or _spawn_process
        self._request_timeout = _validate_timeout(request_timeout, "request_timeout")
        self._shutdown_timeout = _validate_timeout(
            shutdown_timeout, "shutdown_timeout"
        )
        self._client_info: Message = {
            "name": client_name,
            "title": client_title,
            "version": client_version,
        }

        self._state = _ClientState.NEW
        self._state_lock = RLock()
        self._write_lock = Lock()
        self._process: ProcessLike | None = None
        self._reader_thread: Thread | None = None
        self._next_request_id = 1
        self._pending: dict[int, _PendingRequest] = {}
        self._updates: deque[RateLimitSnapshots] = deque()
        self._login_completions: deque[CodexLoginCompletion] = deque()

    def __enter__(self) -> CodexAppServerClient:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @property
    def is_ready(self) -> bool:
        with self._state_lock:
            return self._state is _ClientState.READY

    def start(self) -> None:
        """Start app-server and complete the required initialization handshake."""

        with self._state_lock:
            if self._state is not _ClientState.NEW:
                raise CodexClientStateError("Codex client can only be started once")
            self._state = _ClientState.STARTING

        try:
            command = (
                self._command
                if self._command is not None
                else build_codex_command()
            )
            process = self._start_process(command)
            self._install_process(process)
            self._request(
                "initialize",
                {"clientInfo": dict(self._client_info)},
                _parse_initialize_result,
                allow_starting=True,
            )
            self._send_message({"method": "initialized", "params": {}})
            with self._state_lock:
                if self._state is not _ClientState.STARTING:
                    raise CodexConnectionClosedError(
                        "App-server connection closed during initialization"
                    )
                self._state = _ClientState.READY
        except CodexClientError:
            self._close_after_start_failure()
            raise
        except Exception:
            self._close_after_start_failure()
            raise CodexProcessStartError(
                "Codex app-server process could not be started"
            ) from None

    def read_account(self, timeout: float | None = None) -> CodexAccountState:
        """Request current narrow Codex account state without refreshing tokens."""

        result = self._request(
            "account/read",
            {"refreshToken": False},
            parse_account_result,
            timeout=timeout,
        )
        return cast(CodexAccountState, result)

    def start_chatgpt_login(self, timeout: float | None = None) -> CodexLoginStart:
        """Start Codex-managed ChatGPT browser authentication."""

        result = self._request(
            "account/login/start",
            {"type": "chatgpt"},
            parse_login_start_result,
            timeout=timeout,
        )
        return cast(CodexLoginStart, result)

    def pop_login_completion(self) -> CodexLoginCompletion | None:
        """Return the oldest sanitized login completion notification."""

        with self._state_lock:
            if not self._login_completions:
                return None
            return self._login_completions.popleft()

    def read_rate_limits(self, timeout: float | None = None) -> RateLimitSnapshots:
        """Request the current account rate limits after initialization."""

        result = self._request(
            "account/rateLimits/read",
            None,
            parse_rate_limit_result,
            timeout=timeout,
        )
        return cast(RateLimitSnapshots, result)

    def pop_rate_limit_update(self) -> RateLimitSnapshots | None:
        """Return the oldest parsed update notification without blocking."""

        with self._state_lock:
            if not self._updates:
                return None
            return self._updates.popleft()

    def close(self) -> None:
        """Stop the reader and child process with bounded forced termination."""

        process: ProcessLike | None
        reader: Thread | None
        with self._state_lock:
            if self._state is _ClientState.CLOSED:
                return
            self._state = _ClientState.CLOSING
            process = self._process
            reader = self._reader_thread
            self._fail_pending_locked(
                CodexClientClosedError("Codex client closed during a pending request")
            )

        shutdown_error: CodexShutdownError | None = None
        if process is not None:
            self._close_stream(process.stdin)
            try:
                self._stop_process(process)
            except CodexShutdownError as error:
                shutdown_error = error
            finally:
                self._close_stream(process.stdout)

        if reader is not None and reader is not current_thread():
            reader.join(timeout=self._shutdown_timeout)
            if reader.is_alive() and shutdown_error is None:
                shutdown_error = CodexShutdownError(
                    "Codex app-server reader did not stop"
                )

        with self._state_lock:
            self._process = None
            self._reader_thread = None
            self._state = _ClientState.CLOSED

        if shutdown_error is not None:
            raise shutdown_error

    def _start_process(self, command: tuple[str, ...]) -> ProcessLike:
        try:
            process = self._process_factory(command)
        except CodexClientError:
            raise
        except Exception:
            raise CodexProcessStartError(
                "Codex app-server process could not be started"
            ) from None

        if process.stdin is None or process.stdout is None:
            self._dispose_unusable_process(process)
            raise CodexProcessStartError(
                "Codex app-server process did not provide required pipes"
            )
        return process

    def _install_process(self, process: ProcessLike) -> None:
        reader = Thread(
            target=self._reader_loop,
            name="codex-app-server-reader",
            daemon=True,
        )
        with self._state_lock:
            if self._state is not _ClientState.STARTING:
                rejected = True
            else:
                rejected = False
                self._process = process
                self._reader_thread = reader

        if rejected:
            self._discard_process(process)
            raise CodexClientClosedError(
                "Codex client closed while the app-server was starting"
            )

        reader.start()

    def _request(
        self,
        method: str,
        params: Message | None,
        parser: Callable[[object], _T],
        *,
        timeout: float | None = None,
        allow_starting: bool = False,
    ) -> _T:
        wait_timeout = (
            self._request_timeout
            if timeout is None
            else _validate_timeout(timeout, "timeout")
        )

        with self._state_lock:
            valid_state = self._state is _ClientState.READY or (
                allow_starting and self._state is _ClientState.STARTING
            )
            if not valid_state:
                raise CodexClientStateError("Codex client is not ready")
            request_id = self._next_request_id
            self._next_request_id += 1
            pending = _PendingRequest(parser=cast(Callable[[object], object], parser))
            self._pending[request_id] = pending

        message: Message = {"method": method, "id": request_id}
        if params is not None:
            message["params"] = params

        try:
            self._send_message(message)
        except CodexClientError:
            with self._state_lock:
                self._pending.pop(request_id, None)
            raise

        if not pending.event.wait(wait_timeout):
            with self._state_lock:
                self._pending.pop(request_id, None)
            raise CodexRequestTimeoutError("App-server request timed out")

        if pending.error is not None:
            raise pending.error
        return cast(_T, pending.result)

    def _send_message(self, message: Message) -> None:
        encoded = json.dumps(message, separators=(",", ":")) + "\n"
        with self._write_lock:
            with self._state_lock:
                process = self._process
            if process is None or process.stdin is None:
                raise CodexConnectionClosedError("App-server connection is unavailable")
            try:
                process.stdin.write(encoded)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                raise CodexConnectionClosedError(
                    "App-server connection is unavailable"
                ) from None

    def _reader_loop(self) -> None:
        with self._state_lock:
            process = self._process
        if process is None or process.stdout is None:
            self._fail_connection(
                CodexConnectionClosedError("App-server connection is unavailable")
            )
            return

        while True:
            try:
                line = process.stdout.readline()
            except (OSError, ValueError):
                with self._state_lock:
                    closing = self._state in {
                        _ClientState.CLOSING,
                        _ClientState.CLOSED,
                    }
                if not closing:
                    self._fail_connection(
                        CodexConnectionClosedError(
                            "App-server output closed unexpectedly"
                        )
                    )
                return

            if line == "":
                with self._state_lock:
                    closing = self._state in {
                        _ClientState.CLOSING,
                        _ClientState.CLOSED,
                    }
                if not closing:
                    self._fail_connection(
                        CodexConnectionClosedError(
                            "App-server output closed unexpectedly"
                        )
                    )
                return

            try:
                decoded = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._fail_connection(
                    CodexMalformedMessageError(
                        "App-server emitted malformed JSON"
                    )
                )
                return

            if not isinstance(decoded, dict):
                self._fail_connection(
                    CodexMalformedMessageError(
                        "App-server emitted a non-object message"
                    )
                )
                return

            self._dispatch_message(cast(Message, decoded))

    def _dispatch_message(self, message: Message) -> None:
        request_id = message.get("id", _MISSING)
        if isinstance(request_id, int) and not isinstance(request_id, bool):
            with self._state_lock:
                pending = self._pending.pop(request_id, None)
            if pending is None:
                return

            if "error" in message:
                pending.error = CodexProtocolError(
                    "App-server returned a protocol error"
                )
            elif "result" not in message:
                pending.error = CodexProtocolError(
                    "App-server response did not contain a result"
                )
            else:
                try:
                    pending.result = pending.parser(message["result"])
                except CodexClientError as error:
                    pending.error = error
                except Exception:
                    pending.error = CodexProtocolError(
                        "App-server response validation failed"
                    )
            pending.event.set()
            return

        if message.get("method") == "account/rateLimits/updated":
            try:
                update = parse_rate_limit_result(message.get("params"))
            except CodexClientError as error:
                self._fail_connection(error)
                return
            with self._state_lock:
                self._updates.append(update)
            return

        if message.get("method") == "account/login/completed":
            try:
                completion = parse_login_completion(message.get("params"))
            except CodexClientError:
                return
            with self._state_lock:
                self._login_completions.append(completion)

    def _fail_connection(self, error: CodexClientError) -> None:
        with self._state_lock:
            if self._state not in {_ClientState.CLOSING, _ClientState.CLOSED}:
                self._state = _ClientState.FAILED
            self._fail_pending_locked(error)

    def _fail_pending_locked(self, error: CodexClientError) -> None:
        pending_requests = tuple(self._pending.values())
        self._pending.clear()
        for pending in pending_requests:
            pending.error = error
            pending.event.set()

    def _close_after_start_failure(self) -> None:
        try:
            self.close()
        except CodexShutdownError:
            pass

    def _stop_process(self, process: ProcessLike) -> None:
        if self._wait_for_process(process):
            return

        try:
            process.terminate()
        except (OSError, ValueError):
            pass
        if self._wait_for_process(process):
            return

        try:
            process.kill()
        except (OSError, ValueError):
            raise CodexShutdownError(
                "Codex app-server could not be forcefully terminated"
            ) from None
        if not self._wait_for_process(process):
            raise CodexShutdownError(
                "Codex app-server did not stop after forced termination"
            )

    def _wait_for_process(self, process: ProcessLike) -> bool:
        try:
            process.wait(timeout=self._shutdown_timeout)
            return True
        except subprocess.TimeoutExpired:
            return False
        except (OSError, ValueError):
            raise CodexShutdownError(
                "Codex app-server process state could not be determined"
            ) from None

    def _dispose_unusable_process(self, process: ProcessLike) -> None:
        self._discard_process(process)

    def _discard_process(self, process: ProcessLike) -> None:
        self._close_stream(process.stdin)
        try:
            self._stop_process(process)
        except CodexShutdownError:
            pass
        self._close_stream(process.stdout)

    @staticmethod
    def _close_stream(stream: IO[str] | None) -> None:
        if stream is None:
            return
        try:
            stream.close()
        except (OSError, ValueError):
            pass
