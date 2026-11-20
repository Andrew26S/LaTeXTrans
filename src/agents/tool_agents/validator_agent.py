from pathlib import Path
from typing import Any

from src.agents.tool_agents.base_tool_agent import BaseToolAgent
from src.formats.latex.validation import validate_fragment


class ValidatorAgent(BaseToolAgent):
    def __init__(self, config: dict[str, Any], project_dir: str = None, output_dir: str = None):
        super().__init__(agent_name="ValidatorAgent", config=config)
        self.project_dir = project_dir
        self.output_dir = output_dir

    def execute(self, errors_report: list[dict] | None = None) -> list[dict]:
        project = Path(self.project_dir).name
        self.log(f"Starting validation for {project}.")
        output = Path(self.output_dir)
        sections = self.read_file(output / "sections_map.json", "json")
        captions = self.read_file(output / "captions_map.json", "json")
        envs = self.read_file(output / "envs_map.json", "json")

        parts = [s for s in sections if str(s["section"]) not in {"0", "-1"}]
        parts.extend(captions)
        parts.extend(e for e in envs if e.get("need_trans", e["content"] != e.get("trans_content")))
        requested = None
        if errors_report is not None:
            requested = {(entry["part"], entry["num_or_ph"]) for entry in errors_report}

        remaining = []
        for part in parts:
            kind, identifier = self._part_id(part)
            if requested is not None and (kind, identifier) not in requested:
                continue
            report = self._validate(part)
            if report:
                remaining.append(report)
        self.save_file(output / "errors_report.json", "json", remaining)
        self.log(f"Validation complete for {project}. Fragments requiring repair: {len(remaining)}.")
        return remaining

    @staticmethod
    def _part_id(part: dict) -> tuple[str, str]:
        if "section" in part:
            return "sec", part["section"]
        return ("env" if "env_name" in part else "cap"), part["placeholder"]

    def _validate(self, part: dict) -> dict | None:
        kind, identifier = self._part_id(part)
        errors, warnings = validate_fragment(part["content"], part["trans_content"])
        if warnings:
            self.log(f"{kind} {identifier}: macro usage changed (informational): {', '.join(warnings)}.")
        if errors:
            return {"part": kind, "num_or_ph": identifier, **errors}
        return None
