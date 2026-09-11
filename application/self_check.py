"""
Installation self-check.

    SAT-SA --self-check

Answers the question an operator on a locked-down machine actually has:
"is this install working, before I hand it a dataset?" It reads the
bundled configuration, confirms its writable directories, imports every
backend and probes the AI layer — without running an assessment and
without loading a model.

It exists because a packaged application fails differently from a
source checkout. A missing bundled resource or a hidden import that
PyInstaller did not follow shows up as an unhelpful dialog at the worst
moment; here it shows up as a named, failing line.

AI is reported but never required. A machine with no AI runtime passes
this check, because the deterministic assessment is the product.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from application import paths
from application.version import APP_NAME, APP_VERSION


def _check_config() -> tuple[bool, str]:
    path = paths.config_path()
    if not path.is_file():
        return False, f"rule configuration NOT FOUND at {path}"
    try:
        import yaml
        with open(path) as handle:
            cfg = yaml.safe_load(handle) or {}
    except Exception as exc:
        return False, f"rule configuration unreadable: {exc}"

    rules = len(cfg.get("scoring", {}).get("weights", {}) or {})
    return True, f"rule configuration loaded ({rules} scored rule weights)"


def _check_writable() -> tuple[bool, str]:
    root = paths.default_history_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".satsa-write-test"
        probe.write_text("ok")
        probe.unlink()
    except Exception as exc:
        return False, f"assessment history directory NOT writable: {root} ({exc})"
    return True, f"assessment history directory writable: {root}"


def _check_engine() -> tuple[bool, str]:
    try:
        import pandas  # noqa: F401
        from analytics.detection import execution_gaps, negative_space  # noqa: F401
        from analytics.reporting import write_pdf_report  # noqa: F401
    except Exception as exc:
        return False, f"analytics engine failed to load: {exc}"
    return True, "analytics engine and PDF reporting loaded"


def _check_backends() -> tuple[bool, str]:
    """
    Backends are resolved by NAME from configuration, so a packaging
    tool has no reference to follow. If a hidden import were missed,
    this is where it surfaces.
    """
    try:
        from analytics.narration import BACKENDS
        missing = [name for name, cls in BACKENDS.items() if cls is None]
        if missing:
            return False, f"backends not importable: {', '.join(missing)}"
    except Exception as exc:
        return False, f"narration layer failed to load: {exc}"
    return True, f"all explanation backends importable ({', '.join(sorted(BACKENDS))})"


def _check_explanation_prompt() -> tuple[bool, str]:
    """
    Can the explanation layer actually build a prompt?

    `base.build_prompt` imports the prompt builder inside the function,
    so a packaging tool's static analysis does not always follow it.
    Missing it breaks explanations in the frozen build ONLY — the worst
    place to find out. Building a prompt from a synthetic finding costs
    nothing and needs no model, so it is checked here rather than
    discovered during a demonstration.
    """
    try:
        from analytics.narration.base import build_prompt
        from analytics.narration.prompt import validate_explanation

        prompt = build_prompt({
            "finding_type": "SELF_CHECK",
            "rule_id": "SELF-CHECK-001",
            "soc_id": "SOC-SELFCHECK",
            "evidence": {"self_check": True},
        })
    except Exception as exc:
        return False, f"explanation prompt builder failed: {exc}"

    if "SELF_CHECK" not in prompt or "self_check" not in prompt:
        return False, "the prompt builder did not include the supplied finding"
    if not callable(validate_explanation):
        return False, "explanation validation is unavailable"

    return True, f"explanation prompt builder working ({len(prompt):,} characters)"


def _report_ai() -> str:
    """Informational only — never a pass/fail condition."""
    try:
        from application.services.ai_config import AIConfig
        from application.services.narration_service import NarrationService
        status = NarrationService(AIConfig(enabled=True)).status()
        line = f"{status.headline()} — {status.detail}"
        remedy = status.remedy()
        return f"{line}\n      next step: {remedy}" if remedy else line
    except Exception as exc:
        return f"AI layer could not be probed: {exc}"


def run_self_check(stream=None) -> int:
    """Print a report. Returns a process exit code: 0 pass, 1 fail."""
    out = stream or sys.stdout
    lines = []

    def say(text=""):
        lines.append(text)
        print(text, file=out)

    say(f"{APP_NAME} {APP_VERSION} — installation self-check")
    say("=" * 58)
    say(f"  platform     : {platform.system()} {platform.release()} "
        f"({platform.machine()})")
    say(f"  python       : {platform.python_version()}")
    say(f"  packaged     : {'yes' if paths.is_frozen() else 'no (running from source)'}")
    say(f"  resources    : {paths.resource_root()}")
    say()

    checks = [
        ("Rule configuration", _check_config),
        ("Analytics engine", _check_engine),
        ("Explanation backends", _check_backends),
        ("Explanation prompt", _check_explanation_prompt),
        ("Writable storage", _check_writable),
    ]

    failures = 0
    for name, check in checks:
        try:
            passed, detail = check()
        except Exception as exc:            # a check must never crash the report
            passed, detail = False, f"check raised {type(exc).__name__}: {exc}"
        if not passed:
            failures += 1
        say(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    say()
    say("  Local AI (optional — the assessment does not require it):")
    say(f"      {_report_ai()}")
    say()
    say("  Network: none used. SAT-SA makes no external connection.")
    say("=" * 58)

    if failures:
        say(f"  {failures} check(s) FAILED — this install is not ready.")
    else:
        say("  All checks passed. SAT-SA is ready to assess a submission.")

    _write_report(lines)
    return 1 if failures else 0


def _write_report(lines) -> None:
    """
    Also write the report to a file.

    A packaged Windows build is a windowed application with no console,
    so running it with --self-check from a command prompt prints
    nowhere. The file is the only copy the operator can actually read —
    and the one they can attach to an accreditation record.
    """
    try:
        target = paths.user_data_root() / "self-check.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n  Report written to: {target}")
    except Exception:
        pass  # the console copy above is still a complete report
