"""fixer agent"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Protocol

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from src.agents.scientist.analysis.tools import get_component_pins, search_components
from src.core.config import get as cfg
from src.core.paths import PROMPTS_DIR
from src.core import telemetry
from src.core.patching import (
    AmbiguousSelectorError,
    PolicyError,
    RefPathError,
    apply_groups,
    rewrite_plan,
    validate_plan,
)
from src.interfaces.schemas.critic import CriticResult
from src.interfaces.schemas.fixer_plan import (
    AppliedGroup,
    FixerPlan,
    FixOutcome,
    RejectedGroup,
)
from src.interfaces.schemas.pipeline import CriticPipelineResult
from src.interfaces.schemas.scientist import SystemBlueprint

logger = logging.getLogger(__name__)




@dataclass
class FixerResult:

    success: bool
    blueprint: SystemBlueprint
    attempts: int
    escalated: bool = False
    applied_groups: List[AppliedGroup] = field(default_factory=list)
    rejected_groups: List[RejectedGroup] = field(default_factory=list)

    def summary(self) -> str:
        max_retries = cfg("agents", "fixer", "max_retries", default=3)
        status = "PASS" if self.success else ("ESCALATED" if self.escalated else "FAIL")
        return (
            f"=== Fixer Result: {status} ===\n"
            f"Attempts: {self.attempts}/{max_retries}\n"
            f"Groups applied : {len(self.applied_groups)}\n"
            f"Groups rejected: {len(self.rejected_groups)}"
        )


class CriticFn(Protocol):
    def __call__(self, blueprint: SystemBlueprint) -> CriticPipelineResult: ...




class FixerAgent:
    def __init__(self, model: str | None = None, temperature: float | None = None):
        self.model = model or cfg("agents", "fixer", "model", default="gpt-4o-mini")
        self.temperature = (
            temperature
            if temperature is not None
            else cfg("agents", "fixer", "temperature", default=0.0)
        )
        self.mode: str = cfg("agents", "fixer", "mode", default="patch")
        if self.mode not in {"patch", "rewrite"}:
            raise ValueError(
                f"Invalid agents.fixer.mode='{self.mode}' (expected 'patch' or 'rewrite')"
            )

        self.tools = [search_components, get_component_pins]
        self.llm = ChatOpenAI(model=self.model, temperature=self.temperature)
        self.llm_with_tools = self.llm.bind_tools(self.tools, parallel_tool_calls=False)

        self._plan_llm = self.llm.with_structured_output(
            FixerPlan, method="function_calling"
        )
        self._blueprint_llm = self.llm.with_structured_output(
            SystemBlueprint, method="function_calling"
        )

        prompt_file = (
            "fixer_agent_patch_prompt.txt"

        )
        prompt_path = PROMPTS_DIR / "fixer" / prompt_file
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()


    def _format_issues(self, critic_result: CriticResult) -> str:
        return "\n".join(
            f"{i + 1}. [{issue.severity}] {issue.category}"
            f"{f' (component: {issue.component_ref})' if issue.component_ref else ''}"
            f": {issue.description}\n   FIX: {issue.fix_action}"
            for i, issue in enumerate(critic_result.issues)
        )

    def _format_applied_log(self, applied: List[AppliedGroup]) -> str:
        if not applied:
            return "(none yet)"
        return "\n".join(
            f"- issue {a.issue_index}: {a.rationale} ({a.op_count} ops)"
            for a in applied
        )

    def _format_rejected_log(self, rejected: List[RejectedGroup]) -> str:
        if not rejected:
            return "(none yet)"
        return "\n".join(
            f"- issue {r.issue_index}: {r.rationale} — {r.reason}"
            for r in rejected
        )

    def _run_tool_loop(self, messages: list) -> list:
        max_tool_calls = cfg("agents", "fixer", "max_tool_calls", default=25)
        for _ in range(max_tool_calls):
            response = self.llm_with_tools.invoke(messages)
            messages.append(response)
            if not response.tool_calls:
                break
            for tool_call in response.tool_calls:
                if tool_call["name"] == "search_components":
                    result = search_components.invoke(tool_call["args"])
                elif tool_call["name"] == "get_component_pins":
                    result = get_component_pins.invoke(tool_call["args"])
                else:
                    messages.append(
                        ToolMessage(
                            content=f"Error: Tool {tool_call['name']} not found",
                            tool_call_id=tool_call["id"],
                        )
                    )
                    continue
                messages.append(
                    ToolMessage(
                        content=json.dumps(result), tool_call_id=tool_call["id"]
                    )
                )
        return messages


    def _fix_patch(
        self,
        blueprint: SystemBlueprint,
        critic_result: CriticResult,
        applied_log: List[AppliedGroup],
        rejected_log: List[RejectedGroup] | None = None,
    ) -> FixOutcome:
        prefer_index = cfg("agents", "fixer", "index_selectors", default=True)
        selector_hint = (
            "Use ARRAY-INDEX JSON pointers (/components/3/value, "
            "/nets/14/connections/2/pin_name). Name-based ref-paths are "
            "only allowed when the name is unique; duplicates (e.g. VBUS "
            "pins on a USB-C receptacle) MUST use numeric indices."
            if prefer_index
            else "Use ref-paths. Preconditions must use op='test'."
        )
        rejected_section = (
            f"\n## Rejected in previous attempt (DO NOT repeat you idiot :D)\n"
            f"{self._format_rejected_log(rejected_log or [])}\n"
            if rejected_log
            else ""
        )
        user_content = (
            f"## Current Blueprint (authoritative)\n"
            f"{blueprint.model_dump_json(indent=2)}\n\n"
            f"## Issues to Fix ({len(critic_result.issues)} total)\n"
            f"{self._format_issues(critic_result)}\n\n"
            f"## Already-applied groups (do NOT redo)\n"
            f"{self._format_applied_log(applied_log)}"
            f"{rejected_section}\n"
            f"## Critic Summary\n"
            f"{critic_result.summary}\n\n"
            f"Emit a FixerPlan JSON object. {selector_hint} "
            f"Preconditions must use op='test'."
        )
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_content),
        ]
        messages = self._run_tool_loop(messages)

        final_prompt = HumanMessage(
            content="Investigation complete. Output the FixerPlan JSON object now."
        )
        plan: FixerPlan = self._plan_llm.invoke(messages + [final_prompt])

        try:
            validate_plan(plan)
        except PolicyError as exc:
            logger.warning("Fixer plan violated policy: %s", exc)
            return FixOutcome(
                blueprint=blueprint,
                applied_groups=[],
                rejected_groups=[
                    RejectedGroup(
                        issue_index=-1,
                        rationale="whole-plan policy violation",
                        reason=str(exc),
                    )
                ],
            )

        try:
            blueprint_dict = json.loads(blueprint.model_dump_json())
            rewritten = rewrite_plan(plan, blueprint_dict)
        except AmbiguousSelectorError as exc:
            logger.warning(
                "Fixer plan has ambiguous ref-path: %s (candidates=%s)",
                exc, exc.candidates,
            )
            return FixOutcome(
                blueprint=blueprint,
                applied_groups=[],
                rejected_groups=[
                    RejectedGroup(
                        issue_index=-1,
                        rationale=(
                            f"ambiguous selector {exc.filters}; "
                            f"use one of /<collection>/<index> with index in {exc.candidates}"
                        ),
                        reason=str(exc),
                    )
                ],
            )
        except RefPathError as exc:
            logger.warning("Fixer plan has unresolved ref-paths: %s", exc)
            return FixOutcome(
                blueprint=blueprint,
                applied_groups=[],
                rejected_groups=[
                    RejectedGroup(
                        issue_index=-1,
                        rationale="whole-plan ref-path failure",
                        reason=str(exc),
                    )
                ],
            )

        new_blueprint, applied, rejected = apply_groups(blueprint, rewritten)
        return FixOutcome(
            blueprint=new_blueprint,
            applied_groups=applied,
            rejected_groups=rejected,
        )

    def _fix_rewrite(
        self, blueprint: SystemBlueprint, critic_result: CriticResult
    ) -> SystemBlueprint:
        issues_text = self._format_issues(critic_result)
        user_content = (
            f"## Current Blueprint (preserve unless flagged)\n"
            f"{blueprint.model_dump_json(indent=2)}\n\n"
            f"## Issues to Fix ({len(critic_result.issues)} total)\n"
            f"{issues_text}\n\n"
            f"## Critic Summary\n"
            f"{critic_result.summary}\n\n"
            f"Fix ONLY the issues above. Output the corrected SystemBlueprint JSON."
        )
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_content),
        ]
        messages = self._run_tool_loop(messages)
        final_prompt = HumanMessage(
            content="Fixes applied. Now output the corrected SystemBlueprint JSON."
        )
        return self._blueprint_llm.invoke(messages + [final_prompt])

    def fix(
        self, blueprint: SystemBlueprint, critic_result: CriticResult
    ) -> SystemBlueprint:
        if self.mode == "rewrite":
            return self._fix_rewrite(blueprint, critic_result)
        outcome = self._fix_patch(blueprint, critic_result, applied_log=[])
        return outcome.blueprint

    def run(
        self,
        blueprint: SystemBlueprint,
        critic_result: CriticResult,
        critic: CriticFn,
    ) -> FixerResult:
        max_retries = cfg("agents", "fixer", "max_retries", default=3)
        current_bp = blueprint
        current_critic = critic_result
        applied_log: List[AppliedGroup] = []
        rejected_log: List[RejectedGroup] = []

        for attempt in range(1, max_retries + 1):
            logger.info(
                "Fixer attempt %d/%d — %d issue(s) to fix (mode=%s)",
                attempt,
                max_retries,
                len(current_critic.issues),
                self.mode,
            )

            if self.mode == "patch":
                recent_rejected = rejected_log[-10:] if rejected_log else None
                outcome = self._fix_patch(
                    current_bp, current_critic, applied_log, recent_rejected,
                )
                fixed = outcome.blueprint
                applied_log.extend(outcome.applied_groups)
                rejected_log.extend(outcome.rejected_groups)
                print(
                    f"\n=== Fixer Attempt {attempt}/{max_retries} — "
                    f"{len(outcome.applied_groups)} applied / "
                    f"{len(outcome.rejected_groups)} rejected ==="
                )
            else:
                fixed = self._fix_rewrite(current_bp, current_critic)
                print(f"\n=== Fixer Attempt {attempt}/{max_retries} — Fixed Blueprint ===")
                print(fixed.model_dump_json(indent=2))

            pipeline_result = critic(fixed)
            new_critic = pipeline_result.critic_result
            processed_bp = pipeline_result.blueprint

            print(f"\n{'=' * 60}")
            print(f"  Critic Re-validation — Attempt {attempt}/{max_retries}")
            print(f"{'=' * 60}")
            print(f"  Sufficient : {new_critic.is_sufficient}")
            print(f"  Summary    : {new_critic.summary}")
            if new_critic.issues:
                print(f"  Remaining Issues ({len(new_critic.issues)}):")
                for i, issue in enumerate(new_critic.issues, 1):
                    ref = f" ({issue.component_ref})" if issue.component_ref else ""
                    print(
                        f"    {i}. [{issue.severity}] {issue.category}{ref}: {issue.description}"
                    )
            print(f"{'=' * 60}")

            if new_critic.is_sufficient:
                logger.info("Critic passed on attempt %d", attempt)
                telemetry.record(blueprint.design_id, "fixer", {
                    "attempts": attempt,
                    "groups_applied": len(applied_log),
                    "groups_rejected": len(rejected_log),
                    "rewrite_fallback_used": False,
                    "escalated": False,
                })
                return FixerResult(
                    success=True,
                    blueprint=processed_bp,
                    attempts=attempt,
                    applied_groups=applied_log,
                    rejected_groups=rejected_log,
                )

            current_bp = processed_bp
            current_critic = new_critic

        if self.mode == "patch" and cfg("agents", "fixer", "rewrite_fallback", default=True):
            logger.warning(
                "Fixer op-based retries exhausted; invoking subtree-rewrite fallback"
            )
            rewritten = self._fix_rewrite(current_bp, current_critic)
            pipeline_result = critic(rewritten)
            new_critic = pipeline_result.critic_result
            processed_bp = pipeline_result.blueprint
            print(f"\n{'=' * 60}")
            print("  Fixer Subtree-Rewrite Fallback — Critic Re-validation")
            print(f"{'=' * 60}")
            print(f"  Sufficient : {new_critic.is_sufficient}")
            print(f"  Summary    : {new_critic.summary}")
            print(f"{'=' * 60}")
            if new_critic.is_sufficient:
                telemetry.record(blueprint.design_id, "fixer", {
                    "attempts": max_retries + 1,
                    "groups_applied": len(applied_log),
                    "groups_rejected": len(rejected_log),
                    "rewrite_fallback_used": True,
                    "escalated": False,
                })
                return FixerResult(
                    success=True,
                    blueprint=processed_bp,
                    attempts=max_retries + 1,
                    applied_groups=applied_log,
                    rejected_groups=rejected_log,
                )
            current_bp = processed_bp

        logger.error("Fixer exhausted %d retries — escalating", max_retries)
        telemetry.record(blueprint.design_id, "fixer", {
            "attempts": max_retries,
            "groups_applied": len(applied_log),
            "groups_rejected": len(rejected_log),
            "rewrite_fallback_used": cfg("agents", "fixer", "rewrite_fallback", default=True) and self.mode == "patch",
            "escalated": True,
        })
        return FixerResult(
            success=False,
            blueprint=current_bp,
            attempts=max_retries,
            escalated=True,
            applied_groups=applied_log,
            rejected_groups=rejected_log,
        )
