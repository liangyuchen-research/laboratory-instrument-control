"""Local configuration for the legacy desktop instrument controller."""

from pathlib import Path
import configparser
import json
import os

CONFIG_PATH = Path(os.environ.get("LAB_CONFIG_FILE", Path(__file__).with_name("config.local.ini")))


def load_settings() -> dict:
    """Load nonsecret configuration and optional environment-based credentials."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(CONFIG_PATH, encoding="utf-8")
    section = parser["instrument"] if parser.has_section("instrument") else {}

    def value(key: str, environment: str, default: str = "") -> str:
        return os.environ.get(environment, section.get(key, default))

    def wavelength_list(key: str, environment: str, default: str) -> list[float]:
        parsed = json.loads(value(key, environment, default))
        if not isinstance(parsed, list) or not all(isinstance(x, (int, float)) for x in parsed):
            raise ValueError(f"{key} must be a JSON list of numeric wavelengths.")
        return parsed

    return {
        "pi_user": value("pi_user", "LAB_PI_USER"),
        "pi_address": value("pi_address", "LAB_PI_HOST"),
        "pi_python_file": value("pi_python_file", "LAB_PI_CONTROLLER"),
        "pi_data_dir": value("pi_data_dir", "LAB_PI_DATA_DIR"),
        "pi_password": os.environ.get("LAB_PI_PASSWORD", ""),
        "time_series_wavelength": wavelength_list(
            "time_series_wavelength", "LAB_TIME_SERIES_WAVELENGTHS", "[243.389, 363.984]"
        ),
        "normalized_wavelength": wavelength_list(
            "normalized_wavelength", "LAB_NORMALIZATION_WAVELENGTHS", "[237.868, 362.122]"
        ),
    }
