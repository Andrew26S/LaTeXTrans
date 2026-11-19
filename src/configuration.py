from typing import Any, Dict, Optional

import toml


DEFAULT_CONFIG_PATH = "config/default.toml"
DEFAULT_LOCAL_CONFIG_PATH = "config/local.toml"


def term_updates_enabled(value: object) -> bool:
    """Accept TOML booleans and the strings used by older configuration files."""
    return str(value).strip().lower() == "true"


def load_runtime_config(
    config_path: str = DEFAULT_CONFIG_PATH,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = toml.load(config_path)
    overrides = overrides or {}

    if "llm_config" in overrides:
        config["llm_config"] = dict(overrides["llm_config"])
    llm_config = config.setdefault("llm_config", {})
    if overrides.get("url"):
        llm_config["base_url"] = overrides["url"]
    if overrides.get("model"):
        llm_config["model"] = overrides["model"]
    if overrides.get("key") is not None:
        llm_config["api_key"] = overrides["key"]

    for option, field in (("source", "tex_sources_dir"), ("output", "output_dir"),
                          ("source_language", "source_language"), ("target_language", "target_language")):
        if overrides.get(option):
            config[field] = overrides[option]

    if overrides.get("user_term") is not None:
        config["user_term"] = overrides["user_term"]
    if overrides.get("mode") is not None:
        config["mode"] = overrides["mode"]
    if overrides.get("update_term") is not None:
        config["update_term"] = overrides["update_term"]
    config["update_term"] = term_updates_enabled(config.get("update_term", False))
    if config.get("mode", 0) not in (0, 1, 2):
        raise ValueError("mode must be 0, 1 or 2.")

    if overrides.get("category"):
        config.setdefault("category", {}).update(overrides["category"])

    extra_papers = overrides.get("paper_list") or []
    if overrides.get("replace_paper_list"):
        config["paper_list"] = list(extra_papers)
    elif extra_papers:
        config.setdefault("paper_list", [])
        config["paper_list"].extend(extra_papers)

    config.setdefault("source_language", "en")
    config.setdefault("target_language", "ch")
    return config
