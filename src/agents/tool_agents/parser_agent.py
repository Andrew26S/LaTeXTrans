from typing import Dict, Any
from src.agents.tool_agents.base_tool_agent import BaseToolAgent
from pathlib import Path
import sys
import os

base_dir = os.getcwd()
sys.path.append(base_dir)

class ParserAgent(BaseToolAgent):
    def __init__(self, 
                 config: Dict[str, Any], 
                 project_dir: str = None,
                 output_dir: str = None
                 ):
        super().__init__(agent_name="ParserAgent", config=config)
        self.config = config
        self.project_dir = project_dir  # Project path for parsing
        self.output_dir = output_dir  # Output directory for parsed files

    def execute(self) -> Any:
        self.log(f"🤖💬 Starting parsing for project...⏳: {os.path.basename(self.project_dir)}.")

        from src.formats.latex.parser import LatexParser
        latex_parser = LatexParser(self.project_dir, self.output_dir)
        latex_parser.parse()
        # need_trans for each environment is decided locally by the parser (see env_need_trans).

        self.save_file(Path(self.output_dir, "inputs_map.json"), "json", latex_parser.inputs_json)
        self.save_file(Path(self.output_dir, "envs_map.json"), "json", latex_parser.envs_json)
        self.save_file(Path(self.output_dir, "captions_map.json"), "json", latex_parser.captions_json)
        self.save_file(Path(self.output_dir, "newcommands_map.json"), "json", latex_parser.newcommands_json)
        self.save_file(Path(self.output_dir, "sections_map.json"), "json", latex_parser.sections_json)

        self.log(f"✅ Successfully parsed {os.path.basename(self.project_dir)}.")
        self.log(f"🤖💬 Parsed files are saved in {self.output_dir}.")
