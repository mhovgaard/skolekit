"""The one-shot device client finds its gateway origin and key without printing them."""
import pytest

from skoleklar import device_client


def test_config_values_are_used_as_given():
    base, headers = device_client.connection(None, {
        "base_url": "https://example.ew.gateway.dev/", "gateway_key": "key-from-config", "token": "tok"})
    assert base == "https://example.ew.gateway.dev"
    assert headers == {"Authorization": "Bearer tok", "x-api-key": "key-from-config"}


def test_a_controller_file_with_only_a_token_uses_the_flag_and_the_keychain(monkeypatch):
    monkeypatch.setattr("skoleklar.device_token.stored_gateway_api_key", lambda: "key-from-keychain")
    base, headers = device_client.connection("https://example.ew.gateway.dev", {"device_id": "local-controller",
                                                                                "token": "tok"})
    assert base == "https://example.ew.gateway.dev"
    assert headers["x-api-key"] == "key-from-keychain"


@pytest.mark.parametrize("base_url, config, message", [
    (None, {"token": "tok", "gateway_key": "k"}, "--base-url"),
    ("http://example.ew.gateway.dev", {"token": "tok", "gateway_key": "k"}, "HTTPS"),
    ("https://user:pass@example.ew.gateway.dev", {"token": "tok", "gateway_key": "k"}, "HTTPS"),
    ("https://example.ew.gateway.dev", {"gateway_key": "k"}, "device token"),
])
def test_missing_or_unsafe_values_are_refused(base_url, config, message):
    with pytest.raises(ValueError, match=message):
        device_client.connection(base_url, config)


def test_no_gateway_key_anywhere_is_refused(monkeypatch):
    monkeypatch.setattr("skoleklar.device_token.stored_gateway_api_key", lambda: None)
    with pytest.raises(ValueError, match="deploy the gateway first"):
        device_client.connection("https://example.ew.gateway.dev", {"token": "tok"})
