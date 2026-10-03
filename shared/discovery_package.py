from __future__ import annotations

"""Pure helpers for deterministic Discovery package summaries."""

from typing import Any


def observed_tools_inventory(bundles: list[dict[str, Any]]) -> dict[str, Any]:
    apps: dict[str, int] = {}
    sites: dict[str, int] = {}
    for bundle in bundles:
        if not isinstance(bundle, dict):
            continue
        for group in bundle.get("canonical_evidence") or []:
            if not isinstance(group, dict):
                continue
            for event in group.get("events") or []:
                if not isinstance(event, dict):
                    continue
                app = str(event.get("app") or "").strip()
                if app and app != "Excluded":
                    apps[app] = apps.get(app, 0) + 1
                meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
                page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
                host = str(page.get("hostname") or "").strip().lower()
                if host:
                    sites[host] = sites.get(host, 0) + 1
    return {
        "apps": [
            {"name": key, "observations": apps[key]}
            for key in sorted(apps)
        ],
        "sites": [
            {"hostname": key, "observations": sites[key]}
            for key in sorted(sites)
        ],
        "caveat": (
            "Observed use does not mean the user or organization has an API, "
            "connector, license, or permission for that tool."
        ),
        "deterministic": True,
    }


__all__ = ["build_brief_markdown", "observed_tools_inventory"]


def _quote_markdown(value: Any, *, fallback: str = "(not provided)") -> str:
    text = str(value or "").strip()
    if not text:
        return f"> {fallback}"
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def _seconds_label(value: Any) -> str:
    try:
        seconds = max(0.0, float(value or 0.0))
    except Exception:
        seconds = 0.0
    if seconds < 60:
        return f"{round(seconds, 1):g} sec"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{round(minutes, 1):g} min"
    return f"{round(minutes / 60.0, 1):g} hr"


def build_brief_markdown(package: dict[str, Any]) -> str:
    """Render a deterministic implementation brief from an approved/preview package.

    This deliberately does not decide what should be automated. It organizes
    observed evidence, human-provided context, and explicit unknowns so an
    external AI/implementation team can make those judgments with provenance.
    """
    study = package.get("study") if isinstance(package.get("study"), dict) else {}
    implementation = (
        study.get("implementation_context")
        if isinstance(study.get("implementation_context"), dict)
        else {}
    )
    coverage = package.get("coverage") if isinstance(package.get("coverage"), dict) else {}
    inventory = (
        package.get("observed_tools")
        if isinstance(package.get("observed_tools"), dict)
        else {}
    )
    bundles = [
        item
        for item in (package.get("workflow_evidence") or [])
        if isinstance(item, dict)
    ]
    answered = [
        item
        for item in (package.get("human_statements") or [])
        if isinstance(item, dict)
    ]
    unanswered = [
        item
        for item in (package.get("saved_unanswered_questions") or [])
        if isinstance(item, dict)
    ]
    suggested = [
        item
        for item in (package.get("suggested_targeted_questions") or [])
        if isinstance(item, dict)
    ]
    handoff = package.get("handoff") if isinstance(package.get("handoff"), dict) else {}

    lines: list[str] = [
        "# OpenWorkGraph implementation brief",
        "",
        "This brief is deterministic. OpenWorkGraph has not used an LLM to decide what the workflow means, what should be automated, or what the worker is allowed to do.",
        "",
        "## Human-provided goal and context",
        "",
        "**Goal**",
        "",
        _quote_markdown(implementation.get("goal")),
        "",
        "**Worker description**",
        "",
        _quote_markdown(implementation.get("description")),
        "",
        "These statements are human-provided context, not facts inferred from observed activity.",
        "",
        "## Observation scope",
        "",
        f"- Study: {study.get('name') or 'Workflow discovery'}",
        f"- Window: {study.get('starts_at') or '?'} to {study.get('ends_at') or '?'}",
        f"- Selected executions: {int(coverage.get('selected_execution_count') or 0)}",
        f"- Workflow bundles: {int(coverage.get('workflow_bundles_included') or 0)}",
        f"- Handoff purpose: {handoff.get('purpose') or study.get('handoff_purpose') or 'automate'}",
        "",
        "Observed frequency below means frequency **inside this study window only**. It is not a claim about weekly/monthly business volume.",
        "",
        "## Observed tools",
        "",
        "### Apps",
    ]
    apps = [item for item in inventory.get("apps") or [] if isinstance(item, dict)]
    lines.extend(
        [
            f"- {item.get('name')}: {int(item.get('observations') or 0)} observations"
            for item in apps
        ]
        or ["- No native app count available"]
    )
    lines.extend(["", "### Sites"])
    sites = [item for item in inventory.get("sites") or [] if isinstance(item, dict)]
    lines.extend(
        [
            f"- {item.get('hostname')}: {int(item.get('observations') or 0)} observations"
            for item in sites
        ]
        or ["- No browser host count available"]
    )
    lines.extend(
        [
            "",
            f"> {inventory.get('caveat') or 'Observed use does not prove API access, licensing, or permission.'}",
            "",
            "## Workflow reconstruction",
            "",
        ]
    )

    if not bundles:
        lines.extend(
            [
                "No repeated workflow bundle met the deterministic inclusion threshold. Inspect the canonical evidence directly rather than inventing a workflow.",
                "",
            ]
        )
    for index, bundle in enumerate(bundles[:12], start=1):
        selector = bundle.get("selector") if isinstance(bundle.get("selector"), dict) else {}
        alignment = (
            bundle.get("structural_alignment")
            if isinstance(bundle.get("structural_alignment"), dict)
            else {}
        )
        timing = bundle.get("timing") if isinstance(bundle.get("timing"), dict) else {}
        durations = (
            timing.get("execution_duration_seconds")
            if isinstance(timing.get("execution_duration_seconds"), dict)
            else {}
        )
        resources = [
            item
            for item in (bundle.get("resource_types") or [])
            if isinstance(item, dict)
        ]
        transfers = (
            (bundle.get("data_movement") or {}).get("clipboard_transfers")
            if isinstance(bundle.get("data_movement"), dict)
            else []
        ) or []
        stable_steps = [
            item
            for item in (alignment.get("high_support_steps") or [])
            if isinstance(item, dict)
        ]
        variations = [
            item
            for item in (alignment.get("less_common_observed_steps") or [])
            if isinstance(item, dict)
        ]
        run_count = int(
            selector.get("selected_execution_count")
            or len(bundle.get("executions") or [])
            or 0
        )
        surface_count = len(
            {
                str(item.get("surface") or "")
                for item in (timing.get("observed_surface_foreground_time") or [])
                if isinstance(item, dict) and item.get("surface")
            }
        )
        lines.extend(
            [
                f"### Workflow {index}",
                "",
                f"- Observed runs in study: {run_count}",
                f"- Median observed duration: {_seconds_label(durations.get('median'))}",
                f"- Stable observed steps: {len(stable_steps)}",
                f"- Observed surfaces with timing: {surface_count}",
                f"- Observed copy/paste transfer patterns: {len(transfers)}",
                f"- Observed resource types: {len(resources)}",
                "",
                "**Stable observed steps**",
                "",
            ]
        )
        if stable_steps:
            ordered_steps = sorted(
                stable_steps,
                key=lambda item: (
                    float(item.get("median_zero_based_position") or 0.0),
                    str(item.get("step") or ""),
                ),
            )
            for step_index, item in enumerate(ordered_steps[:16], start=1):
                lines.append(
                    f"{step_index}. {item.get('step') or 'Observed step'} "
                    f"— seen in {int(item.get('support_runs') or 0)}/{int(item.get('runs_total') or run_count)} runs"
                )
        else:
            lines.append("- No high-support readable step sequence was established.")
        lines.extend(["", "**Observed variations**", ""])
        if variations:
            for item in variations[:8]:
                lines.append(
                    f"- {item.get('step') or 'Observed variation'} "
                    f"— seen in {int(item.get('support_runs') or 0)}/{int(item.get('runs_total') or run_count)} runs"
                )
        else:
            lines.append("- No less-common readable step was returned for this bundle.")
        lines.append("")

    lines.extend(
        [
            "## Employee-provided explanations",
            "",
        ]
    )
    if answered:
        for item in answered:
            lines.extend(
                [
                    f"**{item.get('question') or 'Question'}**",
                    "",
                    _quote_markdown(item.get("answer"), fallback="(no answer)"),
                    "",
                ]
            )
    else:
        lines.extend(["No employee explanation has been saved yet.", ""])

    lines.extend(["## Open implementation questions", ""])
    questions: list[str] = []
    seen: set[str] = set()
    for item in [*unanswered, *suggested]:
        question = str(item.get("question") or "").strip()
        if question and question not in seen:
            seen.add(question)
            questions.append(question)
    for generic in (
        "What is the source of truth for each input the future system will need?",
        "Which observed outputs have downstream consumers, reports, controls, or audit requirements?",
        "Which save/send/approve/financial/regulated actions require a human or organization-defined approval boundary?",
        "Which observed tools have an authorized API, connector, browser/computer interface, license, and service account available to the implementation?",
        "What exceptions or seasonal/rare variants were not observed during this study window?",
    ):
        if generic not in seen:
            questions.append(generic)
    lines.extend([f"- {question}" for question in questions] or ["- No open question recorded."])

    lines.extend(
        [
            "",
            "## Implementation decision table",
            "",
            "| Area | What OWG observed | Decision the implementation team must make |",
            "| --- | --- | --- |",
            "| Inputs | Apps/sites/resource types and bounded canonical evidence | Which live source/API/connector should supply each required value? |",
            "| Data movement | Copy/paste occurrence and linkage only; clipboard values are not captured | Can the transfer be eliminated or replaced with a direct authorized integration? |",
            "| Workflow steps | Repeated and less-common observed steps with support counts | Which steps are required business logic, which are UI mechanics, and which can disappear? |",
            "| Consequential actions | Saves/sends/approvals may appear as observed actions | What explicit policy/approval boundary applies before an agent performs them? |",
            "| Exceptions | Only observed variants and employee explanations are present | What unobserved exceptions need tests, escalation, or human handling? |",
            "",
            "## Permissions, dependencies, and risks",
            "",
            "- Observed use never proves API access, licensing, credentials, organizational permission, or authorization.",
            "- Repetition does not make a step policy and does not grant an agent permission to act.",
            "- Missing historical payload is not automatically an automation blocker; an authorized implementation may fetch the live value from the source system at execution time.",
            "- Redacted exports keep exact provider object IDs opaque. Full local AI context can resolve locally retained object references without putting those IDs into the redacted package.",
            "- File contents are not copied into OWG. An authorized local AI may read an observed file on demand only when its fingerprint still matches the version OWG observed.",
            "- Typed text and clipboard contents are not captured, so they must never be reconstructed or guessed from this package.",
            "- Rare, seasonal, off-device, or out-of-scope work may be missing.",
            "- Structural evidence is not executable test data. Validate the implementation against live or approved source-system test data before relying on it.",
            "",
            "## AI / implementation handoff",
            "",
            str(handoff.get("recommended_next_step") or ""),
            "",
            "Keep captured evidence, employee-provided context, and your own implementation judgments explicitly separate in the final design.",
            "",
        ]
    )
    return "\n".join(lines)
