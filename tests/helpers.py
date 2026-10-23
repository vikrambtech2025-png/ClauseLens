"""Shared fixtures and small factories for the ClauseLens test suite."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.analysis import AnalysisPipeline
from models.schemas import AnalysisResult, FindingStatus, PolicyConfig

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def sample_path(name: str) -> Path:
    path = SAMPLES / name
    assert path.exists(), f"Missing synthetic sample: {path}"
    return path


def run_sample(
    pipeline: AnalysisPipeline,
    name: str,
    policy: PolicyConfig | None = None,
    use_llm: bool = False,
    llm_client=None,
) -> AnalysisResult:
    path = sample_path(name)
    return pipeline.analyze_file(
        str(path),
        path.name,
        policy or PolicyConfig(),
        use_llm=use_llm,
        llm_client=llm_client,
    )


def run_text(
    pipeline: AnalysisPipeline,
    text: str,
    policy: PolicyConfig | None = None,
) -> AnalysisResult:
    return pipeline.analyze_text(text.strip(), "test-contract.txt", policy or PolicyConfig())


def rule(result: AnalysisResult, rule_id: str):
    matches = [finding for finding in result.findings if finding.rule_id == rule_id]
    assert matches, f"Expected rule {rule_id}; got {[f.rule_id for f in result.findings]}"
    return matches[0]


def rules(result: AnalysisResult) -> list[str]:
    return [finding.rule_id or "" for finding in result.findings]


def status_map(result: AnalysisResult) -> dict[str, str]:
    return {
        finding.rule_id or finding.finding_id: finding.status.value
        for finding in result.findings
    }


class FakeCompletions:
    """Minimal stand-in for ``client.chat.completions``."""

    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self.content or "")),
            ]
        )


class FakeOpenAIClient:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.completions = FakeCompletions(content=content, error=error)
        self.chat = SimpleNamespace(completions=self.completions)


def ai_payload(*proposals: dict) -> str:
    import json

    return json.dumps({"findings": list(proposals)})


def evidence_statuses(result: AnalysisResult) -> dict[str, str]:
    return {
        finding.rule_id or finding.finding_id: finding.verification_status.value
        for finding in result.findings
        if finding.has_evidence
    }


CONFIRMED_STATUSES = {
    FindingStatus.HIGH,
    FindingStatus.MEDIUM,
    FindingStatus.LOW,
}
