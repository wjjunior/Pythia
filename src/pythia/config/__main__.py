"""Boot check: load settings, confirm paper mode, print a secret-free summary."""

from pythia.config.settings import ensure_paper_mode, get_settings


def main() -> None:
    settings = get_settings()
    ensure_paper_mode(settings)
    db_target = settings.database_url.rsplit("@", 1)[-1] if "@" in settings.database_url else "set"
    print(
        "pythia.config: boot ok — "
        f"mode={settings.mode} "
        f"live_venues={settings.use_live_venues} "
        f"llm={'configured' if settings.llm_enabled else 'stub'} "
        f"db={db_target}"
    )


if __name__ == "__main__":
    main()
