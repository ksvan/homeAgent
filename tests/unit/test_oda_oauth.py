"""Unit tests for app.oda.oauth — PKCE, metadata discovery, DCR, token
exchange/refresh. All HTTP calls are faked; nothing here hits oda.com."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.oda import oauth


class _FakeResponse:
    def __init__(self, json_data: dict[str, Any], status_code: int = 200) -> None:
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://oda.com/")
            raise httpx.HTTPStatusError(
                "error", request=request, response=httpx.Response(self.status_code, request=request)
            )

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeAsyncClient:
    """Queue of canned responses returned in call order, regardless of URL."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []  # (method, url, kwargs)

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._responses.pop(0)

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self._responses.pop(0)


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, responses: list[_FakeResponse]
) -> _FakeAsyncClient:
    fake = _FakeAsyncClient(responses)
    monkeypatch.setattr(oauth.httpx, "AsyncClient", lambda **kwargs: fake)
    return fake


_RESOURCE_META = {
    "resource": "https://oda.com/mcp",
    "authorization_servers": ["https://oda.com/o"],
    "scopes_supported": ["mcp"],
}
_AS_META = {
    "issuer": "https://oda.com/o",
    "authorization_endpoint": "https://oda.com/o/authorize/",
    "token_endpoint": "https://oda.com/o/token/",
    "revocation_endpoint": "https://oda.com/o/revoke_token/",
    "registration_endpoint": "https://oda.com/o/register/",
}


class TestPkce:
    def test_generate_pkce_pair_matches_s256(self) -> None:
        verifier, challenge = oauth.generate_pkce_pair()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        assert challenge == expected
        assert "=" not in verifier
        assert "=" not in challenge

    def test_generate_pkce_pair_is_random(self) -> None:
        v1, _ = oauth.generate_pkce_pair()
        v2, _ = oauth.generate_pkce_pair()
        assert v1 != v2

    def test_generate_state_is_random(self) -> None:
        assert oauth.generate_state() != oauth.generate_state()


class TestDiscoverMetadata:
    async def test_discovers_endpoints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(
            monkeypatch,
            [_FakeResponse(_RESOURCE_META), _FakeResponse(_AS_META)],
        )
        metadata = await oauth.discover_metadata()
        assert metadata.authorization_endpoint == "https://oda.com/o/authorize/"
        assert metadata.token_endpoint == "https://oda.com/o/token/"
        assert metadata.registration_endpoint == "https://oda.com/o/register/"
        assert metadata.revocation_endpoint == "https://oda.com/o/revoke_token/"

    async def test_raises_when_no_authorization_servers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_client(monkeypatch, [_FakeResponse({"authorization_servers": []})])
        with pytest.raises(oauth.OdaOAuthError):
            await oauth.discover_metadata()

    async def test_raises_when_as_metadata_missing_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_client(
            monkeypatch,
            [_FakeResponse(_RESOURCE_META), _FakeResponse({"authorization_endpoint": "x"})],
        )
        with pytest.raises(oauth.OdaOAuthError):
            await oauth.discover_metadata()

    async def test_raises_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, [_FakeResponse({}, status_code=500)])
        with pytest.raises(oauth.OdaOAuthError):
            await oauth.discover_metadata()


_METADATA = oauth.OAuthServerMetadata(
    authorization_endpoint="https://oda.com/o/authorize/",
    token_endpoint="https://oda.com/o/token/",
    revocation_endpoint="https://oda.com/o/revoke_token/",
    registration_endpoint="https://oda.com/o/register/",
)


class TestRegisterClient:
    async def test_returns_client_id_and_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(
            monkeypatch,
            [_FakeResponse({"client_id": "abc123", "client_secret": "shh"})],
        )
        client_id, client_secret = await oauth.register_client(
            _METADATA, "https://home.example.com/integrations/oda/callback"
        )
        assert client_id == "abc123"
        assert client_secret == "shh"

    async def test_public_client_has_empty_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, [_FakeResponse({"client_id": "abc123"})])
        _client_id, client_secret = await oauth.register_client(_METADATA, "https://x/callback")
        assert client_secret == ""

    async def test_raises_when_response_missing_client_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_client(monkeypatch, [_FakeResponse({})])
        with pytest.raises(oauth.OdaOAuthError):
            await oauth.register_client(_METADATA, "https://x/callback")


class TestBuildAuthorizeUrl:
    def test_includes_all_required_params(self) -> None:
        url = oauth.build_authorize_url(
            _METADATA,
            client_id="abc123",
            redirect_uri="https://home.example.com/integrations/oda/callback",
            state="state-value",
            code_challenge="challenge-value",
        )
        assert url.startswith("https://oda.com/o/authorize/?")
        assert "response_type=code" in url
        assert "client_id=abc123" in url
        assert "state=state-value" in url
        assert "code_challenge=challenge-value" in url
        assert "code_challenge_method=S256" in url
        assert "scope=mcp" in url


class TestExchangeCode:
    async def test_returns_token_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(
            monkeypatch,
            [_FakeResponse({"access_token": "at", "refresh_token": "rt", "expires_in": 3600})],
        )
        before = datetime.now(timezone.utc)
        result = await oauth.exchange_code(
            _METADATA,
            client_id="abc123",
            client_secret="",
            code="auth-code",
            redirect_uri="https://x/callback",
            code_verifier="verifier",
        )
        assert result.access_token == "at"
        assert result.refresh_token == "rt"
        assert result.expires_at > before
        _method, _url, kwargs = fake.calls[0]
        assert kwargs["data"]["grant_type"] == "authorization_code"
        assert kwargs["data"]["code_verifier"] == "verifier"
        assert "client_secret" not in kwargs["data"]

    async def test_includes_client_secret_when_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _patch_client(
            monkeypatch,
            [_FakeResponse({"access_token": "at", "expires_in": 3600})],
        )
        await oauth.exchange_code(
            _METADATA,
            client_id="abc123",
            client_secret="topsecret",
            code="auth-code",
            redirect_uri="https://x/callback",
            code_verifier="verifier",
        )
        _method, _url, kwargs = fake.calls[0]
        assert kwargs["data"]["client_secret"] == "topsecret"

    async def test_raises_when_missing_access_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, [_FakeResponse({})])
        with pytest.raises(oauth.OdaOAuthError):
            await oauth.exchange_code(
                _METADATA,
                client_id="abc123",
                client_secret="",
                code="auth-code",
                redirect_uri="https://x/callback",
                code_verifier="verifier",
            )


class TestRefreshAccessToken:
    async def test_returns_new_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(
            monkeypatch,
            [
                _FakeResponse(
                    {"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 60}
                )
            ],
        )
        result = await oauth.refresh_access_token(
            _METADATA, client_id="abc123", client_secret="", refresh_token="old-rt"
        )
        assert result.access_token == "new-at"
        assert result.refresh_token == "new-rt"
        _method, _url, kwargs = fake.calls[0]
        assert kwargs["data"]["grant_type"] == "refresh_token"
        assert kwargs["data"]["refresh_token"] == "old-rt"

    async def test_raises_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, [_FakeResponse({}, status_code=401)])
        with pytest.raises(oauth.OdaOAuthError):
            await oauth.refresh_access_token(
                _METADATA, client_id="abc123", client_secret="", refresh_token="old-rt"
            )


class TestRevokeToken:
    async def test_posts_token_and_client_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch, [_FakeResponse({})])
        await oauth.revoke_token(
            _METADATA, client_id="abc123", client_secret="", token="refresh-token-value"
        )
        _method, url, kwargs = fake.calls[0]
        assert url == _METADATA.revocation_endpoint
        assert kwargs["data"]["token"] == "refresh-token-value"
        assert kwargs["data"]["client_id"] == "abc123"
        assert "client_secret" not in kwargs["data"]

    async def test_includes_client_secret_when_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _patch_client(monkeypatch, [_FakeResponse({})])
        await oauth.revoke_token(
            _METADATA, client_id="abc123", client_secret="shh", token="refresh-token-value"
        )
        _method, _url, kwargs = fake.calls[0]
        assert kwargs["data"]["client_secret"] == "shh"

    async def test_noop_when_no_revocation_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch, [])
        no_revoke = oauth.OAuthServerMetadata(
            authorization_endpoint=_METADATA.authorization_endpoint,
            token_endpoint=_METADATA.token_endpoint,
            revocation_endpoint="",
            registration_endpoint=_METADATA.registration_endpoint,
        )
        await oauth.revoke_token(
            no_revoke, client_id="abc123", client_secret="", token="refresh-token-value"
        )
        assert fake.calls == []

    async def test_noop_when_no_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(monkeypatch, [])
        await oauth.revoke_token(_METADATA, client_id="abc123", client_secret="", token="")
        assert fake.calls == []

    async def test_raises_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, [_FakeResponse({}, status_code=500)])
        with pytest.raises(oauth.OdaOAuthError):
            await oauth.revoke_token(
                _METADATA, client_id="abc123", client_secret="", token="refresh-token-value"
            )
