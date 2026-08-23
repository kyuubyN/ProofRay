# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Coverage for the machine-bound bearer token (machine_auth.py) and the token-bucket rate
limiter (rate_limit.py) wired into api/server.py's `before_request` gate."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault(
    "HORIZON_API_CREDENTIALS_PATH",
    str(Path(tempfile.gettempdir()) / "horizon-memory-test-credentials.json"))

import machine_auth  # noqa: E402
from rate_limit import RateLimiter, TokenBucket  # noqa: E402
from server import CREDENTIALS, STORE, app  # noqa: E402
import server as server_module  # noqa: E402

DOCUMENTS = [
    "The Meridian project reduced compute cost by exactly 42 percent compared to the "
    "previous baseline architecture across every workload.",
]
QUESTION = "What percent did the Meridian project reduce cost by?"


class BearerTokenGateTests(unittest.TestCase):
    def setUp(self):
        STORE.clear()
        server_module.RATE_LIMITER.reset()
        app.testing = True
        self.client = app.test_client()

    def _post(self):
        return self.client.post("/v1/answers", json={"question": QUESTION, "documents": DOCUMENTS})

    def test_missing_authorization_header_is_rejected(self):
        response = self._post()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["type"], "auth_error")

    def test_wrong_token_is_rejected(self):
        self.client.environ_base["HTTP_AUTHORIZATION"] = "Bearer not-the-real-token"
        self.assertEqual(self._post().status_code, 401)

    def test_malformed_authorization_header_is_rejected(self):
        self.client.environ_base["HTTP_AUTHORIZATION"] = CREDENTIALS["token"]  # missing "Bearer "
        self.assertEqual(self._post().status_code, 401)

    def test_correct_token_is_accepted(self):
        self.client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {CREDENTIALS['token']}"
        self.assertEqual(self._post().status_code, 201)

    def test_health_check_requires_no_token(self):
        response = self.client.get("/v1/health")
        self.assertEqual(response.status_code, 200)

    def test_get_answer_also_requires_the_token(self):
        self.client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {CREDENTIALS['token']}"
        answer_id = self._post().get_json()["id"]
        del self.client.environ_base["HTTP_AUTHORIZATION"]
        self.assertEqual(self.client.get(f"/v1/answers/{answer_id}").status_code, 401)


class MachineFingerprintTests(unittest.TestCase):
    """The token alone is not enough -- a credentials file copied to a different machine (a
    different recomputed fingerprint) must not authenticate, even with the right token."""

    def test_mismatched_fingerprint_rejects_an_otherwise_correct_token(self):
        credentials = {"token": "abc123", "machine_fingerprint": "not-this-machine-at-all"}
        original = machine_auth.raw_machine_identifier
        machine_auth.raw_machine_identifier = lambda: "a-real-machine-id"
        try:
            self.assertFalse(
                machine_auth.verify_bearer_token("Bearer abc123", credentials))
        finally:
            machine_auth.raw_machine_identifier = original

    def test_matching_fingerprint_accepts_the_token(self):
        raw_id = "a-real-machine-id"
        credentials = {"token": "abc123", "machine_fingerprint": machine_auth.machine_fingerprint(raw_id)}
        original = machine_auth.raw_machine_identifier
        machine_auth.raw_machine_identifier = lambda: raw_id
        try:
            self.assertTrue(
                machine_auth.verify_bearer_token("Bearer abc123", credentials))
        finally:
            machine_auth.raw_machine_identifier = original

    def test_unavailable_machine_id_skips_the_fingerprint_check(self):
        # Some containers/CI images expose no OS machine-id -- the token alone still gates access
        # rather than locking everyone out.
        credentials = {"token": "abc123", "machine_fingerprint": "irrelevant-when-unavailable"}
        original = machine_auth.raw_machine_identifier
        machine_auth.raw_machine_identifier = lambda: None
        try:
            self.assertTrue(
                machine_auth.verify_bearer_token("Bearer abc123", credentials))
        finally:
            machine_auth.raw_machine_identifier = original


class TokenBucketUnitTests(unittest.TestCase):
    """Direct unit tests of the refill math, independent of Flask or wall-clock sleeps."""

    def test_denies_once_capacity_is_exhausted(self):
        bucket = TokenBucket(capacity=2, refill_per_second=0.0)
        self.assertTrue(bucket.allow())
        self.assertTrue(bucket.allow())
        self.assertFalse(bucket.allow())

    def test_refills_continuously_rather_than_resetting_at_a_fixed_tick(self):
        bucket = TokenBucket(capacity=1, refill_per_second=1.0)
        self.assertTrue(bucket.allow())
        self.assertFalse(bucket.allow())
        bucket.last_check -= 1.0  # simulate one second having elapsed, no real sleep needed
        self.assertTrue(bucket.allow())

    def test_never_exceeds_its_own_capacity(self):
        bucket = TokenBucket(capacity=3, refill_per_second=100.0)
        bucket.last_check -= 1000.0  # a huge elapsed gap must still cap at `capacity`, not overflow
        self.assertEqual(bucket.tokens, 3)  # unchanged until the next `allow()` call recomputes
        bucket.allow()
        self.assertLessEqual(bucket.tokens, 3)


class RateLimiterTests(unittest.TestCase):
    def test_distinct_keys_get_independent_buckets(self):
        limiter = RateLimiter(per_minute=1)
        self.assertTrue(limiter.allow("1.2.3.4"))
        self.assertFalse(limiter.allow("1.2.3.4"))
        self.assertTrue(limiter.allow("5.6.7.8"))  # a different key is not penalized

    def test_reset_clears_every_tracked_bucket(self):
        limiter = RateLimiter(per_minute=1)
        limiter.allow("1.2.3.4")
        self.assertFalse(limiter.allow("1.2.3.4"))
        limiter.reset()
        self.assertTrue(limiter.allow("1.2.3.4"))


class FlaskRateLimitIntegrationTests(unittest.TestCase):
    """A real, low capacity, exercised through the actual before_request gate."""

    def setUp(self):
        STORE.clear()
        self._original_limiter = server_module.RATE_LIMITER
        server_module.RATE_LIMITER = RateLimiter(per_minute=2)
        app.testing = True
        self.client = app.test_client()
        self.client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {CREDENTIALS['token']}"

    def tearDown(self):
        server_module.RATE_LIMITER = self._original_limiter

    def test_exceeding_capacity_returns_429(self):
        for _ in range(2):
            response = self.client.post(
                "/v1/answers", json={"question": QUESTION, "documents": DOCUMENTS})
            self.assertEqual(response.status_code, 201)
        limited = self.client.post(
            "/v1/answers", json={"question": QUESTION, "documents": DOCUMENTS})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json()["error"]["type"], "rate_limit_error")

    def test_health_check_is_also_rate_limited(self):
        for _ in range(2):
            self.assertEqual(self.client.get("/v1/health").status_code, 200)
        self.assertEqual(self.client.get("/v1/health").status_code, 429)


if __name__ == "__main__":
    unittest.main()
