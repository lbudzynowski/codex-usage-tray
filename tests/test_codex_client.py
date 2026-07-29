"""Tests for the narrow Codex app-server JSONL client."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
import unittest
from collections.abc import Callable
from typing import cast
from unittest.mock import patch

from codex_usage_tray.codex_client import (
    CodexAccount,
    CodexAccountState,
    CodexAppServerClient,
    CodexClientClosedError,
    CodexConnectionClosedError,
    CodexExecutableNotFoundError,
    CodexMalformedMessageError,
    CodexProcessStartError,
    CodexProtocolError,
    CodexRequestTimeoutError,
    CodexLoginCompletion,
    CodexLoginStart,
    RateLimitSnapshots,
    build_codex_command,
    parse_account_result,
    parse_login_start_result,
    parse_rate_limit_result,
)
from codex_usage_tray.codex_executable import CodexExecutableResolutionError
from codex_usage_tray.models import RateLimitSnapshot, RateLimitWindow

_EOF = object()
_INVALID_TIMEOUTS: tuple[object, ...] = (
    float("nan"),
    float("inf"),
    float("-inf"),
    0,
    -0.1,
    True,
    "1",
)


class _FakeStdout:
    def __init__(self) -> None:
        self._lines: queue.Queue[str | object] = queue.Queue()
        self._closed = False

    def readline(self) -> str:
        line = self._lines.get()
        if line is _EOF:
            return ""
        return cast(str, line)

    def feed(self, line: str) -> None:
        self._lines.put(line)

    def feed_eof(self) -> None:
        self._lines.put(_EOF)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.feed_eof()


class _FakeStdin:
    def __init__(self, on_close: Callable[[], None]) -> None:
        self._on_close = on_close
        self._messages: queue.Queue[str] = queue.Queue()
        self.closed = False

    def write(self, data: str) -> int:
        if self.closed:
            raise BrokenPipeError
        self._messages.put(data)
        return len(data)

    def flush(self) -> None:
        if self.closed:
            raise BrokenPipeError

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._on_close()

    def next_message(self, timeout: float = 0.5) -> dict[str, object]:
        encoded = self._messages.get(timeout=timeout)
        return cast(dict[str, object], json.loads(encoded))

    def assert_no_message(self, testcase: unittest.TestCase) -> None:
        with testcase.assertRaises(queue.Empty):
            self._messages.get(timeout=0.02)


class _FakeProcess:
    def __init__(
        self,
        *,
        exit_on_stdin_close: bool = True,
        exit_on_terminate: bool = True,
    ) -> None:
        self.stdout = _FakeStdout()
        self.stdin = _FakeStdin(self._stdin_closed)
        self.returncode: int | None = None
        self.exit_on_stdin_close = exit_on_stdin_close
        self.exit_on_terminate = exit_on_terminate
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-codex", timeout)
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.exit_on_terminate:
            self._exit(-15)

    def kill(self) -> None:
        self.kill_calls += 1
        self._exit(-9)

    def send_message(self, message: dict[str, object]) -> None:
        self.stdout.feed(json.dumps(message) + "\n")

    def send_raw(self, line: str) -> None:
        self.stdout.feed(line)

    def send_eof(self) -> None:
        self.stdout.feed_eof()

    def next_client_message(self, timeout: float = 0.5) -> dict[str, object]:
        return self.stdin.next_message(timeout)

    def _stdin_closed(self) -> None:
        if self.exit_on_stdin_close:
            self._exit(0)

    def _exit(self, returncode: int) -> None:
        if self.returncode is None:
            self.returncode = returncode
            self.stdout.feed_eof()


class _CapturingFactory:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: tuple[str, ...]) -> _FakeProcess:
        self.commands.append(command)
        return self.process


def _live_snapshot(
    *, used_percent: int = 4, limit_id: str = "codex"
) -> dict[str, object]:
    return {
        "limitId": limit_id,
        "limitName": None,
        "primary": {
            "usedPercent": used_percent,
            "windowDurationMins": 10_080,
            "resetsAt": 1_784_889_359,
        },
        "secondary": None,
        "rateLimitReachedType": None,
    }


def _start_in_thread(
    client: CodexAppServerClient,
) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            client.start()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


class CodexClientTestCase(unittest.TestCase):
    def create_client(
        self,
        process: _FakeProcess | None = None,
        **kwargs: object,
    ) -> tuple[CodexAppServerClient, _FakeProcess, _CapturingFactory]:
        fake_process = process or _FakeProcess()
        factory = _CapturingFactory(fake_process)
        client = CodexAppServerClient(
            command=("fake-codex", "-c", "analytics.enabled=false", "app-server"),
            process_factory=factory,
            request_timeout=0.2,
            shutdown_timeout=0.01,
            **kwargs,
        )
        return client, fake_process, factory

    def finish_start(
        self, client: CodexAppServerClient, process: _FakeProcess
    ) -> dict[str, object]:
        thread, errors = _start_in_thread(client)
        initialize = process.next_client_message()
        process.send_message({"id": initialize["id"], "result": {"ignored": True}})
        thread.join(timeout=0.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        initialized = process.next_client_message()
        self.assertTrue(client.is_ready)
        return initialized


class ProtocolOrderingTests(CodexClientTestCase):
    def test_initialize_initialized_and_account_request_ordering(self) -> None:
        client, process, factory = self.create_client()
        self.addCleanup(client.close)

        thread, errors = _start_in_thread(client)
        initialize = process.next_client_message()

        self.assertEqual(initialize["method"], "initialize")
        self.assertNotIn("jsonrpc", initialize)
        self.assertEqual(
            initialize["params"],
            {
                "clientInfo": {
                    "name": "codex_usage_tray",
                    "title": "Codex Usage Tray",
                    "version": "0.0.0",
                }
            },
        )
        process.stdin.assert_no_message(self)

        process.send_message({"id": initialize["id"], "result": {}})
        thread.join(timeout=0.5)
        self.assertEqual(errors, [])

        initialized = process.next_client_message()
        self.assertEqual(initialized, {"method": "initialized", "params": {}})
        self.assertNotIn("jsonrpc", initialized)

        result: list[RateLimitSnapshots] = []
        read_thread = threading.Thread(
            target=lambda: result.append(client.read_rate_limits())
        )
        read_thread.start()
        account_request = process.next_client_message()
        self.assertEqual(account_request["method"], "account/rateLimits/read")
        self.assertNotIn("params", account_request)
        self.assertNotIn("jsonrpc", account_request)

        process.send_message(
            {"id": account_request["id"], "result": {"rateLimits": None}}
        )
        read_thread.join(timeout=0.5)
        self.assertEqual(result, [()])
        self.assertEqual(
            factory.commands,
            [("fake-codex", "-c", "analytics.enabled=false", "app-server")],
        )

    def test_correlates_concurrent_responses_by_request_id(self) -> None:
        client, process, _ = self.create_client()
        self.addCleanup(client.close)
        self.finish_start(client, process)

        results: dict[str, RateLimitSnapshots] = {}
        first_thread = threading.Thread(
            target=lambda: results.__setitem__("first", client.read_rate_limits())
        )
        first_thread.start()
        first_request = process.next_client_message()

        second_thread = threading.Thread(
            target=lambda: results.__setitem__("second", client.read_rate_limits())
        )
        second_thread.start()
        second_request = process.next_client_message()

        process.send_message(
            {
                "id": second_request["id"],
                "result": {"rateLimits": _live_snapshot(used_percent=60)},
            }
        )
        process.send_message(
            {
                "id": first_request["id"],
                "result": {"rateLimits": _live_snapshot(used_percent=10)},
            }
        )
        first_thread.join(timeout=0.5)
        second_thread.join(timeout=0.5)

        self.assertEqual(
            results["first"][0].primary,
            RateLimitWindow(10, 10_080, 1_784_889_359),
        )
        self.assertEqual(
            results["second"][0].primary,
            RateLimitWindow(60, 10_080, 1_784_889_359),
        )


class AccountParsingTests(CodexClientTestCase):
    def test_parses_signed_out_account_response(self) -> None:
        self.assertEqual(parse_account_result({"account": None, "requiresOpenaiAuth": True}), CodexAccountState(None, True))

    def test_parses_chatgpt_account_with_email_and_plan(self) -> None:
        self.assertEqual(parse_account_result({"account": {"type": "chatgpt", "email": "user@example.test", "planType": "plus"}, "requiresOpenaiAuth": True}), CodexAccountState(CodexAccount("chatgpt", "user@example.test", "plus"), True))

    def test_parses_account_with_missing_optional_email(self) -> None:
        self.assertEqual(parse_account_result({"account": {"type": "chatgpt", "planType": "enterprise"}, "requiresOpenaiAuth": True}).account, CodexAccount("chatgpt", None, "enterprise"))

    def test_rejects_malformed_account_responses(self) -> None:
        for payload in ({}, {"account": {}, "requiresOpenaiAuth": True}, {"account": {"type": "chatgpt", "email": 3}, "requiresOpenaiAuth": True}):
            with self.subTest(payload=payload):
                with self.assertRaises(CodexProtocolError):
                    parse_account_result(payload)

    def test_parses_login_start_response(self) -> None:
        self.assertEqual(parse_login_start_result({"type": "chatgpt", "loginId": "login-1", "authUrl": "https://chatgpt.com/auth"}), CodexLoginStart("login-1", "https://chatgpt.com/auth"))



    def test_account_read_request_shape(self) -> None:
        client, process, _ = self.create_client()
        self.addCleanup(client.close)
        self.finish_start(client, process)
        result: list[CodexAccountState] = []
        thread = threading.Thread(target=lambda: result.append(client.read_account()))
        thread.start()
        request = process.next_client_message()
        self.assertEqual(request["method"], "account/read")
        self.assertEqual(request["params"], {"refreshToken": False})
        process.send_message({"id": request["id"], "result": {"account": None, "requiresOpenaiAuth": True}})
        thread.join(timeout=0.5)
        self.assertEqual(result, [CodexAccountState(None, True)])

    def test_login_start_request_shape_and_completion_notification(self) -> None:
        client, process, _ = self.create_client()
        self.addCleanup(client.close)
        self.finish_start(client, process)
        result: list[CodexLoginStart] = []
        thread = threading.Thread(target=lambda: result.append(client.start_chatgpt_login()))
        thread.start()
        request = process.next_client_message()
        self.assertEqual(request["method"], "account/login/start")
        self.assertEqual(request["params"], {"type": "chatgpt"})
        process.send_message({"id": request["id"], "result": {"type": "chatgpt", "loginId": "login-1", "authUrl": "https://chatgpt.com/auth"}})
        thread.join(timeout=0.5)
        self.assertEqual(result, [CodexLoginStart("login-1", "https://chatgpt.com/auth")])
        process.send_message({"method": "account/login/completed", "params": {"loginId": "login-1", "success": False, "error": "private raw error"}})
        deadline = time.monotonic() + 0.5
        completion = None
        while completion is None and time.monotonic() < deadline:
            completion = client.pop_login_completion()
            if completion is None:
                time.sleep(0.001)
        self.assertEqual(completion, CodexLoginCompletion("login-1", False, True))


class RateLimitParsingTests(unittest.TestCase):
    def test_parses_the_sanitized_live_response_shape(self) -> None:
        snapshots = parse_rate_limit_result({"rateLimits": _live_snapshot()})

        self.assertEqual(
            snapshots,
            (
                RateLimitSnapshot(
                    limit_id="codex",
                    primary=RateLimitWindow(4, 10_080, 1_784_889_359),
                ),
            ),
        )
        self.assertIsNone(snapshots[0].secondary)

    def test_handles_rate_limits_by_limit_id_and_deduplicates_default(self) -> None:
        snapshot = _live_snapshot()
        snapshots = parse_rate_limit_result(
            {
                "rateLimits": snapshot,
                "rateLimitsByLimitId": {"codex": snapshot},
            }
        )

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].limit_id, "codex")

    def test_supports_multiple_distinct_limit_ids(self) -> None:
        snapshots = parse_rate_limit_result(
            {
                "rateLimits": _live_snapshot(),
                "rateLimitsByLimitId": {
                    "codex": _live_snapshot(),
                    "another-limit": {
                        "limitName": "Additional limit",
                        "primary": {
                            "usedPercent": 25,
                            "windowDurationMins": 300,
                            "resetsAt": 1_784_800_000,
                        },
                    },
                },
            }
        )

        self.assertEqual(
            [snapshot.limit_id for snapshot in snapshots],
            ["codex", "another-limit"],
        )
        self.assertEqual(snapshots[1].limit_name, "Additional limit")
        self.assertEqual(
            snapshots[1].primary,
            RateLimitWindow(25, 300, 1_784_800_000),
        )

    def test_tolerates_unknown_fields_without_retaining_them(self) -> None:
        snapshots = parse_rate_limit_result(
            {
                "privateAccountField": "ignored",
                "rateLimits": {
                    **_live_snapshot(),
                    "unknownSnapshotField": {"private": "ignored"},
                    "primary": {
                        "usedPercent": 4,
                        "windowDurationMins": 10_080,
                        "resetsAt": 1_784_889_359,
                        "unknownWindowField": "ignored",
                    },
                },
            }
        )

        self.assertEqual(snapshots[0].limit_id, "codex")
        self.assertEqual(
            snapshots[0].primary,
            RateLimitWindow(4, 10_080, 1_784_889_359),
        )


class NotificationTests(CodexClientTestCase):
    def test_accepts_and_parses_rate_limit_updated_notifications(self) -> None:
        client, process, _ = self.create_client()
        self.addCleanup(client.close)
        self.finish_start(client, process)

        process.send_message(
            {
                "method": "account/rateLimits/updated",
                "params": {"rateLimits": _live_snapshot(used_percent=12)},
            }
        )

        deadline = time.monotonic() + 0.5
        update = None
        while update is None and time.monotonic() < deadline:
            update = client.pop_rate_limit_update()
            if update is None:
                time.sleep(0.001)

        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(
            update[0].primary,
            RateLimitWindow(12, 10_080, 1_784_889_359),
        )


class FailureHandlingTests(CodexClientTestCase):
    def test_rejects_invalid_request_timeout_constructor_values(self) -> None:
        for value in _INVALID_TIMEOUTS:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "request_timeout must be a positive number",
                ):
                    CodexAppServerClient(
                        command=("fake-codex",),
                        request_timeout=cast(float, value),
                    )

    def test_rejects_invalid_shutdown_timeout_constructor_values(self) -> None:
        for value in _INVALID_TIMEOUTS:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "shutdown_timeout must be a positive number",
                ):
                    CodexAppServerClient(
                        command=("fake-codex",),
                        shutdown_timeout=cast(float, value),
                    )

    def test_invalid_per_request_timeout_does_not_send_a_request(self) -> None:
        client, process, _ = self.create_client()
        self.addCleanup(client.close)
        self.finish_start(client, process)

        for value in _INVALID_TIMEOUTS:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "timeout must be a positive number",
                ):
                    client.read_rate_limits(timeout=cast(float, value))
                process.stdin.assert_no_message(self)

    def test_malformed_json_during_initialization_is_sanitized(self) -> None:
        client, process, _ = self.create_client()
        thread, errors = _start_in_thread(client)
        process.next_client_message()
        process.send_raw("not-json\n")
        thread.join(timeout=0.5)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CodexMalformedMessageError)
        self.assertEqual(str(errors[0]), "App-server emitted malformed JSON")
        process.stdin.assert_no_message(self)

    def test_unexpected_eof_during_initialization_is_sanitized(self) -> None:
        client, process, _ = self.create_client()
        thread, errors = _start_in_thread(client)
        process.next_client_message()
        process.send_eof()
        thread.join(timeout=0.5)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CodexConnectionClosedError)
        self.assertNotIn("fake", str(errors[0]))

    def test_protocol_error_response_does_not_expose_raw_error_data(self) -> None:
        client, process, _ = self.create_client()
        self.addCleanup(client.close)
        self.finish_start(client, process)

        errors: list[BaseException] = []

        def read() -> None:
            try:
                client.read_rate_limits()
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=read)
        thread.start()
        request = process.next_client_message()
        process.send_message(
            {
                "id": request["id"],
                "error": {
                    "code": -32603,
                    "message": "private@example.test token-secret /private/path",
                },
            }
        )
        thread.join(timeout=0.5)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CodexProtocolError)
        self.assertEqual(str(errors[0]), "App-server returned a protocol error")

    def test_request_timeout_is_sanitized_and_removes_pending_request(self) -> None:
        client, process, _ = self.create_client()
        self.addCleanup(client.close)
        self.finish_start(client, process)

        with self.assertRaisesRegex(CodexRequestTimeoutError, "request timed out"):
            client.read_rate_limits(timeout=0.01)
        request = process.next_client_message()

        process.send_message(
            {"id": request["id"], "result": {"rateLimits": _live_snapshot()}}
        )
        self.assertIsNone(client.pop_rate_limit_update())

    def test_process_startup_failure_is_sanitized(self) -> None:
        def fail_to_start(command: tuple[str, ...]) -> _FakeProcess:
            raise OSError("private /home/example/account path")

        client = CodexAppServerClient(
            command=("fake-codex", "-c", "analytics.enabled=false", "app-server"),
            process_factory=fail_to_start,
        )

        with self.assertRaises(CodexProcessStartError) as raised:
            client.start()

        self.assertEqual(
            str(raised.exception), "Codex app-server process could not be started"
        )
        self.assertNotIn("/home", str(raised.exception))

    def test_shutdown_interrupts_a_pending_request(self) -> None:
        client, process, _ = self.create_client()
        self.finish_start(client, process)
        errors: list[BaseException] = []

        def read() -> None:
            try:
                client.read_rate_limits()
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=read)
        thread.start()
        process.next_client_message()
        client.close()
        thread.join(timeout=0.5)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CodexClientClosedError)


class LifecycleTests(CodexClientTestCase):
    def test_missing_path_executable_uses_a_sanitized_error(self) -> None:
        with patch(
            "codex_usage_tray.codex_executable.resolve_codex_executable",
            side_effect=CodexExecutableResolutionError(
                "private executable path must not escape"
            ),
        ):
            with self.assertRaises(CodexExecutableNotFoundError) as raised:
                build_codex_command()

        self.assertEqual(
            str(raised.exception),
            "Codex executable was not found in PATH",
        )

    def test_default_command_uses_path_lookup_and_disables_analytics(self) -> None:
        with patch(
            "codex_usage_tray.codex_executable.resolve_codex_executable",
            return_value="/resolved/codex",
        ) as resolver:
            command = build_codex_command()

        resolver.assert_called_once_with()
        self.assertEqual(
            command,
            ("/resolved/codex", "-c", "analytics.enabled=false", "app-server"),
        )

    def test_graceful_shutdown_closes_stdin_without_forced_signals(self) -> None:
        client, process, _ = self.create_client()
        self.finish_start(client, process)

        client.close()

        self.assertTrue(process.stdin.closed)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.terminate_calls, 0)
        self.assertEqual(process.kill_calls, 0)

    def test_forced_termination_is_bounded_when_graceful_shutdown_fails(self) -> None:
        process = _FakeProcess(
            exit_on_stdin_close=False,
            exit_on_terminate=False,
        )
        client, process, _ = self.create_client(process)
        self.finish_start(client, process)

        client.close()

        self.assertTrue(process.stdin.closed)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.returncode, -9)

    def test_shutdown_while_process_factory_is_returning_rejects_the_child(
        self,
    ) -> None:
        process = _FakeProcess()
        factory_entered = threading.Event()
        release_factory = threading.Event()

        def delayed_factory(command: tuple[str, ...]) -> _FakeProcess:
            factory_entered.set()
            release_factory.wait(timeout=0.5)
            return process

        client = CodexAppServerClient(
            command=("fake-codex", "-c", "analytics.enabled=false", "app-server"),
            process_factory=delayed_factory,
            shutdown_timeout=0.01,
        )
        thread, errors = _start_in_thread(client)
        self.assertTrue(factory_entered.wait(timeout=0.5))

        client.close()
        release_factory.set()
        thread.join(timeout=0.5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CodexClientClosedError)
        self.assertTrue(process.stdin.closed)
        self.assertIsNotNone(process.returncode)


if __name__ == "__main__":
    unittest.main()
