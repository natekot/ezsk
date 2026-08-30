from __future__ import annotations

from pathlib import Path


def test_post_init_sets_defaults():
    """Config sets state_file and log_file defaults from paths module."""
    from ezsk.config import Config
    c = Config()
    assert c.state_file  # non-empty
    assert c.log_file
    assert "state.json" in c.state_file
    assert "ezsk.log" in c.log_file


def test_from_file_loads_toml(tmp_path, monkeypatch):
    """Config.from_file() reads TOML correctly."""
    from ezsk.config import Config

    config_file = tmp_path / "config.toml"
    config_file.write_text("""\
[slskd]
url = "http://test:9999"
api_key = "test-key-long-enough-for-validation"

[output]
dir = "/tmp/music"
format = "mp3"
min_bitrate = 256
verify = true
title_only_search = false
""")

    c = Config.from_file(config_file)
    assert c.url == "http://test:9999"
    assert c.api_key == "test-key-long-enough-for-validation"
    assert c.output_dir == "/tmp/music"
    assert c.preferred_format == "mp3"
    assert c.min_bitrate == 256
    assert c.verify is True
    assert c.title_only_search is False


def test_from_file_reads_soulseek_username_from_slskd(tmp_path):
    """soulseek_username lives under [slskd]."""
    from ezsk.config import Config

    config_file = tmp_path / "config.toml"
    config_file.write_text("""\
[slskd]
api_key = "test-key-long-enough-for-validation"
soulseek_username = "newname"
""")

    assert Config.from_file(config_file).soulseek_username == "newname"


def test_from_file_falls_back_to_legacy_docker_username(tmp_path):
    """Pre-0.2 configs stored soulseek_username under [docker]."""
    from ezsk.config import Config

    config_file = tmp_path / "config.toml"
    config_file.write_text("""\
[slskd]
api_key = "test-key-long-enough-for-validation"

[docker]
soulseek_username = "legacyname"
""")

    assert Config.from_file(config_file).soulseek_username == "legacyname"


def test_update_from_dict_falls_back_to_legacy_docker_username():
    """The web settings PUT path honours the legacy [docker] section too."""
    from ezsk.config import Config

    c = Config()
    c.update_from_dict({"docker": {"soulseek_username": "legacyname"}})
    assert c.soulseek_username == "legacyname"

    c.update_from_dict({"slskd": {"soulseek_username": "newname"}})
    assert c.soulseek_username == "newname"


def test_save_and_reload(tmp_path):
    """Config.save() produces a file that Config.from_file() can read back."""
    from ezsk.config import Config

    c = Config(
        url="http://localhost:5030",
        api_key="test-api-key-long-enough-xx",
        output_dir="/tmp/out",
        preferred_format="flac",
        verify=True,
        title_only_search=True,
    )
    path = tmp_path / "config.toml"
    c.save(path)

    c2 = Config.from_file(path)
    assert c2.url == c.url
    assert c2.api_key == c.api_key
    assert c2.output_dir == c.output_dir
    assert c2.preferred_format == c.preferred_format
    assert c2.verify is True
    assert c2.title_only_search is True


def test_validate_missing_api_key():
    """Validate catches missing API key."""
    from ezsk.config import Config
    c = Config(downloads_dir="/tmp/dl")
    errors = c.validate()
    assert any("API key" in e for e in errors)


def test_validate_missing_downloads_dir():
    """Validate catches missing downloads dir."""
    from ezsk.config import Config
    c = Config(api_key="test-api-key-long-enough-xx")
    errors = c.validate()
    assert any("downloads directory" in e for e in errors)


def test_to_dict_update_from_dict_roundtrip():
    """to_dict and update_from_dict preserve all fields."""
    from ezsk.config import Config
    c = Config(
        url="http://x:1234",
        api_key="key-that-is-long-enough-xxx",
        output_dir="/out",
        preferred_format="mp3",
        min_bitrate=256,
        verify=True,
        title_only_search=False,
    )
    d = c.to_dict()
    c2 = Config()
    c2.update_from_dict(d)
    assert c2.url == c.url
    assert c2.api_key == c.api_key
    assert c2.output_dir == c.output_dir
    assert c2.preferred_format == c.preferred_format
    assert c2.min_bitrate == c.min_bitrate
    assert c2.verify == c.verify
    assert c2.title_only_search == c.title_only_search
