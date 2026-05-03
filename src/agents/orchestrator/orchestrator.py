import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_ENV_PATH, override=True)

from src.agents.clarify.clarify import AIGatekeeper, prepare_clarification_questions
from src.agents.scientist.scientist_agent import ScientistAgent
from src.agents.critic.stages.net_drc_1 import run_net_drc
from src.agents.critic.stages.floating_pin_detection_2 import run_floating_pin_detection
from src.agents.critic.stages.safety_verification_4 import run_safety_verification
from src.agents.critic.stages.parameters_autofill_5 import autofill_parameters
from src.agents.critic.stages.decision_point_6 import evaluate_pipeline
from src.agents.fixer.fixer_agent import FixerAgent
from src.agents.coder.coder_agent import CoderAgent, CoderAgentError
from src.interfaces.schemas.critic import CriticResult, CriticIssue, CriticScore
from src.interfaces.schemas.pipeline import PipelineIssue, CriticPipelineResult

from langchain_core.messages import HumanMessage, AIMessage

logger = logging.getLogger(__name__)






def run_critic_pipeline(blueprint):
    """running critic stages as you can see whatever I'm going to sleep"""
    blueprint, drc_report = run_net_drc(blueprint)
    print(f"\n{drc_report.summary()}")

    fp_report = run_floating_pin_detection(blueprint)
    print(f"\n{fp_report.summary()}")

    safety_report = run_safety_verification(blueprint)
    print(f"\n{safety_report.summary()}")

    blueprint = autofill_parameters(blueprint)

    stage_3_issues: list[PipelineIssue] = []
    for v in drc_report.reject_violations:
        stage_3_issues.append(PipelineIssue(
            severity=v.severity,
            auto_fixed=v.auto_fixed,
            description=v.description,
            check=v.check,
            affected_refs=v.affected_refs,
        ))
    for fp in fp_report.floating_pins:
        stage_3_issues.append(PipelineIssue(
            severity=fp.severity.value,
            auto_fixed=False,
            description=fp.reason,
            component_ref=fp.component_ref,
            pin_name=fp.pin_name,
        ))
    stage_4_issues: list[PipelineIssue] = []
    for check in safety_report.failed_checks:
        stage_4_issues.append(PipelineIssue(
            severity="CRITICAL",
            auto_fixed=False,
            description=check.description,
            check=check.check,
        ))


    reports = {
        "stage_3_issues": [
            {"severity": i.severity, "auto_fixed": i.auto_fixed,
             "description": i.description, "check": i.check,
             "affected_refs": i.affected_refs}
            for i in stage_3_issues
        ],
        "stage_4_issues": [
            {"severity": i.severity, "auto_fixed": i.auto_fixed,
             "description": i.description, "check": i.check}
            for i in stage_4_issues
        ],
    }
    decision = evaluate_pipeline(reports)
    print(f"\n=== Decision Point: {decision['status']} -> {decision['next_step']} ===")

    all_issues = stage_3_issues + stage_4_issues
    critic_issues = []
    for issue in all_issues:
        if issue.auto_fixed:
            continue
        critic_issues.append(CriticIssue(
            severity=issue.severity,
            category=issue.check or "net_integrity",
            component_ref=issue.component_ref,
            description=issue.description,
            fix_action=issue.description,
        ))

    is_sufficient = decision["status"] == "PASS"
    critic_result = CriticResult(
        is_sufficient=is_sufficient,
        summary=f"{decision['status']}: {len(critic_issues)} issue(s) found",
        score=CriticScore(
            goal_alignment="PASS",
            electrical_correctness="PASS" if not drc_report.has_errors else "FAIL",
            safety_components="PASS" if safety_report.overall_passed else "FAIL",
            net_integrity="PASS" if not fp_report.has_issues else "FAIL",
            data_completeness="PASS",
            library_verification="PASS",
            metadata_consistency="PASS",
        ),
        issues=critic_issues,
    )

    return CriticPipelineResult(
        blueprint=blueprint,
        decision=decision,
        critic_result=critic_result,
    )


def _require_api_key() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit(
            "OPENAI_API_KEY not set.\n"
            "Copy .env.example to .env and add your key, "
            "or export OPENAI_API_KEY=sk-... in your shell."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cnet",
        description="CNET — generate a netlist from a natural-language circuit description.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Circuit description. If omitted, an interactive REPL is started.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    _require_api_key()

    one_shot_prompt = " ".join(args.prompt).strip() if args.prompt else None

    clarify_agent = AIGatekeeper()
    history: list = []
    current_params: dict = {}

    while True:
        if one_shot_prompt is not None:
            user_text = one_shot_prompt
            print(f"Describe the Circuit> {user_text}")
        else:
            user_text = input("Describe the Circuit> ")
        clean_text = user_text.strip()

        if not clean_text:
            print("Empty is not allowed\n")
            continue

        if clean_text.lower() in ['q', 'exit', 'quit']:
            break


        context_text = clean_text
        if current_params:
            params_str = ", ".join([f"{k}: {v}" for k, v in current_params.items()])
            context_text = f"Context (Extracted so far: {params_str})\nUser Input: {clean_text}"

        print(f"Analyzing prompt: '{clean_text}'...")
        processed = clarify_agent.process(context_text, history=history)

        if processed.is_clear:
            print(f"Input is clear and actionable.")
            print(f"Refined Intent: {processed.refined_intent}")
            print(f"Extracted Parameters: {processed.extracted_parameters}")

            print("\nStarting Design Phase...")
            try:
                t0 = time.monotonic()
                scientist = ScientistAgent()
                print("Scientist Agent is designing...")

                params_for_scientist = dict(current_params)
                if processed.extracted_parameters:
                    params_for_scientist.update(processed.extracted_parameters)
                blueprint, _ = scientist.run(
                    processed.refined_intent,
                    extracted_parameters=params_for_scientist or None,
                )
                logger.info("Scientist phase completed in %.1fs", time.monotonic() - t0)

                print("\nScientist Blueprint:")
                print(blueprint.model_dump_json(indent=2))

                t0 = time.monotonic()
                print("\nRunning Critic Pipeline...")
                pipeline_result = run_critic_pipeline(blueprint)
                blueprint = pipeline_result.blueprint
                decision = pipeline_result.decision
                critic_result = pipeline_result.critic_result
                logger.info("Critic phase completed in %.1fs", time.monotonic() - t0)

                if decision["status"] == "FIX_NEEDED":
                    t0 = time.monotonic()
                    print("\nFixer Agent is repairing the blueprint...")

                    def _critic_callback(bp):
                        result = run_critic_pipeline(bp)
                        return result

                    fixer = FixerAgent()
                    fixer_result = fixer.run(
                        blueprint,
                        critic_result,
                        critic=_critic_callback,
                    )
                    blueprint = fixer_result.blueprint
                    print(f"\n{fixer_result.summary()}")
                    logger.info("Fixer phase completed in %.1fs", time.monotonic() - t0)

                    if not fixer_result.success:
                        print("\nFixer could not resolve all issues. Aborting.")
                        break

                    pipeline_result = run_critic_pipeline(blueprint)
                    blueprint = pipeline_result.blueprint

                t0 = time.monotonic()
                print("\nFinal Hardware Blueprint:")
                print(blueprint.model_dump_json(indent=2))
                coder = CoderAgent()
                result = coder.run(blueprint)
                logger.info("Coder phase completed in %.1fs", time.monotonic() - t0)
                print(f"\nNetlist written to: {result.netlist_path}")
                if result.svg_path:
                    print(f"SVG schematic written to: {result.svg_path}")

            except CoderAgentError as e:
                print(f"\nCoder Agent failed: {e}")
                if e.diagnostics:
                    print(f"  Stage: {e.diagnostics.get('stage_failed')}")
                    print(f"  Error: {e.diagnostics.get('error_message')}")
            except (ValueError, RuntimeError) as e:
                print(f"\nPipeline error: {e}")
            except Exception as e:
                logger.exception("Unexpected error during hardware design")
                print(f"\nUnexpected error: {e}")
            break
        else:
            print(f"Input needs clarification.")
            question = processed.clarification_question or "Could you provide more details?"
            print(f"Agent: {question}")


            if processed.extracted_parameters:
                current_params.update(processed.extracted_parameters)


            history.append(HumanMessage(content=clean_text))
            history.append(AIMessage(content=question))

            print()

        if one_shot_prompt is not None:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
