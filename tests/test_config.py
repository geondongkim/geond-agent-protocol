from __future__ import annotations

from geond.config import Settings, database_url_env_names, normalize_database_profile


def test_settings_prefers_profile_database_url(monkeypatch) -> None:
    monkeypatch.setenv("GEOND_DATABASE_PROFILE", "azure")
    monkeypatch.setenv("GEOND_DATABASE_URL", "postgresql://local/geond")
    monkeypatch.setenv(
        "AZURE_GEOND_DATABASE_URL",
        "postgresql://geondadmin:secret@pg.example.postgres.database.azure.com:5432/geond?sslmode=require",
    )

    settings = Settings()

    assert settings.database_profile == "azure"
    assert settings.database_url.startswith("postgresql://geondadmin:secret@pg.example")


def test_settings_supports_dynamic_profile_url_suffix(monkeypatch) -> None:
    monkeypatch.setenv("GEOND_DATABASE_PROFILE", "team-blue")
    monkeypatch.setenv("GEOND_DATABASE_URL", "postgresql://local/geond")
    monkeypatch.setenv("GEOND_DATABASE_URL_TEAM_BLUE", "postgresql://team-blue/geond")

    settings = Settings()

    assert settings.database_profile == "team_blue"
    assert settings.database_url == "postgresql://team-blue/geond"


def test_settings_falls_back_to_primary_database_url(monkeypatch) -> None:
    monkeypatch.delenv("GEOND_DATABASE_PROFILE", raising=False)
    monkeypatch.setenv("GEOND_DATABASE_URL", "postgresql://local/geond")
    monkeypatch.setenv("AZURE_GEOND_DATABASE_URL", "postgresql://azure/geond")

    settings = Settings()

    assert settings.database_profile == ""
    assert settings.database_url == "postgresql://local/geond"


def test_database_profile_env_names_are_predictable() -> None:
    assert normalize_database_profile("Team Blue") == "team_blue"
    assert database_url_env_names("azure") == (
        "AZURE_GEOND_DATABASE_URL",
        "GEOND_DATABASE_URL_AZURE",
        "GEOND_AZURE_DATABASE_URL",
    )
