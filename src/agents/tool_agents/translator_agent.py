from typing import Dict, Any, List, Optional
from src.agents.tool_agents.base_tool_agent import BaseToolAgent
#from TransLatex.src.formats.latex.prompts import *
import src.formats.latex.prompts as pm
from src.formats.latex.utils import *
from src.formats.latex.source_context import build_source_contexts
from pathlib import Path
import sys
import os
import re
import regex
import asyncio
import aiohttp
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.utils.progress import log_message, report_progress

base_dir = os.getcwd()
sys.path.append(base_dir)

MAX_RETRY_AFTER_SECONDS = 60.0


class LLMRequestError(Exception):
    """Raised when an LLM request still fails after all retries."""


class TranslatorAgent(BaseToolAgent):
    def __init__(self, 
                 config: Dict[str, Any], 
                 trans_mode: int = 0,
                 project_dir: Optional[str] = None,
                 output_dir: Optional[str] = None,
                 errors_report: Optional[List[Dict]] = None,
                 ):
        super().__init__(agent_name="TranslatorAgent", config=config)
        self.config = config
        if(config.get("update_term") == "True"):
            self.update_term = True 
            self.update_term = False
        # self.update_term = config.get("update_term", False)
        self.model = config["llm_config"].get("model", "gpt-4o")
        self.base_url = config["llm_config"].get("base_url", None)
        self.API_KEY = config["llm_config"].get("api_key", None)
        self.timeout = float(config["llm_config"].get("timeout", 100))
        # Optional output-token cap; omitted from requests unless configured, since
        # limits and parameter names differ between OpenAI-compatible providers.
        self.max_tokens = config["llm_config"].get("max_tokens")
        self.max_attempts = 3
        self.retry_base_delay = 5.0
        self.user_term = config.get("user_term", None)
        self.target_language = config.get("target_language", "ch")
        self.category = config.get("category", {})
        self.project_dir = project_dir  # Project path for parsing
        self.output_dir = output_dir  # Output directory for parsed files
        self.fail_section_nums = []
        self.fail_caption_phs = []
        self.fail_env_phs = []
        self.have_fail_parts = False
        self.errors_report = errors_report if errors_report is not None else []
        self.trans_mode = trans_mode if trans_mode is not None else 0
        # self.term_dict = config.get("term_dict", {})  # Dictionary for terminology translation
        self.term_dict = {}
        self.source_contexts = {}

    async def execute(self, error_retry_count=0, Maxtry=3):

        pm.init_prompts(self.config["source_language"], self.config["target_language"])
        self.add_placeholder()
        self.build_term_dict()

        sections = self.read_file(Path(self.output_dir, "sections_map.json"), "json")
        captions = self.read_file(Path(self.output_dir, "captions_map.json"), "json")
        envs = self.read_file(Path(self.output_dir, "envs_map.json"), "json")

        self.source_contexts = build_source_contexts(sections, captions, envs)

        if self.trans_mode == 0 or self.trans_mode == 2:
            self.log(f"Starting translation for project: {os.path.basename(self.project_dir)}.")

            report_progress(5, "Translating sections...")

            async with aiohttp.ClientSession() as session:
                sem = asyncio.Semaphore(10)  # Considering the api response speed, processing one section approximately takes about 10 seconds, and initiating a call every half second, 
                                             # around 10 should not waste api tokens

                async def process_section(i, sec):
                    async with sem:
                        translated = await self.translate(sec, envs, captions, session)
                        return i, translated

                tasks = [process_section(i, sec) for i, sec in enumerate(sections)]

                completed = 0

                total_tasks = len(tasks)
                for future in asyncio.as_completed(tasks):
                    i, translated_section = await future
                    sections[i] = translated_section
                    
                    completed += 1

                    process = int(5 + 90 * completed / total_tasks)
                    report_progress(process, f"Translating sections: {completed}/{total_tasks}")

                    # It can be considered to save and modify to integrate memory once for hard memory read and write, 
                    # and save each section once for the convenience of observing the translation situation.
                    self.save_file(Path(self.output_dir, "sections_map.json"), "json", sections)
                    self.save_file(Path(self.output_dir, "captions_map.json"), "json", captions)
                    self.save_file(Path(self.output_dir, "envs_map.json"), "json", envs)

                report_progress(95, "Validating translation results...")

                await self._val_fail_parts(Maxtry=Maxtry,
                                     sections=sections,
                                     captions=captions,
                                     envs=envs,
                                     session=session)

                report_progress(100, "Translation finished.")

                self.log("Translation finished.")

        elif self.trans_mode == 1:

            async with aiohttp.ClientSession() as session:
                error_parts = [error_part["num_or_ph"] for error_part in self.errors_report]
                self.log(
                    f"Starting retranslation for error parts: {error_parts}, attempt {error_retry_count + 1}/{Maxtry}.")

                report_progress(0, "Retranslating error parts...")

                await self._retranslate_error_parts(secs=sections,
                                                    caps=captions,
                                                    envs=envs,
                                                    session=session)

                self.save_file(Path(self.output_dir, "sections_map.json"), "json", sections)
                self.save_file(Path(self.output_dir, "captions_map.json"), "json", captions)
                self.save_file(Path(self.output_dir, "envs_map.json"), "json", envs)

                self.fail_section_nums.clear()
                self.fail_caption_phs.clear()
                self.fail_env_phs.clear()
                self.have_fail_parts = False

                await self._val_fail_parts(Maxtry=Maxtry,
                                           sections=sections,
                                           captions=captions,
                                           envs=envs,
                                           session=session)

            self.log("Retranslation finished.")

    async def translate(self,
                        section: Dict[str, Any],
                        envs: List[Dict[str, Any]],
                        captions: List[Dict[str, Any]],
                        session: aiohttp.ClientSession) -> Dict[str, Any]:
        """
        Translates the input data
        """
        placeholder_pattern_cap = r"<PLACEHOLDER_CAP_\d+>"
        placeholder_pattern_env = r"<PLACEHOLDER_ENV_\d+>"
        placeholders_cap = re.findall(placeholder_pattern_cap, section["content"])
        placeholders_env = re.findall(placeholder_pattern_env, section["content"])

        if(section["section"] == "-1" or section["section"] == "0"):
            section = section
        else:
            section = await self._translate_section(section, session)  

        for placeholder in placeholders_env:
            for i, env in enumerate(envs):
                if placeholder == env["placeholder"]:
                    placeholders_cap_in_env = re.findall(placeholder_pattern_cap, env["content"])
                    placeholders_cap.extend(placeholders_cap_in_env)
                    envs[i] = await self._translate_env(env, session)  
                    break

        # remove duplicates
        placeholders_cap = list(dict.fromkeys(placeholders_cap))

        for placeholder in placeholders_cap:
            for i, caption in enumerate(captions):
                if placeholder == caption["placeholder"]:
                    captions[i] = await self._translate_caption(caption, session)  
                    break

        return section
    
    async def _val_fail_parts(self, sections, captions, envs, Maxtry, session: aiohttp.ClientSession, fail_retry_count=0) -> None:

        while fail_retry_count < Maxtry and self.have_fail_parts:
            fail_parts = self.fail_section_nums + self.fail_caption_phs + self.fail_env_phs
            self.log(f"Starting retranslation for failed parts: {fail_parts}, attempt {fail_retry_count+1}/{Maxtry}.")

            report_progress(0, f"Retrying failed parts (attempt {fail_retry_count+1}/{Maxtry})...")

            await self._retranslate_fail_parts(secs=sections, caps=captions, envs=envs, session=session)
            self.save_file(Path(self.output_dir, "sections_map.json"), "json", sections)
            self.save_file(Path(self.output_dir, "captions_map.json"), "json", captions)
            self.save_file(Path(self.output_dir, "envs_map.json"), "json", envs)

            fail_retry_count += 1
            if self.have_fail_parts and fail_retry_count < Maxtry:
                await asyncio.sleep(3)
        if self.have_fail_parts:
            remaining = self.fail_section_nums + self.fail_caption_phs + self.fail_env_phs
            self.log(f"Failed to translate: {remaining}", level="error")

    async def _retranslate_fail_parts(self,
                                secs: List[Dict[str, Any]], 
                                caps: List[Dict[str, Any]], 
                                envs: List[Dict[str, Any]],
                                session: aiohttp.ClientSession) -> Any:
        sec_nums = self.fail_section_nums[:]
        cap_phs = self.fail_caption_phs[:]
        env_phs = self.fail_env_phs[:]
        self.fail_section_nums.clear()
        self.fail_caption_phs.clear()
        self.fail_env_phs.clear()
        self.have_fail_parts = False

        sec_dict = {s["section"]: i for i, s in enumerate(secs)}
        cap_dict = {c["placeholder"]: i for i, c in enumerate(caps)}
        env_dict = {e["placeholder"]: i for i, e in enumerate(envs)}

        if sec_nums:
            self.log(f"Retranslating for {sec_nums}")
            for sec_num in sec_nums:
                if sec_num == "-1" or sec_num == "0":
                    continue
                if sec_num in sec_dict:
                    i = sec_dict[sec_num]
                    secs[i] = await self._translate_section(secs[i], session)
            # else:
            #     print(f"[Warning] Section {sec_num} not found.")
        if cap_phs:
            self.log(f"Retranslating for {cap_phs}")
            for cap_ph in cap_phs:
                if cap_ph in cap_dict:
                    i = cap_dict[cap_ph]
                    caps[i] = await self._translate_caption(caps[i], session) 
            # else:
            #     print(f"[Warning] Caption placeholder {cap_ph} not found.")
        if env_phs:
            self.log(f"Retranslating for {env_phs}")
            for env_ph in env_phs:
                if env_ph in env_dict:
                    i = env_dict[env_ph]
                    envs[i] = await self._translate_env(envs[i], session) 
            # else:
            #     print(f"[Warning] Environment placeholder {env_ph} not found.")

    async def _retranslate_error_parts(self, secs, caps, envs, session) -> Any:

        async with aiohttp.ClientSession() as session:
            sem = asyncio.Semaphore(20)  

            completed = 0
            async def process_ErrorPart(i, error_report):
                async with sem:
                    error_message = []
                    if "command_error" in error_report:
                        error_message.append(error_report["command_error"])
                    if "ph_error" in error_report:
                        error_message.append(error_report["ph_error"])
                    if "bracket_error" in error_report:
                        error_message.append(error_report["bracket_error"])
                    error_message = "\n".join(error_message)

                    if error_report["part"] == "sec":
                        async def process_section(i, sec):
                            async with sem:
                                if error_report["num_or_ph"] == sec["section"]:
                                    sec_async = await self._translate_section(section=sec, error_message=error_message,
                                                                              session=session)
                                    return {"index": i, "result": sec_async, "is_valid": True}
                                else:
                                    return {"index": None, "result": None, "is_valid": False}

                        tasks_sec = [process_section(i, sec) for i, sec in enumerate(secs)]
                        for future in asyncio.as_completed(tasks_sec):
                            result = await future
                            
                            if result["is_valid"]:  
                                i = result["index"]
                                _sec = result["result"]
                                secs[i] = _sec
                    elif error_report["part"] == "env":
                        async def process_env(i, env):
                            async with sem:
                                if error_report["num_or_ph"] == env["placeholder"]:
                                    env_async = await self._translate_env(env=env, error_message=error_message,
                                                                          session=session)
                                    return {"index": i, "result": env_async, "is_valid": True}
                                else:
                                    return {"index": None, "result": None, "is_valid": False}

                        tasks_env = [process_env(i, env) for i, env in enumerate(envs)]
                        for future in asyncio.as_completed(tasks_env):
                            result = await future
                            
                            if result["is_valid"]:  
                                i = result["index"]
                                _env = result["result"]
                                envs[i] = _env
                    elif error_report["part"] == "cap":
                        async def process_cap(i, cap):
                            async with sem:
                                if error_report["num_or_ph"] == cap["placeholder"]:
                                    cap_async = await self._translate_caption(caption=cap, error_message=error_message,
                                                                              session=session)
                                    return {"index": i, "result": cap_async, "is_valid": True}
                                else:
                                    return {"index": None, "result": None, "is_valid": False}

                        tasks_cap = [process_cap(i, cap) for i, cap in enumerate(caps)]
                        for future in asyncio.as_completed(tasks_cap):
                            result = await future
                            
                            if result["is_valid"]:  
                                i = result["index"]
                                _cap = result["result"]
                                caps[i] = _cap
                    return i

            tasks_ErrorPart = [process_ErrorPart(i, error_report) for i, error_report in enumerate(self.errors_report)]
            total_error_tasks = len(tasks_ErrorPart)
            for future in asyncio.as_completed(tasks_ErrorPart):
                result = await future
                completed += 1

                report_progress(int(100 * completed / total_error_tasks), f"Retranslating error parts: {completed}/{total_error_tasks}")

                if result is not None:  
                    i = result

            report_progress(100, "Retranslation finished.")

    async def _translate_section(self, section: Dict[str, Any], session: aiohttp.ClientSession, error_message=None) -> Dict[str, Any]:
        
        transed_section = section.copy()
        section_num = section["section"]
        if self.trans_mode == 0:
            
            transed_section["trans_content"] = await self._request_llm_for_trans(
                pm.section_system_prompt,
                section["content"],
                fail_part=section_num,
                type="sec",
                session=session
            )
        elif self.trans_mode == 1:
            transed_section["trans_content"] = await self._request_llm_for_retrans_error_parts(
            pm.retrans_error_parts_system_prompt,
            part=transed_section,
            error_message=error_message,
            fail_part=section_num,
            type="sec",
            session=session)

        elif self.trans_mode == 2:
            """
            Combined with terminology translation
            """
            if not self.term_dict:
                transed_section["trans_content"] = await self._request_llm_for_trans(
                    pm.section_system_prompt,
                    section["content"],
                    fail_part=section_num,
                    type="sec",
                    session=session
                )
            else:
                transed_section["trans_content"] = await self._request_llm_for_trans_with_terms(
                                                            pm.section_system_prompt_with_dict,
                                                            section["content"], 
                                                            fail_part=section_num,
                                                            type="sec",
                                                            session=session
                                                            )
                
            try:
                if self.update_term == True:
                    src_text = self._extract_text_from_tex(transed_section["content"])
                    tgt_text = self._extract_text_from_tex(transed_section["trans_content"])
                    term_text = await self._request_llm_for_extract_terms(pm.extract_terminology_system_prompt,
                                                            src_text,
                                                            tgt_text,
                                                            session=session
                                                            )

                    # self._updated_term_dict(term_text)
                    self._updated_term_dict_v2(term_text)
            except Exception as e:
                return transed_section

        return transed_section

    async def _translate_caption(self, caption: Dict[str, Any], session: aiohttp.ClientSession, error_message=None) -> Dict[str, Any]:
        """
        Translates the captions of the input data.
        """
        transed_caption = caption.copy()
        placeholder = caption["placeholder"]
        if self.trans_mode == 0:
            transed_caption["trans_content"] = await self._request_llm_for_trans(pm.caption_system_prompt,
                                                        caption["content"],
                                                        fail_part=placeholder,
                                                        type="cap",
                                                        session=session
                                                        )
        elif self.trans_mode == 1:
            # Keep current retranslating path for captions.
            transed_caption["trans_content"] = await self._request_llm_for_retrans_error_parts(pm.retrans_error_parts_system_prompt,
                                                                                         part=transed_caption,
                                                                                         error_message=error_message,
                                                                                         fail_part=placeholder,
                                                                                         type="cap",
                                                                                         session=session)
            
        elif self.trans_mode == 2:
            if not self.term_dict:
                transed_caption["trans_content"] = await self._request_llm_for_trans(pm.caption_system_prompt,
                                                        caption["content"], 
                                                        fail_part=placeholder,
                                                        type="cap",
                                                        session=session
                                                        )
            else:
                transed_caption["trans_content"] = await self._request_llm_for_trans_with_terms(pm.caption_system_prompt_with_dict,
                                                                                          caption["content"],
                                                                                          fail_part=placeholder,
                                                                                          type="cap",
                                                                                          session=session)
            try:
                if self.update_term == True:
                    src_text = self._extract_text_from_tex(transed_caption["content"])
                    tgt_text = self._extract_text_from_tex(transed_caption["trans_content"])
                    term_text = await self._request_llm_for_extract_terms(pm.extract_terminology_system_prompt,
                                                            src_text,
                                                            tgt_text,
                                                            session=session
                                                            )

                    # self._updated_term_dict(term_text)
                    self._updated_term_dict_v2(term_text)
            except Exception as e:
                return transed_caption

        return transed_caption

    async def _translate_env(self, env: Dict[str, Any], session: aiohttp.ClientSession, error_message=None) -> Dict[str, Any]:
        """
        Translates an environment block (env) based on whether translation is needed.
        """
        transed_env = env.copy()
        placeholder = env["placeholder"]
        if self.trans_mode == 0: # sum
            if env["need_trans"]:
                transed_env["trans_content"] = await self._request_llm_for_trans(pm.env_system_prompt,
                                                            env["content"], 
                                                            fail_part=placeholder,
                                                            type="env",
                                                            session=session
                                                            )                
            else:
                transed_env["trans_content"] = env["content"]
        elif self.trans_mode == 1:
                transed_env["trans_content"] = await self._request_llm_for_retrans_error_parts(pm.retrans_error_parts_system_prompt,
                                                                                         part=transed_env,
                                                                                         error_message=error_message,
                                                                                         fail_part=placeholder,
                                                                                         type="env",
                                                                                         session = session)
        elif self.trans_mode == 2: # dict or sum+dict
            if not self.term_dict:
                if env["need_trans"]:
                    transed_env["trans_content"] = await self._request_llm_for_trans(pm.env_system_prompt,
                                                            env["content"], 
                                                            fail_part=placeholder,
                                                            type="env",
                                                            session=session
                                                            )
                else:
                    transed_env["trans_content"] = env["content"]
            else:
                if env["need_trans"]:
                    transed_env["trans_content"] = await self._request_llm_for_trans_with_terms(pm.env_system_prompt_with_dict,
                                                                                            env["content"],
                                                                                            fail_part=placeholder,
                                                                                            type="env",
                                                                                            session=session)
                else:
                    transed_env["trans_content"] = env["content"]

            if env["need_trans"]:
                try:
                    if self.update_term == True:
                        src_text = self._extract_text_from_tex(transed_env["content"])
                        tgt_text = self._extract_text_from_tex(transed_env["trans_content"])
                        text = await self._request_llm_for_extract_terms(pm.extract_terminology_system_prompt,
                                                                src_text,
                                                                tgt_text,
                                                                session=session
                                                                )

                            # self._updated_term_dict(term_text)
                        self._updated_term_dict_v2(text)
                except Exception as e:
                    return transed_env


        return transed_env

    def _build_payload(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        payload = {
            "model": f"{self.model}",
            "messages": messages,
            "temperature": 0.7,
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        return payload

    def _retry_delay(self, attempt: int, retry_after: Optional[str] = None) -> float:
        """
        Seconds to wait before the next attempt: honour a numeric Retry-After header
        (capped), otherwise exponential backoff from retry_base_delay.
        """
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), MAX_RETRY_AFTER_SECONDS)
            except ValueError:
                pass
        return self.retry_base_delay * (2 ** (attempt - 1))

    async def _post_chat(self, payload: Dict[str, Any], session: aiohttp.ClientSession) -> str:
        """
        POST an OpenAI-compatible chat completion request and return the stripped message content.

        Retries on network errors, timeouts, HTTP 429/5xx and malformed or empty responses.
        Other HTTP errors (400, 401, 403, 404, ...) fail immediately since retrying cannot help.
        Raises LLMRequestError when the request ultimately fails.
        """
        headers = {
            "Authorization": f"Bearer {self.API_KEY}",
            "Content-Type": "application/json"
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        last_error = "unknown error"

        for attempt in range(1, self.max_attempts + 1):
            retry_after = None
            try:
                async with session.post(self.base_url, json=payload, headers=headers, timeout=timeout) as response:
                    if response.status >= 400:
                        body = (await response.text())[:300]
                        last_error = f"HTTP {response.status}: {body}"
                        if response.status != 429 and response.status < 500:
                            raise LLMRequestError(last_error)
                        retry_after = response.headers.get("Retry-After")
                    else:
                        result = await response.json(content_type=None)
                        choice = result["choices"][0]
                        content = choice["message"]["content"]
                        if not isinstance(content, str) or not content.strip():
                            raise ValueError("empty message content")
                        if choice.get("finish_reason") == "length":
                            self.log("Warning: LLM output was truncated (finish_reason=length).")
                        return content.strip()
            except LLMRequestError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = f"{type(e).__name__}: {e or f'no response within {self.timeout}s'}"
            except (ValueError, KeyError, IndexError, TypeError) as e:
                # ValueError also covers JSON decode errors
                last_error = f"malformed response ({type(e).__name__}: {e})"

            if attempt < self.max_attempts:
                await asyncio.sleep(self._retry_delay(attempt, retry_after))

        raise LLMRequestError(f"failed after {self.max_attempts} attempts, last error: {last_error}")

    def _mark_fail_part(self, fail_part: str, type: str) -> None:
        self.have_fail_parts = True
        if type == 'sec':
            self.fail_section_nums.append(fail_part)
        elif type == 'cap':
            self.fail_caption_phs.append(fail_part)
        else:
            self.fail_env_phs.append(fail_part)

    def _translation_payload(self, system_prompt, user_prompt, part_id):
        context = self.source_contexts.get(part_id)
        if context:
            system_prompt += (
                "\nUse the source reference only to interpret the target fragment. "
                "Return only the translated or corrected target fragment."
            )
            user_prompt = f"[Source reference]\n{context}\n\n[Target fragment]\n{user_prompt}"
        return self._build_payload([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])

    async def _request_llm_for_trans(self,
                                     system_prompt: str,
                                     text: str,
                                     fail_part: str,
                                     type: str,
                                     session: aiohttp.ClientSession) -> str:
        payload = self._translation_payload(system_prompt, text, fail_part)
        try:
            content = await self._post_chat(payload, session)
        except LLMRequestError as e:
            self._mark_fail_part(fail_part, type)
            log_message(f"Failed to translate text, return original text: {fail_part}. {e}")
            return text
        return sanitize_llm_translation(text, content)

    async def _request_llm_for_trans_with_terms(self,
                                          system_prompt: str,
                                          text: str,
                                          fail_part: str,
                                          type: str,
                                          session: aiohttp.ClientSession) -> str:
        system_prompt = f"{system_prompt}\nWhen translating, you must strictly use the following glossary for substitution. This is the highest priority rule to ensure the consistency of terms throughout the text.\n<Glossary>:\n{self.term_dict}\nNow, please translate the following new paragraph. Maintain the terminology from the glossary provided."
        payload = self._translation_payload(system_prompt, f"[Current LaTeX Paragraph]:\n{text}", fail_part)
        try:
            content = await self._post_chat(payload, session)
        except LLMRequestError as e:
            self._mark_fail_part(fail_part, type)
            log_message(f"Failed to translate text, return original text: {fail_part}. {e}")
            return text
        return sanitize_llm_translation(text, content)

    async def _request_llm_for_retrans_error_parts(self,
                                                   system_prompt: str,
                                                   part: Dict[str, Any],
                                                   error_message: str,
                                                   fail_part: str,
                                                   type: str,
                                                   session: aiohttp.ClientSession) -> str:

        user_prompt = f"[Original]:\n{part['content']}\n[Translation]:\n{part['trans_content']}\n[Error]:\n{error_message}"
        system_prompt = f"{system_prompt}\nWhen translating, you must strictly use the following glossary for substitution. This is the highest priority rule to ensure the consistency of terms throughout the text.\n<Glossary>:\n{self.term_dict}\nNow, please translate the following new paragraph. Maintain the terminology from the glossary provided."
        payload = self._translation_payload(system_prompt, user_prompt, fail_part)
        try:
            content = await self._post_chat(payload, session)
        except LLMRequestError as e:
            self._mark_fail_part(fail_part, type)
            log_message(f"Failed to retranslate text, keep previous translation: {fail_part}. {e}")
            return part["trans_content"]
        return sanitize_llm_translation(part["content"], content)

    async def _request_llm_for_extract_terms(self, system_prompt, src, tgt,
                                       session: aiohttp.ClientSession) -> str:
        payload = self._build_payload([
            {
                "role": "system",
                "content": f"{system_prompt}"
            },
            {
                "role": "user",
                "content": f"<en source>\n{src}\n<zh translation>\n{tgt}"
            }
        ])
        try:
            return await self._post_chat(payload, session)
        except LLMRequestError as e:
            log_message(f"Warning: failed to extract terms, set N/A. {e}")
            return "N/A"

    def _updated_term_dict(self, text: str) -> None:
        """
        Updates the term dictionary with new terms.
        """
        pattern = r'"([^"]+)"\s*-\s*"([^"]+)"'
        matches = re.findall(pattern, text)

        seen_lower = {k.lower() for k in self.term_dict}
        
        for en, zh in matches:
            en_lower = en.lower()
            if en_lower not in seen_lower:
                self.term_dict[en] = zh  
                seen_lower.add(en_lower)

        self.save_file(Path(self.output_dir, "term_dict.json"), "json", self.term_dict)

    def _updated_term_dict_v2(self, text: str) -> None:

        new_term_dict = {}
        lines = text.split('\n')[1:]
        for line in lines:
            line = line.strip()
            if not line:
                continue  

            match = re.match(r'^"(.+?)"\s*-\s*"(.+?)"$', line)
            if match:
                english = match.group(1)
                chinese = match.group(2)
                new_term_dict[english] = chinese

        for en, zh in new_term_dict.items():
            if en not in self.term_dict:
                self.term_dict[en] = zh

    def _process_latex_to_eva(self, latex_code):
        latex_code = replace_href(latex_code)
        latex_code = replace_includegraphics(latex_code)
        return latex_code

    def _extract_text_from_tex(self, tex):
        # convert = CustomLatexNodes2Text()
        # text = convert.latex_to_text(tex)
        tex = self._process_latex_to_eva(tex)
        text = LatexNodes2Text().latex_to_text(tex)
        text = delete_ph(text)
        return text
    
    def build_term_dict(self):
        if self.user_term:
            df = pd.read_csv(self.user_term, header=None, names=['English Term', 'Chinese Translation'])
            self.term_dict.update(zip(df['English Term'], df['Chinese Translation']))
        else:
            arxiv_id = self.config.get("arxiv_id") or os.path.basename(self.project_dir)
            if self.category.get(arxiv_id):
                term_dict_loaded = False
                for category in self.category[arxiv_id]:
                    file_path = os.path.join('terms', f'{category}.csv')
                    try:
                        df = pd.read_csv(file_path, header=None, names=['English Term', 'Chinese Translation'])
                        self.term_dict.update(zip(df['English Term'], df['Chinese Translation']))
                        term_dict_loaded = True

                    except FileNotFoundError:
                        continue

                if not term_dict_loaded:
                    try:
                        df = pd.read_csv('terms/default.csv', header=None,
                                         names=['English Term', 'Chinese Translation'])
                        self.term_dict.update(zip(df['English Term'], df['Chinese Translation']))
                    except FileNotFoundError as e:
                        log_message(f"Error: Default terminology file not found: {e}")
            else:
                try:
                    df = pd.read_csv('terms/default.csv', header=None,
                                     names=['English Term', 'Chinese Translation'])
                    self.term_dict.update(zip(df['English Term'], df['Chinese Translation']))
                except FileNotFoundError as e:
                    log_message(f"Error: Default terminology file not found: {e}")

    def add_placeholder(self):

        # Add placeholders from caption, env, input, and newcommand to the vocabulary
        caption_path = os.path.join(self.output_dir, "captions_map.json")
        input_path = os.path.join(self.output_dir, "inputs_map.json")
        env_path = os.path.join(self.output_dir, "envs_map.json")
        command_path = os.path.join(self.output_dir, "newcommands_map.json")

        placeholder_list = []

        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for item in data:
            if "begin" in item:
                placeholder_list.append(item["begin"])
            if "end" in item:
                placeholder_list.append(item["end"])

        with open(env_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for item in data:
            if "placeholder" in item:
                placeholder_list.append(item["placeholder"])

        with open(caption_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for item in data:
            if "placeholder" in item:
                placeholder_list.append(item["placeholder"])

        with open(command_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for item in data:
            if "placeholder" in item:
                placeholder_list.append(item["placeholder"])

        for item in placeholder_list:
            self.term_dict[item] = item



