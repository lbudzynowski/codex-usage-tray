"""Tests for secret-safe, in-memory pairing state."""

import unittest

from codex_usage_tray.pairing import (
    PairingArtifact,
    PairingError,
    PairingSession,
    PairingState,
    confirmed_qr_payload,
)


TEST_MANUAL_CODE = "TEST-CODE-ONLY"
TEST_PAIRING_PAYLOAD = "test-pairing-payload-not-valid"


class PairingArtifactTests(unittest.TestCase):
    def test_parses_optional_fields_and_expiry(self) -> None:
        artifact = PairingArtifact.from_mapping(
            {
                "manualPairingCode": TEST_MANUAL_CODE,
                "pairingCode": TEST_PAIRING_PAYLOAD,
                "expiresAt": 1_000,
                "futureField": "ignored",
            }
        )

        self.assertEqual(artifact.manual_code, TEST_MANUAL_CODE)
        self.assertEqual(artifact.seconds_remaining(990), 10)
        self.assertTrue(artifact.is_expired(1_000))

    def test_tolerates_missing_optional_pairing_payload_and_expiry(self) -> None:
        artifact = PairingArtifact.from_mapping(
            {"manualPairingCode": TEST_MANUAL_CODE}
        )

        self.assertIsNone(artifact.pairing_code)
        self.assertIsNone(artifact.seconds_remaining(10))

    def test_rejects_malformed_payload_without_echoing_it(self) -> None:
        with self.assertRaises(PairingError) as raised:
            PairingArtifact.from_mapping(
                {"manualPairingCode": TEST_MANUAL_CODE, "expiresAt": "later"}
            )

        self.assertNotIn(TEST_MANUAL_CODE, str(raised.exception))

    def test_repr_never_contains_pairing_artifacts(self) -> None:
        artifact = PairingArtifact(
            TEST_MANUAL_CODE, TEST_PAIRING_PAYLOAD, expires_at=100
        )

        representation = repr(artifact)
        self.assertNotIn(TEST_MANUAL_CODE, representation)
        self.assertNotIn(TEST_PAIRING_PAYLOAD, representation)

    def test_qr_is_disabled_without_a_confirmed_scanner_contract(self) -> None:
        artifact = PairingArtifact(TEST_MANUAL_CODE, TEST_PAIRING_PAYLOAD)
        self.assertIsNone(confirmed_qr_payload(artifact))


class PairingSessionTests(unittest.TestCase):
    def test_copy_uses_only_the_injected_clipboard_writer(self) -> None:
        copied: list[str] = []
        session = PairingSession(
            PairingArtifact(TEST_MANUAL_CODE, expires_at=9_999_999_999)
        )

        session.copy(copied.append)

        self.assertEqual(copied, [TEST_MANUAL_CODE])
        self.assertIs(session.state, PairingState.COPIED)

    def test_expiry_prevents_copy_and_updates_state(self) -> None:
        copied: list[str] = []
        session = PairingSession(PairingArtifact(TEST_MANUAL_CODE, expires_at=1))

        session.tick(now=2)
        session.copy(copied.append)

        self.assertEqual(copied, [])
        self.assertIs(session.state, PairingState.EXPIRED)


if __name__ == "__main__":
    unittest.main()
