# =============================================================================
# FILE: tests/unit/test_auth.py
# WHAT: Unit tests for the verify_api_key FastAPI dependency.
# WHY:  Auth is the outermost layer of protection for the entire API.
#       If verify_api_key silently breaks, every endpoint becomes publicly
#       accessible — no DB change, no test failure, just an open API.
#       These tests guarantee the three conditions that must always hold:
#         1. Correct key  → request passes through (returns the key)
#         2. Wrong key    → 401, request blocked
#         3. Missing key  → 401, request blocked with a helpful message
# HOW WE TEST:
#   verify_api_key is a FastAPI dependency function — it's just an async
#   function that takes an api_key string and either returns it or raises
#   HTTPException. We call it directly without spinning up a FastAPI app.
#   This is fast (no HTTP overhead) and precise (tests only the auth logic).
# CONNECTED TO:
#   ← backend/api/dependencies/auth.py  — the function under test
#   ← backend/config.py                 — settings.api_secret_key
# =============================================================================

import pytest
from unittest.mock import patch
from fastapi import HTTPException

from api.dependencies.auth import verify_api_key


class TestVerifyApiKey:

    async def test_valid_key_passes(self):
        # When the correct key is provided, the function returns it.
        # The route handler then runs normally — no exception raised.
        with patch("api.dependencies.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "real-secret-key"
            result = await verify_api_key(api_key="real-secret-key")
        assert result == "real-secret-key"

    async def test_wrong_key_raises_401(self):
        # A key that doesn't match the configured secret must be rejected.
        # status_code 401 = Unauthorized — wrong credentials provided.
        with patch("api.dependencies.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "real-secret-key"
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(api_key="wrong-key")
        assert exc_info.value.status_code == 401

    async def test_missing_key_raises_401(self):
        # No header at all — FastAPI passes None when auto_error=False.
        # Must be rejected with 401 (not 422 or 500).
        with patch("api.dependencies.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "real-secret-key"
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(api_key=None)
        assert exc_info.value.status_code == 401

    async def test_empty_string_key_raises_401(self):
        # An empty X-API-Key header — should be treated the same as missing.
        with patch("api.dependencies.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "real-secret-key"
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(api_key="")
        assert exc_info.value.status_code == 401

    async def test_missing_key_error_message_is_helpful(self):
        # The error message should tell the caller exactly what to do.
        # An engineer hitting the API for the first time should understand
        # immediately — not get a generic "unauthorized" with no guidance.
        with patch("api.dependencies.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "real-secret-key"
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(api_key=None)
        assert "X-API-Key" in exc_info.value.detail

    async def test_wrong_key_does_not_leak_correct_key(self):
        # The error detail for a wrong key must NOT contain the actual secret.
        # Logging the provided key is fine (we do it at WARNING level),
        # but returning the correct key in the HTTP response is a data leak.
        with patch("api.dependencies.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "super-secret-value"
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(api_key="wrong-key")
        assert "super-secret-value" not in exc_info.value.detail

    async def test_key_comparison_is_exact(self):
        # "secret " (trailing space) must NOT match "secret".
        # Loose comparison would allow bypassing auth with padded keys.
        with patch("api.dependencies.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "secret"
            with pytest.raises(HTTPException):
                await verify_api_key(api_key="secret ")

    async def test_key_comparison_is_case_sensitive(self):
        # "Secret" must NOT match "secret".
        # Case-insensitive comparison would halve the key space.
        with patch("api.dependencies.auth.settings") as mock_settings:
            mock_settings.api_secret_key = "secret"
            with pytest.raises(HTTPException):
                await verify_api_key(api_key="SECRET")
