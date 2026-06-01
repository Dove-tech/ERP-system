import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

import yaml

from utils import logger


PROMPT_ROOT = os.path.join(os.path.dirname(__file__), "prompt_registry")


@dataclass
class PromptSpec:
    prompt_id: str
    version: str
    template: str
    output_parser: str = "text"


class PromptRegistry:
    """
    Loads versioned prompt templates and model profiles from prompt_registry/.

    The registry deliberately stays small: it handles prompt lookup, model-family
    routing, and variable rendering. Business code continues to call PromptHub.
    """

    def __init__(self, root: str = PROMPT_ROOT):
        self.root = root
        self.profiles = self._load_profiles()

    def _load_profiles(self) -> Dict[str, Any]:
        profile_path = os.path.join(self.root, "profiles.yaml")
        if not os.path.exists(profile_path):
            return {}
        with open(profile_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def detect_family(self, model_name: str) -> str:
        model_name = (model_name or "").lower()
        if "qwen" in model_name:
            return "qwen"
        if "deepseek" in model_name:
            return "deepseek"
        return "generic"

    def get_version(self, prompt_id: str, model_name: str) -> str:
        family = self.detect_family(model_name)
        profiles = self.profiles.get("model_profiles", {})
        profile = profiles.get(family, profiles.get("generic", {}))
        prompt_versions = profile.get("prompt_versions", {})
        return prompt_versions.get(prompt_id, "v1")

    def load(self, prompt_id: str, model_name: str) -> PromptSpec:
        version = self.get_version(prompt_id, model_name)
        path = os.path.join(self.root, prompt_id, f"{version}.yaml")
        if not os.path.exists(path):
            path = os.path.join(self.root, prompt_id, "v1.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return PromptSpec(
            prompt_id=data.get("id", prompt_id),
            version=data.get("version", version),
            template=data.get("template", ""),
            output_parser=data.get("output_parser", "text"),
        )

    def render(self, prompt_id: str, model_name: str, variables: Dict[str, Any]) -> str:
        spec = self.load(prompt_id, model_name)
        prompt = spec.template
        for key, value in variables.items():
            prompt = prompt.replace("{{ " + key + " }}", str(value))
            prompt = prompt.replace("{{" + key + "}}", str(value))
        logger.debug(f"Rendered prompt {prompt_id}:{spec.version} for model={model_name}")
        return prompt


class StructuredOutputParser:
    """Small JSON-first parser with permissive fallback for existing model outputs."""

    @staticmethod
    def extract_json(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[len("```json"):].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def parse_intent(text: str) -> Dict[str, Any]:
        data = StructuredOutputParser.extract_json(text)
        if data:
            intent = str(data.get("intent", "")).lower().strip()
            if intent in {"confirm", "abort", "unclear"}:
                return {
                    "intent": intent,
                    "confidence": float(data.get("confidence", 0.0) or 0.0),
                    "reason": str(data.get("reason", "")),
                }

        lowered = (text or "").lower()
        for intent in ("confirm", "abort", "unclear"):
            if intent in lowered:
                return {"intent": intent, "confidence": 0.5, "reason": "fallback keyword parse"}
        return {"intent": "unclear", "confidence": 0.0, "reason": "unparseable model output"}

    @staticmethod
    def parse_tool_action(text: str) -> Optional[str]:
        data = StructuredOutputParser.extract_json(text)
        if data:
            action = data.get("action") or data.get("tool") or data.get("tool_name")
            if action:
                return str(action).strip()

        if not text:
            return None
        for line in text.strip().splitlines():
            if "Action:" in line:
                return line.split("Action:", 1)[1].strip().strip(",[]")
        return None

    @staticmethod
    def parse_slot_filling_intent(text: str) -> Dict[str, Any]:
        data = StructuredOutputParser.extract_json(text) or {}
        intent = str(data.get("intent", "")).lower().strip()
        if intent not in {"provide_info", "abort", "unclear"}:
            lowered = (text or "").lower()
            if any(word in lowered for word in ("取消", "停止", "放弃", "不执行", "abort", "cancel", "no")):
                intent = "abort"
            else:
                intent = "unclear"
        filled_params = data.get("filled_params", {})
        if not isinstance(filled_params, dict):
            filled_params = {}
        return {
            "intent": intent,
            "filled_params": filled_params,
            "confidence": StructuredOutputParser._safe_float(data.get("confidence", 0.0)),
            "reason": str(data.get("reason", "")),
        }

    @staticmethod
    def parse_ambiguity_feedback_intent(text: str) -> Dict[str, Any]:
        data = StructuredOutputParser.extract_json(text) or {}
        intent = str(data.get("intent", "")).lower().strip()
        if intent not in {"confirm_candidate", "provide_info", "abort", "unclear"}:
            lowered = (text or "").lower()
            if any(word in lowered for word in ("确认", "继续", "可以", "同意", "ok", "yes", "confirm")):
                intent = "confirm_candidate"
            elif any(word in lowered for word in ("取消", "停止", "放弃", "不执行", "abort", "cancel", "no")):
                intent = "abort"
            else:
                intent = "unclear"
        filled_facts = data.get("filled_facts", {})
        if not isinstance(filled_facts, dict):
            filled_facts = {}
        return {
            "intent": intent,
            "revised_query": str(data.get("revised_query", "") or ""),
            "filled_facts": filled_facts,
            "confidence": StructuredOutputParser._safe_float(data.get("confidence", 0.0)),
            "reason": str(data.get("reason", "")),
        }

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except Exception:
            return 0.0
