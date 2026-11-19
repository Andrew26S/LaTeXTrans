from typing import Dict, Any, List
from src.agents.tool_agents.base_tool_agent import BaseToolAgent
from pathlib import Path
import sys
import os
import shutil

from src.utils.progress import report_progress

base_dir = os.getcwd()
sys.path.append(base_dir)

 
class GeneratorAgent(BaseToolAgent):
    def __init__(self, 
                 config: Dict[str, Any],
                 project_dir: str = None,
                 output_dir: str = None  # Output directory for parsed files
                 ):
        super().__init__(agent_name="GeneratorAgent", config=config)
        self.config = config
        self.project_dir = project_dir
        self.output_dir = output_dir  # Output directory for parsed files
        self.compile_attempts = []

    def execute(self) -> Any:

        from src.formats.latex.compile import LaTexCompiler
        from src.formats.latex.reconstruct import LatexConstructor

        name = os.path.basename(self.project_dir)
        self.log(f"Starting generation for {name}.")
        report_progress(10, "Reading translation data...")
        sections = self.read_file(Path(self.output_dir, "sections_map.json"), "json")
        captions = self.read_file(Path(self.output_dir, "captions_map.json"), "json")
        envs = self.read_file(Path(self.output_dir, "envs_map.json"), "json")
        newcommands = self.read_file(Path(self.output_dir, "newcommands_map.json"), "json")
        inputs = self.read_file(Path(self.output_dir, "inputs_map.json"), "json")

        report_progress(60, "Creating translation project directory...")
        transed_latex_dir = self._creat_transed_latex_folder(self.project_dir)
        report_progress(70, "Reconstructing LaTeX document...")
        latex_constructor = LatexConstructor(
            sections=sections, captions=captions, envs=envs, inputs=inputs,
            newcommands=newcommands, output_latex_dir=transed_latex_dir,
        )
        latex_constructor.construct()

        report_progress(80, "Compiling PDF document...")
        latex_compiler = LaTexCompiler(output_latex_dir=transed_latex_dir)
        self.compile_attempts = latex_compiler.attempts
        pdf_file = latex_compiler.compile()
        if pdf_file:
            report_progress(100, "PDF compiled.")
            self.log(f"Successfully generated PDF for {name}.")
        else:
            self.log(f"Failed to compile PDF for {name}.", level="error")
        return pdf_file

    def _creat_transed_latex_folder(self, src_dir: str) -> str:
        """
        Create a translated folder by copying the source directory and renaming it.
        """
        if not os.path.isdir(src_dir):
            raise NotADirectoryError(f"The path {src_dir} is not a valid directory.")

        base_name = os.path.basename(src_dir)
        dest_dir = os.path.join(self.output_dir, base_name)

        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir)
        shutil.copytree(src_dir, dest_dir)

        return dest_dir
        
    


# import toml
# import argparse

# parser = argparse.ArgumentParser()
# parser.add_argument("--config", type=str, default="config/default.toml")
# args = parser.parse_args()

# config = toml.load(args.config)
# dir = "D:\code\AutoLaTexTrans\output\ch_arXiv-2504.06261v2/arXiv-2504.06261v2"
# Validator = ValidatorAgent(config=config,
#                           project_dir=config["paths"].get("project_dir", None),
#                           validator_dir=dir
#                           )
# Validator.execute()
