"""In-memory Remote Control pairing state.

Pairing artifacts are deliberately excluded from representations.  Callers
must not log command stdout or instances of :class:`PairingArtifact`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from time import time


class PairingError(RuntimeError):
    """A sanitized pairing failure that never includes a pairing artifact."""


@dataclass(frozen=True, slots=True)
class PairingArtifact:
    manual_code: str = field(repr=False)
    pairing_code: str | None = field(default=None, repr=False)
    expires_at: int | None = None

    @classmethod
    def from_mapping(cls, payload: object) -> "PairingArtifact":
        if not isinstance(payload, Mapping):
            raise PairingError("Pairing response was not a JSON object")
        manual_code = payload.get("manualPairingCode")
        if not isinstance(manual_code, str) or not manual_code.strip():
            raise PairingError("Pairing response did not include a manual code")
        pairing_code = payload.get("pairingCode")
        if pairing_code is not None and (
            not isinstance(pairing_code, str) or not pairing_code
        ):
            raise PairingError("Pairing response contained an invalid pairing payload")
        expires_at = payload.get("expiresAt")
        if expires_at is not None and (
            isinstance(expires_at, bool) or not isinstance(expires_at, int)
        ):
            raise PairingError("Pairing response contained an invalid expiry time")
        return cls(
            manual_code=manual_code,
            pairing_code=pairing_code,
            expires_at=expires_at,
        )

    def seconds_remaining(self, now: float | None = None) -> int | None:
        if self.expires_at is None:
            return None
        current = time() if now is None else now
        return max(0, int(self.expires_at - current))

    def is_expired(self, now: float | None = None) -> bool:
        remaining = self.seconds_remaining(now)
        return remaining == 0 if remaining is not None else False


class PairingState(Enum):
    READY = "ready"
    COPIED = "copied"
    CLAIMED = "claimed"
    EXPIRED = "expired"
    ERROR = "error"


class PairingSession:
    """Small testable state machine for one in-memory pairing artifact."""

    def __init__(self, artifact: PairingArtifact) -> None:
        self._artifact = artifact
        self.state = PairingState.READY

    @property
    def artifact(self) -> PairingArtifact:
        return self._artifact

    def copy(self, clipboard_writer: Callable[[str], None]) -> None:
        if self._artifact.is_expired():
            self.state = PairingState.EXPIRED
            return
        clipboard_writer(self._artifact.manual_code)
        self.state = PairingState.COPIED

    def tick(self, now: float | None = None) -> int | None:
        remaining = self._artifact.seconds_remaining(now)
        if remaining == 0 and self.state not in (
            PairingState.CLAIMED,
            PairingState.ERROR,
        ):
            self.state = PairingState.EXPIRED
        return remaining

    def mark_claimed(self) -> None:
        if self.state is not PairingState.EXPIRED:
            self.state = PairingState.CLAIMED

    def mark_error(self) -> None:
        if self.state not in (PairingState.CLAIMED, PairingState.EXPIRED):
            self.state = PairingState.ERROR


def confirmed_qr_payload(_artifact: PairingArtifact) -> None:
    """Return no QR payload until OpenAI documents the scanner contract.

    Codex 0.146 exposes an opaque ``pairingCode`` and a manual code, but its
    public protocol does not define either value as a QR wire payload.  Encoding
    either field would therefore be an unsupported guess.
    """

    return None
