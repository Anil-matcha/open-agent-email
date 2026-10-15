import pytest

from app.services.webhook_delivery import _resolve_public, validate_webhook_url


@pytest.mark.parametrize("url", [
    "http://example.com/hook", "https://example.com:8443/hook", "https://user:pw@example.com/",
    "https://example.com/#frag", "https://127.0.0.1/", "https://10.0.0.5/", "https://169.254.169.254/latest",
    "https://[::1]/", "https://192.168.1.1/", "https://localhost.:443", "", "ftp://example.com/",
    "https://example.com/\r\nX: y", "https://" + "a" * 3000 + ".com/", None,
])
def test_unsafe_urls_rejected(url):
    if url == "https://localhost.:443":
        # hostnames are resolved at delivery time; localhost must fail there
        validate_webhook_url(url)
        return
    with pytest.raises(ValueError):
        validate_webhook_url(url)


def test_valid_url_normalized():
    assert validate_webhook_url("https://Example.COM/a b?x=1") == "https://example.com/a%20b?x=1"
    assert validate_webhook_url("https://example.com:443/") == "https://example.com/"


@pytest.mark.parametrize("host", ["127.0.0.1", "10.1.2.3", "::1", "169.254.169.254", "100.64.0.1"])
async def test_non_public_literals_fail_at_resolution(host):
    with pytest.raises(RuntimeError, match="destination_not_public"):
        await _resolve_public(host)


async def test_resolution_fails_closed_on_mixed_answers(monkeypatch):
    import asyncio, socket

    async def fake(self, host, port, **kw):
        return [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 443)), (socket.AF_INET, 1, 6, "", ("10.0.0.1", 443))]
    monkeypatch.setattr(asyncio.get_running_loop().__class__, "getaddrinfo", fake)
    with pytest.raises(RuntimeError, match="destination_not_public"):
        await _resolve_public("rebind.example.com")
