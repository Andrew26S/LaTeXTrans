import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import toml

from src.utils.progress import log_message
from src.agents.coordinator_agent import CoordinatorAgent
from src.formats.latex.utils import (
    extract_archive,
    prepare_arxiv_source,
    extract_arxiv_ids,
    extract_compressed_files,
    get_arxiv_category,
    get_profect_dirs,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_CONFIG_PATH = "config/local.toml"
ProjectEventCallback = Callable[[Dict[str, Any]], None]


def resolve_path(path_value: str) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


def is_local_archive(path: str) -> bool:
    p = Path(path)
    if not path or not p.is_file():
        return False
    lower = p.name.lower()
    return lower.endswith((".zip", ".tar", ".tar.gz", ".tgz"))


def archive_project_dir(archive_path: str, projects_dir: str) -> str:
    name = os.path.basename(archive_path)
    lower = name.lower()
    if lower.endswith(".tar.gz"):
        stem = name[:-7]
    elif lower.endswith(".tgz"):
        stem = name[:-4]
    elif lower.endswith(".tar"):
        stem = name[:-4]
    elif lower.endswith(".zip"):
        stem = name[:-4]
    else:
        stem = os.path.splitext(name)[0]
    return os.path.join(projects_dir, stem)


def create_unique_dir(base_dir: Path) -> Path:
    index = 0
    candidate = base_dir
    while True:
        try:
            candidate.mkdir(parents=True)
            return candidate
        except FileExistsError:
            index += 1
            candidate = base_dir.with_name(f"{base_dir.name}_{index}")


def extract_local_archive(archive_path: str, projects_dir: str) -> str:
    target_dir = create_unique_dir(Path(archive_project_dir(archive_path, projects_dir)))
    try:
        extract_archive(Path(archive_path), target_dir)
    except Exception:
        shutil.rmtree(target_dir)
        raise
    return str(target_dir)


def split_cli_items(values: Sequence[str]) -> List[str]:
    raw = " ".join(values)
    return [item.strip() for item in raw.split(",") if item.strip()]


def split_multivalue_text(value: str) -> List[str]:
    if not value:
        return []
    normalized = value.replace("\n", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def load_runtime_config(
    config_path: str = "config/default.toml",
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = toml.load(config_path)
    overrides = overrides or {}

    llm_config = config.setdefault("llm_config", {})
    if overrides.get("url"):
        llm_config["base_url"] = overrides["url"]
    if overrides.get("model"):
        llm_config["model"] = overrides["model"]
    if overrides.get("key"):
        llm_config["api_key"] = overrides["key"]

    for key in ("source", "output", "source_language", "target_language", "user_term"):
        if overrides.get(key):
            mapped_key = {
                "source": "tex_sources_dir",
                "output": "output_dir",
                "source_language": "source_language",
                "target_language": "target_language",
                "user_term": "user_term",
            }[key]
            config[mapped_key] = overrides[key]

    if overrides.get("mode") is not None:
        config["mode"] = overrides["mode"]
    if overrides.get("update_term") is not None:
        config["update_term"] = overrides["update_term"]

    if overrides.get("category"):
        config.setdefault("category", {}).update(overrides["category"])

    extra_papers = overrides.get("paper_list") or []
    if overrides.get("replace_paper_list"):
        config["paper_list"] = list(extra_papers)
    elif extra_papers:
        config.setdefault("paper_list", [])
        config["paper_list"].extend(extra_papers)

    return config


def prepare_projects(
    config: Dict[str, Any],
    project_items: Optional[Iterable[str]] = None,
    all_existing: bool = False,
) -> tuple[List[str], List[Dict[str, Any]], str, str]:
    input_items = config.get("paper_list", [])
    projects_dir = str(resolve_path(config.get("tex_sources_dir", "tex source")))
    output_dir = str(resolve_path(config.get("output_dir", "outputs")))

    os.makedirs(projects_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    projects = []
    failures = []

    def record_failure(source, error, *, arxiv=False):
        failure = {"project_name": source if arxiv else Path(source).name, "error": str(error)}
        failure["arxiv_id" if arxiv else "project_dir"] = source
        failures.append(failure)
        log_message(f"Error preparing {source}: {error}")

    paper_list = []
    for item in input_items:
        ids = extract_arxiv_ids([item])
        if ids:
            paper_list.extend(ids)
        else:
            record_failure(item, "Invalid arXiv ID or URL.", arxiv=True)

    project_items = [item for item in (project_items or []) if item]
    categories = config.setdefault("category", {})
    for paper in dict.fromkeys(paper_list):
        try:
            path = prepare_arxiv_source(paper, projects_dir)
            if not config.get("user_term") and paper not in categories:
                categories.update(get_arxiv_category([paper]))
            projects.append(path)
        except Exception as exc:
            record_failure(paper, exc, arxiv=True)

    for item in project_items:
        path = item
        try:
            path = str(resolve_path(item))
            if os.path.isdir(path):
                projects.append(path)
            elif is_local_archive(path):
                projects.append(extract_local_archive(path, projects_dir))
            else:
                record_failure(path, "Invalid local project path.")
        except Exception as e:
            record_failure(path, e)

    if not input_items and not project_items:
        if not all_existing:
            raise ValueError("No input provided. Use --arxiv or --project. To process existing projects, pass --all-existing.")
        log_message("No explicit inputs. Processing all existing projects in the specified directory.")
        extraction_errors = extract_compressed_files(projects_dir)
        for archive, reason in extraction_errors.items():
            # Nested archives belong to the containing paper. Its workflow
            # determines whether the missing asset actually prevents translation.
            if Path(archive).parent == Path(projects_dir):
                record_failure(archive, reason)
        projects = get_profect_dirs(projects_dir)
        if not projects and not failures:
            raise ValueError("No projects found. Check 'tex_sources_dir' and 'paper_list' in config.")

    projects = list(dict.fromkeys(os.path.abspath(p) for p in projects))
    return projects, failures, projects_dir, output_dir


def run_projects(
    config: Dict[str, Any],
    projects: Sequence[str],
    output_dir: str,
    event_callback: Optional[ProjectEventCallback] = None,
    preparation_failures: Sequence[Dict[str, Any]] = (),
) -> Dict[str, List[Dict[str, Any]]]:
    completed_projects: List[Dict[str, Any]] = []
    failed_projects: List[Dict[str, Any]] = []
    total_projects = len(projects) + len(preparation_failures)
    for idx, failure in enumerate(preparation_failures, start=1):
        entry = {**failure, "index": idx, "total": total_projects}
        failed_projects.append(entry)
        if event_callback:
            event_callback({"type": "project_error", **entry})

    sources = resolve_path(config.get("tex_sources_dir", "tex source"))
    paper_by_path = {str(sources / paper): paper for paper in extract_arxiv_ids(config.get("paper_list", []))}
    for idx, project_dir in enumerate(projects, start=len(preparation_failures) + 1):
        project_name = os.path.basename(project_dir)
        log_message(f"[{idx}/{total_projects}] Processing {project_name}")
        project = {
            "index": idx, "total": total_projects, "project_name": project_name, "project_dir": project_dir,
        }
        paper = paper_by_path.get(project_dir)
        if paper:
            project["arxiv_id"] = paper
            if paper in config.get("category", {}):
                project["categories"] = config["category"][paper]
        if event_callback:
            event_callback({"type": "project_start", **project})
        coordinator = None
        try:
            coordinator = CoordinatorAgent(config={**config, "arxiv_id": paper}, project_dir=project_dir, output_dir=output_dir)
            project["pdf_path"] = coordinator.workflow_latextrans()
        except Exception as e:
            log_message(f"Error processing project {project_name}: {e}")
            project["error"] = str(e)
            failed_projects.append(project)
            event_type = "project_error"
        else:
            completed_projects.append(project)
            event_type = "project_complete"

        attempts = list(coordinator.compile_attempts) if coordinator is not None else []
        if attempts:
            project["compile_attempts"] = attempts
            status = attempts[-1]["status"]
            if status == "failed" and any(a["status"] == "pdf_with_errors" for a in attempts):
                status = "pdf_with_errors"
            project["compilation_status"] = status
        if event_callback:
            event_callback({"type": event_type, **project})
    return {
        "completed_projects": completed_projects,
        "failed_projects": failed_projects,
    }


def run_translation(
    config_path: str = "config/default.toml",
    overrides: Optional[Dict[str, Any]] = None,
    project_items: Optional[Iterable[str]] = None,
    all_existing: bool = False,
    event_callback: Optional[ProjectEventCallback] = None,
) -> Dict[str, Any]:
    config = load_runtime_config(config_path=config_path, overrides=overrides)
    projects, preparation_failures, projects_dir, output_dir = prepare_projects(
        config=config,
        project_items=project_items,
        all_existing=all_existing,
    )
    project_status = run_projects(
        config=config,
        projects=projects,
        output_dir=output_dir,
        event_callback=event_callback,
        preparation_failures=preparation_failures,
    )
    return {
        "config": config,
        "projects": projects,
        "projects_dir": projects_dir,
        "output_dir": output_dir,
        "completed_projects": project_status["completed_projects"],
        "failed_projects": project_status["failed_projects"],
    }
