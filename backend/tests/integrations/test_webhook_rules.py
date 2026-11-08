"""The rules for webhook addresses and signatures, without a database or a network."""

from __future__ import annotations

import pytest

from docuflow.modules.integrations import webhooks


def resolves_to(*addresses: str):
    return lambda host, port: list(addresses)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/hooks",
        "https://hooks.example.org:8443/in?x=1",
    ],
)
def test_a_public_https_address_is_accepted(url: str) -> None:
    webhooks.check_url(url, resolver=resolves_to("93.184.216.34"))


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://example.com/hook", "https"),
        ("ftp://example.com/hook", "https"),
        ("https://user:pass@example.com/hook", "user name"),
        ("https:///no-host", "host"),
        ("https://", "host"),
        ("not a url", "https"),
        ("https://example.com:99999/hook", "valid"),
        ("https://example.com/" + "a" * 500, "characters"),
        ("", "characters"),
    ],
)
def test_an_unusable_address_is_refused_with_a_reason(url: str, why: str) -> None:
    with pytest.raises(webhooks.UnsafeUrl, match=why):
        webhooks.check_url(url, resolver=resolves_to("93.184.216.34"))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.1",
        "169.254.169.254",  # the cloud metadata service
        "0.0.0.0",  # noqa: S104
        "100.64.0.1",  # carrier-grade private space
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",  # a private address wearing an IPv6 coat
        "224.0.0.1",
        "not-an-ip",
    ],
)
def test_a_name_that_points_inside_the_network_is_refused(address: str) -> None:
    with pytest.raises(webhooks.UnsafeUrl, match="private or internal"):
        webhooks.check_url("https://hooks.example.com/in", resolver=resolves_to(address))


def test_one_private_address_among_public_ones_is_enough_to_refuse() -> None:
    with pytest.raises(webhooks.UnsafeUrl, match="private or internal"):
        webhooks.check_url(
            "https://hooks.example.com/in", resolver=resolves_to("93.184.216.34", "10.0.0.9")
        )


def test_a_name_that_cannot_be_found_is_refused() -> None:
    with pytest.raises(webhooks.UnsafeUrl, match="cannot be found"):
        webhooks.check_url("https://nowhere.example/in", resolver=resolves_to())


def test_an_address_that_is_an_ip_literal_is_checked_like_any_other() -> None:
    def literal(host: str, port: int) -> list[str]:
        return [host]

    with pytest.raises(webhooks.UnsafeUrl):
        webhooks.check_url("https://127.0.0.1/hook", resolver=literal)
    with pytest.raises(webhooks.UnsafeUrl):
        webhooks.check_url("https://[::1]/hook", resolver=literal)
    webhooks.check_url("https://93.184.216.34/hook", resolver=literal)


def test_plain_http_is_only_allowed_on_request_and_still_must_be_public() -> None:
    webhooks.check_url(
        "http://example.com/hook", allow_insecure=True, resolver=resolves_to("93.184.216.34")
    )
    with pytest.raises(webhooks.UnsafeUrl, match="private or internal"):
        webhooks.check_url(
            "http://example.com/hook", allow_insecure=True, resolver=resolves_to("127.0.0.1")
        )


# Signatures


def test_a_signature_checks_out_for_the_same_secret_time_and_body() -> None:
    body = webhooks.encode({"b": 2, "a": 1})
    assert body == b'{"a":1,"b":2}'  # compact, with keys in a fixed order
    header = webhooks.sign("whsec_x", 1_700_000_000, body)
    assert header.startswith("t=1700000000,v1=")
    assert webhooks.verify("whsec_x", header, body, now=1_700_000_100)


def test_a_changed_body_a_wrong_secret_or_a_different_time_does_not_check_out() -> None:
    body = webhooks.encode({"id": "1"})
    header = webhooks.sign("whsec_x", 1_700_000_000, body)
    assert not webhooks.verify("whsec_x", header, webhooks.encode({"id": "2"}), now=1_700_000_000)
    assert not webhooks.verify("whsec_other", header, body, now=1_700_000_000)
    forged = header.replace("t=1700000000", "t=1700000001")
    assert not webhooks.verify("whsec_x", forged, body, now=1_700_000_000)


def test_an_old_call_is_refused_even_with_a_good_signature() -> None:
    body = webhooks.encode({"id": "1"})
    header = webhooks.sign("whsec_x", 1_700_000_000, body)
    assert webhooks.verify("whsec_x", header, body, now=1_700_000_000 + 299)
    assert not webhooks.verify("whsec_x", header, body, now=1_700_000_000 + 301)
    assert not webhooks.verify("whsec_x", header, body, now=1_700_000_000 - 301)


@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v1=ff", "v1=ff", "t=1"])
def test_a_malformed_signature_header_is_refused_not_a_crash(header: str) -> None:
    assert not webhooks.verify("whsec_x", header, b"{}", now=1)


def test_the_subscribable_events_leave_out_reads_and_the_webhooks_own_events() -> None:
    names = set(webhooks.EVENTS)
    assert "document.previewed" not in names
    assert "document.downloaded" not in names
    assert not any(n.startswith(("webhook.", "audit.", "auth.")) for n in names)
    assert webhooks.PING not in names
