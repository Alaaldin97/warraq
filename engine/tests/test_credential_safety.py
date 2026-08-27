"""Regression tests for the credential-handling hardening.

These cover behaviour that is easy to undo by accident and whose failure is
silent: an endpoint downgraded to http, a poll URL pointing somewhere other
than the configured resource, or the Azure CLI being resolved from the
current directory. Each of these leaks or misuses a credential rather than
raising, so they need explicit tests.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from kbo import azure_ocr, rpc  # noqa: E402


# --------------------------------------------------------------- endpoints
@pytest.mark.parametrize("bad", [
    "http://example.cognitiveservices.azure.com",
    "HTTP://example.cognitiveservices.azure.com",
    "hTtP://example.cognitiveservices.azure.com",
    "   http://example.cognitiveservices.azure.com   ",
])
def test_http_endpoint_is_refused_any_case(bad, tmp_path, monkeypatch):
    """http:// must be refused however it is capitalised or padded.

    The key travels in a request header, so an unencrypted endpoint would put
    it on the wire in clear text.
    """
    monkeypatch.setenv("KBO_CONFIG", str(tmp_path / "config.json"))
    with pytest.raises(rpc.InvalidSettings):
        rpc._set_settings({"azureEndpoint": bad})


def test_refused_endpoint_is_not_persisted(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setenv("KBO_CONFIG", str(cfg))
    with pytest.raises(rpc.InvalidSettings):
        rpc._set_settings({"azureEndpoint": "http://evil.example.com"})
    assert not cfg.exists() or "evil.example.com" not in cfg.read_text()


@pytest.mark.parametrize("given,expected_scheme", [
    ("example.cognitiveservices.azure.com", "https"),
    ("https://example.cognitiveservices.azure.com", "https"),
    ("HTTPS://example.cognitiveservices.azure.com", "https"),
])
def test_bare_and_uppercase_endpoints_normalise_to_https(
        given, expected_scheme, tmp_path, monkeypatch):
    monkeypatch.setenv("KBO_CONFIG", str(tmp_path / "config.json"))
    out = rpc._set_settings({"azureEndpoint": given})
    assert out["azureEndpoint"].lower().startswith(expected_scheme + "://")
    # The host must survive normalisation intact.
    assert "example.cognitiveservices.azure.com" in out["azureEndpoint"].lower()


def test_endpoint_without_a_host_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("KBO_CONFIG", str(tmp_path / "config.json"))
    with pytest.raises(rpc.InvalidSettings):
        rpc._set_settings({"azureEndpoint": "https://"})


# --------------------------------------------------------------- same-host
def test_same_host_ignores_explicit_default_port():
    a = "https://x.cognitiveservices.azure.com/documentintelligence"
    b = "https://x.cognitiveservices.azure.com:443/operations/1"
    assert azure_ocr._same_host(a, b)


@pytest.mark.parametrize("other", [
    "https://evil.example.com/operations/1",
    "http://x.cognitiveservices.azure.com/operations/1",
    "https://x.cognitiveservices.azure.com:8443/operations/1",
    "https://x.cognitiveservices.azure.com.evil.com/operations/1",
])
def test_same_host_rejects_different_origins(other):
    base = "https://x.cognitiveservices.azure.com/documentintelligence"
    assert not azure_ocr._same_host(base, other)


def test_same_host_is_case_insensitive_on_host():
    a = "https://X.CognitiveServices.Azure.COM/a"
    b = "https://x.cognitiveservices.azure.com/b"
    assert azure_ocr._same_host(a, b)


# ------------------------------------------------------------------- az CLI
def test_az_refuses_a_relative_resolution(monkeypatch):
    """shutil.which on Windows inserts the CWD at the front of the search
    path, returning '.\\az.CMD' for a planted file. Anything not absolute
    must be rejected, or a file dropped in the working directory runs with
    the user's credentials."""
    monkeypatch.setattr(shutil, "which", lambda _n: os.path.join(".", "az.CMD"))

    def explode(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("a relative executable was executed")

    monkeypatch.setattr(azure_ocr.proc, "run", explode)
    assert azure_ocr._az(["account", "get-access-token"]) == ""


def test_az_returns_empty_when_cli_is_absent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    assert azure_ocr._az(["account"]) == ""


def test_get_token_absent_cli_does_not_forge_a_bearer_header(monkeypatch):
    """_headers builds 'Bearer ' + (get_token() or ''). An empty token must
    not silently produce a header that looks valid."""
    monkeypatch.setattr(azure_ocr, "_az", lambda _a: "")
    assert azure_ocr.get_token() in (None, "")


# ------------------------------------------------------------- redirects
def test_cross_host_redirect_is_refused():
    import urllib.error
    import urllib.request

    h = azure_ocr._NoCrossHostRedirect()
    req = urllib.request.Request("https://x.cognitiveservices.azure.com/a")
    with pytest.raises(urllib.error.HTTPError):
        h.redirect_request(req, None, 302, "Found", {},
                           "https://evil.example.com/a")


def test_same_host_redirect_is_allowed():
    import urllib.request

    h = azure_ocr._NoCrossHostRedirect()
    req = urllib.request.Request("https://x.cognitiveservices.azure.com/a")
    out = h.redirect_request(req, None, 302, "Found", {},
                             "https://x.cognitiveservices.azure.com/b")
    assert out is None or isinstance(out, urllib.request.Request)
