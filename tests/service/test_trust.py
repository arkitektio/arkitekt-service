"""The service JWT: signing, verifying, and the trust bundle (inline, or fetched and cached)."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from joserfc.jwk import OKPKey

from arkitekt_service import trust

ME = "live.arkitekt.testsvc"


def _verify(header, *, method="POST", path="/p", body=b"x", audience="live.arkitekt.rekuest"):
    return trust.verify(method, path, body, header, audience=audience)


def test_a_signed_request_verifies_as_its_sender():
    verified = _verify(trust.sign("POST", "/p", b"x", issuer=ME, audience="live.arkitekt.rekuest"))
    assert verified.issuer == ME and verified.jti


@pytest.mark.parametrize(
    "change, message",
    [
        ({"method": "GET"}, "another request"),
        ({"path": "/q"}, "another request"),
        ({"body": b"y"}, "another body"),
        ({"audience": "live.arkitekt.mikro"}, "not 'live.arkitekt.mikro'"),
    ],
)
def test_a_token_only_fits_the_request_it_was_signed_for(change, message):
    header = trust.sign("POST", "/p", b"x", issuer=ME, audience="live.arkitekt.rekuest")
    with pytest.raises(trust.TrustError, match=message):
        _verify(header, **change)


def test_a_key_can_only_speak_for_its_own_service():
    header = trust.sign("POST", "/p", b"x", issuer="live.arkitekt.rekuest", audience="live.arkitekt.rekuest")
    with pytest.raises(trust.TrustError, match="belongs to"):
        _verify(header)


def test_an_expired_token_is_refused(monkeypatch):
    header = trust.sign("POST", "/p", b"x", issuer=ME, audience="live.arkitekt.rekuest")
    later = time.time() + trust.LIFETIME_SECONDS + 120
    monkeypatch.setattr(trust.time, "time", lambda: later)
    with pytest.raises(trust.TrustError, match="expired"):
        _verify(header)


def test_a_missing_or_foreign_header_is_refused():
    with pytest.raises(trust.TrustError):
        _verify(None)
    with pytest.raises(trust.TrustError):
        _verify("Bearer abc")


def test_the_raw_public_key_is_the_32_byte_challenge_key():
    import base64

    key = OKPKey.generate_key("Ed25519")
    assert len(base64.b64decode(trust.raw_public_key_b64(key))) == 32


class _Bundle:
    """A coord stand-in serving a JWKS that can change (rotation)."""

    def __init__(self, keys):
        self.jwks = {"keys": keys}
        self.hits = 0
        bundle = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                bundle.hits += 1
                body = json.dumps(bundle.jwks).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/.well-known/hub-keys/1"


def test_a_fetched_bundle_is_cached_and_refetched_for_a_new_key(settings, monkeypatch):
    old, new = OKPKey.generate_key("Ed25519"), OKPKey.generate_key("Ed25519")
    bundle = _Bundle([{**trust.public_jwk(old), "service": "live.arkitekt.mikro"}])
    settings.INSTANCE = {"PRIVATE_KEY": settings.INSTANCE["PRIVATE_KEY"], "TRUST_JWKS_URI": bundle.url}
    monkeypatch.setattr(trust, "REFETCH_MIN_SECONDS", 0)
    try:
        signed_old = trust.sign("POST", "/p", b"x", issuer="live.arkitekt.mikro", audience="live.arkitekt.rekuest", key=old)
        assert _verify(signed_old).issuer == "live.arkitekt.mikro"
        assert _verify(signed_old).issuer == "live.arkitekt.mikro"
        assert bundle.hits == 1  # cached

        # mikro was re-enrolled with a new key: an unknown kid triggers one refetch.
        bundle.jwks = {"keys": [{**trust.public_jwk(new), "service": "live.arkitekt.mikro"}]}
        signed_new = trust.sign("POST", "/p", b"x", issuer="live.arkitekt.mikro", audience="live.arkitekt.rekuest", key=new)
        assert _verify(signed_new).issuer == "live.arkitekt.mikro"
        assert bundle.hits == 2
    finally:
        bundle.server.shutdown()
