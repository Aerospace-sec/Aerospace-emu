#!/usr/bin/env python3
"""Cross-check the 5G-ATG report, model, tests and archived evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "report" / "5g_atg_traceability.json"
REPORT = ROOT / "report" / "5g_atg_security_report.md"
MODEL = ROOT / "sim" / "atg5g_lab.py"
TESTS = ROOT / "tests" / "test_atg5g_lab.py"
RUNNER = ROOT / "scripts" / "run_atg5g_experiments.py"
VERIFIER = ROOT / "scripts" / "verify_atg5g_evidence.py"
RESULTS = ROOT / "artifacts" / "5g_atg_results.json"
EXPERIMENT_LOG = ROOT / "artifacts" / "5g_atg_experiment_log.md"
VERIFICATION_LOG = ROOT / "artifacts" / "5g_atg_verification.log"
PROVENANCE = ROOT / "artifacts" / "5g_atg_provenance.json"
HTML = ROOT / "artifacts" / "5g_atg_security_report.html"
sys.path.insert(0, str(ROOT))

from sim.atg5g_lab import run_atg_scenarios  # noqa: E402


class VerificationFailure(RuntimeError):
    """Raised when an evidence-chain check fails."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "lines": content.count(b"\n"),
    }


def resolve_json_path(document: Any, path: str) -> Any:
    current = document
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise VerificationFailure(f"JSON path does not resolve: {path}")
    return current


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def anchor_present(text: str, anchor: str) -> bool:
    if anchor in text:
        return True
    if "." in anchor:
        owner, member = anchor.rsplit(".", 1)
        class_pattern = rf"\bclass\s+{re.escape(owner)}\b"
        member_pattern = rf"\bdef\s+{re.escape(member)}\b"
        return bool(re.search(class_pattern, text)) and bool(re.search(member_pattern, text))
    return False


def check_anchor(item: dict[str, Any], artifact_document: dict[str, Any]) -> None:
    path = ROOT / item["path"]
    require(path.exists(), f"missing evidence path: {item['path']}")
    if item.get("json_path"):
        require(path.suffix == ".json", f"JSON path attached to non-JSON evidence: {item['path']}")
        document = json.loads(path.read_text(encoding="utf-8"))
        resolve_json_path(document, item["json_path"])
    anchor = item.get("anchor")
    if not anchor:
        return
    if item["kind"] == "artifact" and item["path"].endswith(".json") and anchor.startswith("results."):
        resolve_json_path(artifact_document, anchor)
        return
    text = path.read_text(encoding="utf-8")
    require(anchor_present(text, anchor), f"anchor not found: {item['path']}::{anchor}")


def traceability_paths_resolve(matrix: dict[str, Any], artifact_document: dict[str, Any]) -> dict[str, int]:
    source_ids = {item["id"] for item in matrix["source_registry"]}
    local_ids = {item["id"] for item in matrix["local_evidence_registry"]}
    require(len(source_ids) == len(matrix["source_registry"]), "duplicate external source ID")
    require(len(local_ids) == len(matrix["local_evidence_registry"]), "duplicate local source ID")
    require(set(matrix["report_reference_mapping"].values()) == source_ids, "report reference mapping is incomplete")
    report_text = REPORT.read_text(encoding="utf-8")
    cited_numbers = {f"[{number}]" for number in re.findall(r"\[(\d+)\]", report_text)}
    require(cited_numbers <= set(matrix["report_reference_mapping"]), "report contains an unregistered reference number")

    for item in matrix["local_evidence_registry"]:
        path = ROOT / item["path"]
        require(path.exists(), f"missing local evidence file: {item['path']}")
        if path.suffix == ".json":
            document = json.loads(path.read_text(encoding="utf-8"))
            for anchor in item.get("anchors", []):
                if anchor.startswith("results."):
                    resolve_json_path(document, anchor)
                else:
                    require(anchor in path.read_text(encoding="utf-8"), f"local JSON anchor not found: {item['path']}::{anchor}")
        else:
            text = path.read_text(encoding="utf-8")
            for anchor in item.get("anchors", []):
                require(anchor_present(text, anchor), f"local anchor not found: {item['path']}::{anchor}")

    counts = {"normative": 0, "quantitative": 0, "scenario": 0}
    all_claim_ids: set[str] = set()
    for group_name, key in (("normative", "normative_and_architecture_claims"), ("quantitative", "quantitative_claims"), ("scenario", "scenario_conclusions")):
        for claim in matrix[key]:
            claim_id = claim["id"]
            require(claim_id not in all_claim_ids, f"duplicate claim ID: {claim_id}")
            all_claim_ids.add(claim_id)
            for source_id in claim.get("sources", []):
                require(source_id in source_ids, f"unknown external source {source_id} in {claim_id}")
            evidence_key = "local_evidence" if key == "normative_and_architecture_claims" else "evidence"
            for evidence in claim.get(evidence_key, []):
                check_anchor(evidence, artifact_document)
            for assertion in claim.get("assertions", []):
                actual = resolve_json_path(artifact_document, assertion["path"])
                if "equals" in assertion:
                    require(actual == assertion["equals"], f"assertion failed for {claim_id}: {assertion['path']}")
                if "contains" in assertion:
                    require(assertion["contains"] in actual, f"assertion failed for {claim_id}: {assertion['path']}")
                if "regex" in assertion:
                    require(re.search(assertion["regex"], str(actual)) is not None, f"assertion failed for {claim_id}: {assertion['path']}")
            if key == "quantitative_claims":
                test_path = claim["test"].split("::", 1)[0]
                require((ROOT / test_path).exists(), f"quantitative test file missing: {test_path}")
            counts[group_name] += 1

    required_scenario_ids = {f"C-AD-{number:02d}" for number in range(1, 5)} | {f"C-F-{number:02d}" for number in range(1, 13)}
    actual_scenario_ids = {claim["id"] for claim in matrix["scenario_conclusions"]}
    require(required_scenario_ids <= actual_scenario_ids, "not all AD/F report conclusions are mapped")
    require("5g_atg_traceability_matrix.md" in report_text, "report does not link the traceability matrix")
    return counts


def fresh_model_results_match_artifact(artifact_document: dict[str, Any]) -> None:
    fresh = run_atg_scenarios()
    require(fresh == artifact_document["results"], "fresh model results differ from archived results")


def required_regression_command_passes() -> tuple[str, str, int]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_atg5g_lab.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout, completed.stderr, completed.returncode


def full_regression_command_passes() -> tuple[str, str, int]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout, completed.stderr, completed.returncode


def verify_atg5g_evidence() -> dict[str, Any]:
    require(MATRIX.exists(), "traceability matrix missing")
    require(RESULTS.exists(), "experiment results missing; run the experiment runner first")
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    artifact_document = json.loads(RESULTS.read_text(encoding="utf-8"))
    require(artifact_document["environment"]["traceability_matrix"] == "report/5g_atg_traceability.json", "artifact points to an unexpected matrix")
    recorded_verification = artifact_document["environment"]["verification"]
    require(recorded_verification["focused"]["returncode"] == 0, "runner recorded a failed focused test")
    require(recorded_verification["full"]["returncode"] == 0, "runner recorded a failed full regression")
    counts = traceability_paths_resolve(matrix, artifact_document)
    fresh_model_results_match_artifact(artifact_document)
    stdout, stderr, returncode = required_regression_command_passes()
    require(returncode == 0, f"focused regression failed: {stdout}{stderr}")
    full_stdout, full_stderr, full_returncode = full_regression_command_passes()
    require(full_returncode == 0, f"full regression failed: {full_stdout}{full_stderr}")

    required_artifacts = [
        RESULTS,
        EXPERIMENT_LOG,
        REPORT,
        MODEL,
        TESTS,
        RUNNER,
        VERIFIER,
        MATRIX,
        ROOT / "report" / "5g_atg_traceability_matrix.md",
        ROOT / "scripts" / "render_atg5g_traceability.py",
        ROOT / "scripts" / "render_report.py",
    ]
    if HTML.exists():
        required_artifacts.append(HTML)
    manifest = {str(path.relative_to(ROOT)): file_record(path) for path in required_artifacts}
    checks = {
        "traceability_paths_resolve": True,
        "fresh_model_results_match_artifact": True,
        "required_regression_command_passes": True,
        "full_regression_command_passes": True,
        "report_reference_mapping_complete": True,
        "hash_manifest_created": True,
    }
    return {
        "schema_version": "1.0",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "commands": {
            "experiment": "python3 scripts/run_atg5g_experiments.py",
            "focused_tests": "python3 -m pytest -q tests/test_atg5g_lab.py",
            "verification": "python3 scripts/verify_atg5g_evidence.py",
            "render": "python3 scripts/render_report.py",
        },
        "matrix_counts": counts,
        "checks": checks,
        "focused_test_stdout": stdout.strip(),
        "focused_test_stderr": stderr.strip(),
        "full_test_stdout": full_stdout.strip(),
        "full_test_stderr": full_stderr.strip(),
        "runner_recorded_verification": recorded_verification,
        "file_manifest": manifest,
        "self_hash_exclusion": "The provenance JSON and verification log are excluded from the hash manifest because they contain the manifest itself.",
    }


def main() -> None:
    try:
        result = verify_atg5g_evidence()
    except (OSError, json.JSONDecodeError, VerificationFailure) as exc:
        failure = {
            "verified_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error": str(exc),
        }
        PROVENANCE.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        VERIFICATION_LOG.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise SystemExit(f"5G-ATG evidence verification failed: {exc}") from exc

    PROVENANCE.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    VERIFICATION_LOG.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
