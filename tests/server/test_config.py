import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from supernote.server.config import ServerConfig, WebhookEndpointConfig


@pytest.fixture(autouse=True)
def patch_server_config() -> Generator[None]:
    """Override the autouse fixture from conftest.py to do nothing.

    This ensures that ServerConfig.load() runs the real logic instead of returning a mock.
    """
    yield


def test_server_config_defaults(tmp_path: Path) -> None:
    """Test loading configuration with defaults."""
    config_dir = tmp_path / "config"
    config = ServerConfig.load(config_dir)

    assert config.host == "0.0.0.0"
    assert config.port == 8080
    assert config.storage_dir == "storage"
    assert config.auth.secret_key != ""  # Should be generated in-memory


def test_server_config_load_from_file(tmp_path: Path) -> None:
    """Test loading configuration from a file including users."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"

    data = {
        "host": "127.0.0.1",
        "port": 9090,
        "auth": {
            "secret_key": "my-secret-key",
            "enable_registration": True,
        },
    }
    with open(config_file, "w") as f:
        yaml.safe_dump(data, f)

    config = ServerConfig.load(config_dir)

    assert config.host == "127.0.0.1"
    assert config.port == 9090
    assert config.auth.secret_key == "my-secret-key"
    assert config.auth.enable_registration is True


def test_server_config_env_var_override(tmp_path: Path) -> None:
    """Test that environment variables override config file."""
    config_dir = tmp_path / "config"
    with patch.dict(
        os.environ,
        {
            "SUPERNOTE_JWT_SECRET": "env-secret",
            "SUPERNOTE_HOST": "1.2.3.4",
            "SUPERNOTE_PORT": "5555",
        },
    ):
        config = ServerConfig.load(config_dir)
        assert config.auth.secret_key == "env-secret"
        assert config.host == "1.2.3.4"
        assert config.port == 5555


def test_example_config_is_valid() -> None:
    """Ensure config-example.yaml can be loaded by ServerConfig."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    config_path = os.path.join(base_dir, "config-example.yaml")

    config = ServerConfig.load(config_file=config_path)

    assert config.host == "0.0.0.0"
    assert config.port == 8080
    assert config.storage_dir == "storage"
    assert config.auth.secret_key == "CHANGE_ME_TO_A_SECURE_RANDOM_STRING"
    assert config.auth.enable_registration is False


def test_configured_base_url_none_when_unset() -> None:
    """configured_base_url returns None when SUPERNOTE_BASE_URL is not set."""
    config = ServerConfig()
    assert config.configured_base_url is None


def test_configured_base_url_strips_trailing_slash() -> None:
    """configured_base_url returns the configured URL without a trailing slash."""
    config = ServerConfig(_base_url="https://notes.example.com/")
    assert config.configured_base_url == "https://notes.example.com"


def test_base_url_uses_configured_value() -> None:
    """base_url uses the configured base URL when set."""
    config = ServerConfig(_base_url="https://notes.example.com/")
    assert config.base_url == "https://notes.example.com"


def test_base_url_falls_back_to_host_and_port() -> None:
    """base_url falls back to host:port when SUPERNOTE_BASE_URL is unset."""
    config = ServerConfig(host="127.0.0.1", port=9090)
    assert config.configured_base_url is None
    assert config.base_url == "http://127.0.0.1:9090"


def test_base_url_fallback_maps_wildcard_host_to_localhost() -> None:
    """base_url fallback maps the 0.0.0.0 wildcard host to localhost."""
    config = ServerConfig(host="0.0.0.0", port=8080)
    assert config.base_url == "http://localhost:8080"


def test_configured_base_url_from_env(tmp_path: Path) -> None:
    """configured_base_url reflects SUPERNOTE_BASE_URL from the environment."""
    config_dir = tmp_path / "config"
    with patch.dict(os.environ, {"SUPERNOTE_BASE_URL": "https://env.example.com/"}):
        config = ServerConfig.load(config_dir)
        assert config.configured_base_url == "https://env.example.com"
        assert config.base_url == "https://env.example.com"


def test_server_config_webhooks_default_disabled(tmp_path: Path) -> None:
    """Webhooks are disabled with no endpoints by default."""
    config_dir = tmp_path / "config"
    config = ServerConfig.load(config_dir)

    assert config.webhooks.enabled is False
    assert config.webhooks.endpoints == []


def test_server_config_load_webhooks_from_file(tmp_path: Path) -> None:
    """Webhook endpoints are parsed from the config file."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"

    data = {
        "webhooks": {
            "enabled": True,
            "endpoints": [
                {
                    "url": "https://n8n.homelab.local/webhook/supernote",
                    "secret": "hmac-secret-key",
                    "events": ["note.sync_completed"],
                }
            ],
        }
    }
    with open(config_file, "w") as f:
        yaml.safe_dump(data, f)

    config = ServerConfig.load(config_dir)

    assert config.webhooks.enabled is True
    assert len(config.webhooks.endpoints) == 1
    endpoint = config.webhooks.endpoints[0]
    assert endpoint.url == "https://n8n.homelab.local/webhook/supernote"
    assert endpoint.secret == "hmac-secret-key"
    assert endpoint.events == ["note.sync_completed"]


def test_server_config_webhooks_enabled_env_var_override(tmp_path: Path) -> None:
    """SUPERNOTE_WEBHOOKS_ENABLED overrides the config file value."""
    config_dir = tmp_path / "config"
    with patch.dict(os.environ, {"SUPERNOTE_WEBHOOKS_ENABLED": "true"}):
        config = ServerConfig.load(config_dir)
        assert config.webhooks.enabled is True


@pytest.mark.parametrize(
    "invalid_url",
    [
        "",
        "ftp://example.com/webhook",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http:///missing-host",
        "not-a-url",
    ],
)
def test_webhook_endpoint_config_rejects_invalid_urls(invalid_url: str) -> None:
    """WebhookEndpointConfig only accepts http and https URLs with a valid host."""
    with pytest.raises(ValueError, match="http or https scheme"):
        WebhookEndpointConfig(url=invalid_url)


def test_server_config_proxy_env_vars(tmp_path: Path) -> None:
    """Test that proxy configuration can be set via environment variables."""
    config_dir = tmp_path / "config"
    with patch.dict(
        os.environ,
        {
            "SUPERNOTE_PROXY_MODE": "strict",
            "SUPERNOTE_TRUSTED_PROXIES": "10.0.0.1,10.0.0.2",
        },
    ):
        config = ServerConfig.load(config_dir)
        assert config.proxy_mode == "strict"
        # The list should be parsed from the comma-separated string
        assert config.trusted_proxies == ["10.0.0.1", "10.0.0.2"]


def test_server_config_web_flags(tmp_path: Path) -> None:
    """Verify web_enabled and web_notebook_preview configuration loading."""
    config = ServerConfig()
    assert config.web_enabled is True
    assert config.web_notebook_preview is True

    config_dir = tmp_path / "config"
    with patch.dict(
        os.environ,
        {
            "SUPERNOTE_WEB_ENABLED": "false",
            "SUPERNOTE_WEB_NOTEBOOK_PREVIEW": "0",
        },
    ):
        loaded = ServerConfig.load(config_dir)
        assert loaded.web_enabled is False
        assert loaded.web_notebook_preview is False


def test_storage_cleanup_defaults() -> None:
    """Verify default values for storage cleanup settings in ServerConfig."""
    config = ServerConfig()
    assert config.storage_cleanup_enabled is True
    assert config.storage_cleanup_interval_seconds == 3600
    assert config.storage_temp_ttl_seconds == 86400


def test_storage_cleanup_config_load_from_file(tmp_path: Path) -> None:
    """Verify loading storage cleanup settings from yaml config file."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"

    data = {
        "storage_cleanup_enabled": False,
        "storage_cleanup_interval_seconds": 1800,
        "storage_temp_ttl_seconds": 7200,
    }
    with open(config_file, "w") as f:
        yaml.safe_dump(data, f)

    config = ServerConfig.load(config_dir)
    assert config.storage_cleanup_enabled is False
    assert config.storage_cleanup_interval_seconds == 1800
    assert config.storage_temp_ttl_seconds == 7200


def test_storage_cleanup_env_var_overrides(tmp_path: Path) -> None:
    """Verify environment variables override storage cleanup config file settings."""
    config_dir = tmp_path / "config"
    with patch.dict(
        os.environ,
        {
            "SUPERNOTE_STORAGE_CLEANUP_ENABLED": "false",
            "SUPERNOTE_STORAGE_CLEANUP_INTERVAL_SECONDS": "7200",
            "SUPERNOTE_STORAGE_TEMP_TTL_SECONDS": "14400",
        },
    ):
        config = ServerConfig.load(config_dir)
        assert config.storage_cleanup_enabled is False
        assert config.storage_cleanup_interval_seconds == 7200
        assert config.storage_temp_ttl_seconds == 14400


def test_storage_cleanup_invalid_env_vars_fallback(tmp_path: Path) -> None:
    """Verify non-integer environment variables fall back to default values gracefully."""
    config_dir = tmp_path / "config"
    with patch.dict(
        os.environ,
        {
            "SUPERNOTE_STORAGE_CLEANUP_INTERVAL_SECONDS": "not_an_int",
            "SUPERNOTE_STORAGE_TEMP_TTL_SECONDS": "also_invalid",
        },
    ):
        config = ServerConfig.load(config_dir)
        assert config.storage_cleanup_interval_seconds == 3600
        assert config.storage_temp_ttl_seconds == 86400
