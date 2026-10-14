"""Config loading / saving tests."""

from __future__ import annotations

from pathlib import Path

from velox.config import VeloxConfig, load_config, save_config


def test_defaults_match_documented_values():
    config = VeloxConfig()
    assert config.general.keep_original is False
    assert config.general.temp_dir == "source"
    assert config.clean_audio.language == "eng,und"
    assert config.clean_subs.language == "eng"
    assert config.standardise_audio.audio_codec == "aac"
    assert config.hevc_gpu.container == "mkv"
    assert config.hevc_gpu.encoder == "hevc_videotoolbox"
    assert config.hevc_gpu.bitrate_mode == "tier"
    assert config.hevc_gpu.bits_per_pixel == 0.033
    assert config.hevc_gpu.target_size_mb == 1500.0
    assert config.hevc_gpu.enable_10bit is True
    assert config.hevc_gpu.hardware_decode is True
    assert config.file_size_check.upper_bound == 110
    assert config.file_size_check.lower_bound == 0
    assert config.standardise_audio.audio_bitrate_kbps == 0
    assert config.order == [
        "reorder_streams",
        "clean_audio",
        "clean_subs",
        "standardise_audio",
        "hevc_gpu",
        "reorder_all_streams",
        "file_size_check",
    ]


def test_save_and_load_roundtrip(tmp_path: Path):
    config = VeloxConfig()
    config.general.keep_original = False
    config.clean_audio.language = "eng,jpn"
    config.hevc_gpu.bits_per_pixel = 0.045
    config.file_size_check.lower_bound = 25

    path = tmp_path / "config.toml"
    save_config(config, path)
    loaded = load_config(path)

    assert loaded.general.keep_original is False
    assert loaded.clean_audio.language == "eng,jpn"
    assert loaded.hevc_gpu.bits_per_pixel == 0.045
    assert loaded.file_size_check.lower_bound == 25


def test_partial_config_only_overrides_given_values(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[plugins.clean_audio]\nlanguage = \"deu\"\n",
        encoding="utf-8",
    )
    loaded = load_config(path)
    assert loaded.clean_audio.language == "deu"
    # untouched default
    assert loaded.clean_audio.commentary is False
    assert loaded.standardise_audio.audio_codec == "aac"


def test_type_coercion(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[plugins.hevc_gpu]\n"
        "bits_per_pixel = 0.045\n"
        "enable_10bit = true\n"
        "bframes_value = 7\n",
        encoding="utf-8",
    )
    loaded = load_config(path)
    assert loaded.hevc_gpu.bits_per_pixel == 0.045
    assert loaded.hevc_gpu.enable_10bit is True
    assert loaded.hevc_gpu.bframes_value == 7


def test_unknown_key_is_ignored(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[general]\nthis_key_does_not_exist = 1\nkeep_original = false\n",
        encoding="utf-8",
    )
    loaded = load_config(path)
    assert loaded.general.keep_original is False
