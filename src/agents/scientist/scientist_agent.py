import json
import logging
import re
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=True)

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, BaseMessage, AIMessage
from src.core.config import get as cfg
from src.core.paths import PROMPTS_DIR
from src.core import telemetry
from src.interfaces.schemas.scientist import SystemBlueprint
from .analysis.tools import search_components, get_component_pins


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 40) -> str:
    s = _SLUG_RE.sub("_", (text or "").lower()).strip("_")
    return (s[:max_len].rstrip("_")) or "design"


def make_design_id(title: str | None = None, refined_intent: str | None = None) -> str:
    base = _slugify(title or refined_intent or "design")
    suffix = f"{int(time.time()):x}_{uuid.uuid4().hex[:4]}"
    return f"{base}_{suffix}"


_PARAM_TO_CONSTRAINT: dict[str, str] = {
    "input_voltage": "input_voltage",
    "input_voltage_range": "input_voltage",
    "output_voltage": "output_voltage",
    "max_current": "max_current",
    "operating_temp_range": "operating_temp_range",
}


def _build_initial_message(
    design_id: str,
    refined_intent: str,
    extracted_parameters: dict | None,
) -> HumanMessage:
    lines = [f"Design ID: {design_id}", f"User Intent: {refined_intent}"]
    if extracted_parameters:
        lines.append("")
        lines.append("## Verified Design Constraints (treat as authoritative)")
        for key, value in extracted_parameters.items():
            lines.append(f"  {key}: {value}")
        lines.append("")
        lines.append(
            "These constraints were extracted verbatim from the user's "
            "responses. Populate design_constraints and all relevant "
            "component selections from them. Do NOT ignore or paraphrase them."
        )
    return HumanMessage(content="\n".join(lines))


def _merge_constraints(
    blueprint: SystemBlueprint, extracted_parameters: dict | None
) -> None:

    if not extracted_parameters:
        return
    dc = blueprint.design_constraints
    for raw_key, value in extracted_parameters.items():
        target = _PARAM_TO_CONSTRAINT.get(raw_key)
        if target is not None and getattr(dc, target, None) in (None, ""):
            setattr(dc, target, str(value))
        elif target is None:
            current = getattr(dc, raw_key, None)
            if current in (None, ""):
                setattr(dc, raw_key, value)


class ScientistAgent:
    def __init__(self, model=None, temperature=None):
        model = model or cfg("agents", "scientist", "model", default="gpt-5.4")
        temperature = temperature if temperature is not None else cfg("agents", "scientist", "temperature", default=0.0)

        self.llm = ChatOpenAI(model=model, temperature=temperature)

        self.tools = [search_components, get_component_pins]
        self.llm_with_tools = self.llm.bind_tools(self.tools, parallel_tool_calls=True)

        prompt_path = PROMPTS_DIR / "scientist" / "scientist_system_prompt.txt"

        with open(prompt_path, 'r', encoding='utf-8') as f:
            self.system_prompt = f.read()

        self.structured_llm = self.llm.with_structured_output(
            SystemBlueprint,
            method="function_calling"
        )

    def run(
        self,
        refined_intent: str,
        design_id: str = None,
        feedback: str = None,
        history: list[BaseMessage] = None,
        extracted_parameters: dict | None = None,
    ) -> tuple[SystemBlueprint, list[BaseMessage]]:
        if history:
            messages = list(history)
            if feedback:
                messages.append(HumanMessage(content=f"CRITIC FEEDBACK: {feedback}\nPlease correct the design based on this feedback."))
        else:
            messages = [
                SystemMessage(content=self.system_prompt),
                _build_initial_message(design_id, refined_intent, extracted_parameters),
            ]

        for _ in range(cfg("agents", "scientist", "max_tool_calls", default=10)):
            response = self.llm_with_tools.invoke(messages)
            messages.append(response)

            if not response.tool_calls:
                break

            for tool_call in response.tool_calls:
                if tool_call["name"] == "search_components":
                    result = search_components.invoke(tool_call["args"])
                    messages.append(ToolMessage(content=json.dumps(result), tool_call_id=tool_call["id"]))
                elif tool_call["name"] == "get_component_pins":
                    result = get_component_pins.invoke(tool_call["args"])
                    messages.append(ToolMessage(content=json.dumps(result), tool_call_id=tool_call["id"]))
                else:
                    messages.append(ToolMessage(content=f"Error: Tool {tool_call['name']} not found", tool_call_id=tool_call["id"]))

        final_prompt = HumanMessage(content="Design complete. Now, based on the gathered data above, output the strictly valid SystemBlueprint JSON object.")

        blueprint = self.structured_llm.invoke(messages + [final_prompt])

        if not design_id:
            design_id = make_design_id(blueprint.title, refined_intent)
        blueprint.design_id = design_id

        telemetry.record(design_id, "scientist", {
            "tool_loop_iterations": sum(
                1 for m in messages if isinstance(m, AIMessage)
            ),
        })

        _merge_constraints(blueprint, extracted_parameters)

        total = len(blueprint.components)
        verified = sum(1 for c in blueprint.components if c.library_verified)
        if blueprint.metadata.total_components == 0:
            blueprint.metadata.total_components = total
        if blueprint.metadata.verified_components == 0:
            blueprint.metadata.verified_components = verified
        if blueprint.metadata.unverified_components == 0:
            blueprint.metadata.unverified_components = total - verified

        return blueprint, messages



if __name__ == "__main__":
    main()
