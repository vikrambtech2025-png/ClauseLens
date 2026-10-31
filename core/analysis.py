"""End-to-end analysis orchestration.

Pipeline:
upload -> extraction -> segmentation -> lexical/BM25 retrieval -> deterministic
policy rules -> optional LLM ambiguity proposals -> independent evidence
verification -> summary. Coverage findings are added for every category so
absence and unverified coverage are never silently ignored.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from config.settings import Settings, get_settings
from core.clause_splitter import ClauseSplitter
from core.document_loader import DocumentLoader
from core.evidence_verifier import EvidenceVerifier
from core.llm_analyzer import LLMAnalyzer
from core.policy_engine import STATUS_TO_RISK, PolicyEngine
from core.prompt_safety import find_injection_hits, injection_warning
from core.retrieval import ClauseRetriever
from core.risk_classifier import RiskClassifier
from core.text_utils import normalize_whitespace
from core.fake_data_detector import FakeDataDetector
from models.schemas import (
    AnalysisResult,
    CategoryRetrieval,
    ClauseCategory,
    Document,
    Finding,
    FindingStatus,
    LLMStatus,
    PolicyConfig,
    RetrievedPassage,
    VerificationStatus,
)

_STATUS_ORDER = {
    FindingStatus.HIGH: 0,
    FindingStatus.MEDIUM: 1,
    FindingStatus.LOW: 2,
    FindingStatus.REVIEW_REQUIRED: 3,
    FindingStatus.NO_CONFLICT_FOUND: 4,
    FindingStatus.NOT_FOUND: 5,
    FindingStatus.NOT_VERIFIED: 6,
}
_CATEGORY_ORDER = {category: index for index, category in enumerate(ClauseCategory)}


class AnalysisPipeline:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        loader: Optional[DocumentLoader] = None,
        splitter: Optional[ClauseSplitter] = None,
        retriever: Optional[ClauseRetriever] = None,
        engine: Optional[PolicyEngine] = None,
        verifier: Optional[EvidenceVerifier] = None,
        classifier: Optional[RiskClassifier] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.loader = loader or DocumentLoader(
            max_file_size_mb=self.settings.max_upload_mb,
            max_pdf_pages=self.settings.max_pdf_pages,
        )
        self.splitter = splitter or ClauseSplitter()
        self.retriever = retriever or ClauseRetriever(
            top_k=self.settings.retrieved_passages_per_category
        )
        self.engine = engine or PolicyEngine()
        self.verifier = verifier or EvidenceVerifier()
        self.classifier = classifier or RiskClassifier()

    # ------------------------------------------------------------------ files
    def analyze_file(
        self,
        file_path: str,
        filename: Optional[str] = None,
        policy: Optional[PolicyConfig] = None,
        use_llm: bool = False,
        llm_client: Any = None,
    ) -> AnalysisResult:
        document = self.loader.load(file_path, filename)
        return self.analyze_document(
            document,
            policy or PolicyConfig(),
            use_llm=use_llm,
            llm_client=llm_client,
        )

    def analyze_text(
        self,
        text: str,
        filename: str = "contract.txt",
        policy: Optional[PolicyConfig] = None,
        use_llm: bool = False,
        llm_client: Any = None,
    ) -> AnalysisResult:
        document = self.loader.load_text(text, filename)
        return self.analyze_document(
            document,
            policy or PolicyConfig(),
            use_llm=use_llm,
            llm_client=llm_client,
        )

    # -------------------------------------------------------------- document
    def analyze_document(
        self,
        document: Document,
        policy: PolicyConfig,
        use_llm: bool = False,
        llm_client: Any = None,
    ) -> AnalysisResult:
        started = time.perf_counter()
        steps: list[dict[str, Any]] = []
        warnings: list[str] = list(document.stats.warnings)

        # Fake / mocked / falsified document detection (heuristic, local).
        fake_report = FakeDataDetector().detect(document)
        if fake_report.is_fake:
            warnings.append(
                "FAKE DATA: The uploaded document appears to be fake, mocked, or "
                "falsified. " + " ".join(fake_report.reasons)
            )

        step_start = time.perf_counter()
        segments = self.splitter.split(document)
        steps.append(
            {
                "step": "clause_segmentation",
                "duration_ms": round((time.perf_counter() - step_start) * 1000, 1),
                "segments": len(segments),
            }
        )

        # Instructions embedded in a contract are data, not terms. Quarantine
        # those segments from policy matching so an injected sentence cannot
        # create or suppress a finding.
        injection_hits = find_injection_hits(segments)
        quarantined = {hit.clause_id for hit in injection_hits}
        analyzed_segments = [s for s in segments if s.clause_id not in quarantined]
        if injection_hits:
            warnings.append(injection_warning(injection_hits))
            steps.append(
                {
                    "step": "prompt_injection_quarantine",
                    "duration_ms": 0.0,
                    "quarantined_segments": len(quarantined),
                }
            )

        step_start = time.perf_counter()
        retrieval = self.retriever.retrieve(analyzed_segments)
        steps.append(
            {
                "step": "lexical_retrieval",
                "duration_ms": round((time.perf_counter() - step_start) * 1000, 1),
                "candidates": sum(len(result.passages) for result in retrieval.values()),
            }
        )

        step_start = time.perf_counter()
        findings = self.engine.evaluate(retrieval, policy, document)
        steps.append(
            {
                "step": "deterministic_policy_rules",
                "duration_ms": round((time.perf_counter() - step_start) * 1000, 1),
                "findings": len(findings),
            }
        )

        llm_status = LLMStatus(requested=use_llm, provider=self.settings.llm_provider)
        analysis_mode = "deterministic"
        if use_llm:
            step_start = time.perf_counter()
            analyzer = LLMAnalyzer(
                settings=self.settings,
                client=llm_client,
                requested=True,
            )
            outcome = analyzer.analyze(policy, retrieval, document, findings)
            llm_status = LLMStatus(
                requested=True,
                available=outcome.available,
                used=outcome.used,
                provider=outcome.provider,
                model=outcome.model,
                error=outcome.error,
            )
            if outcome.used:
                deterministic_keys = {
                    (
                        finding.clause_category.value,
                        finding.source_id,
                        normalize_whitespace(finding.exact_quote).casefold()[:220],
                    )
                    for finding in findings
                }
                discarded = 0
                for ai_finding in outcome.findings:
                    key = (
                        ai_finding.clause_category.value,
                        ai_finding.source_id,
                        normalize_whitespace(ai_finding.exact_quote).casefold()[:220],
                    )
                    if key in deterministic_keys:
                        discarded += 1
                        continue
                    findings.append(ai_finding)
                    deterministic_keys.add(key)
                if discarded:
                    llm_status.error = (
                        f"{discarded} duplicate AI proposal(s) were discarded in favor of "
                        "deterministic findings."
                    )
                analysis_mode = "deterministic + AI interpretation"
                warnings.extend(outcome.warnings)
            else:
                analysis_mode = "deterministic (AI requested; unavailable)"
                warnings.append(
                    "AI interpretation was unavailable or rejected; deterministic analysis "
                    f"completed instead. {outcome.error or 'No AI findings returned.'}"
                )
            steps.append(
                {
                    "step": "ai_interpretation",
                    "duration_ms": round((time.perf_counter() - step_start) * 1000, 1),
                    "used": outcome.used,
                    "proposals": len(outcome.findings),
                    "error": outcome.error,
                }
            )
        else:
            llm_status = LLMStatus(
                requested=False,
                available=self.settings.llm_configured,
                used=False,
                provider=self.settings.llm_provider,
                model=self.settings.llm_model,
                error=None,
            )

        step_start = time.perf_counter()
        findings = self._ensure_category_coverage(
            findings, retrieval, document, injection_present=bool(injection_hits)
        )
        steps.append(
            {
                "step": "coverage_findings",
                "duration_ms": round((time.perf_counter() - step_start) * 1000, 1),
                "findings": len(findings),
            }
        )

        step_start = time.perf_counter()
        findings = self.verifier.verify(findings, document, policy)
        steps.append(
            {
                "step": "evidence_verification",
                "duration_ms": round((time.perf_counter() - step_start) * 1000, 1),
                "verified": sum(
                    1
                    for finding in findings
                    if finding.verification_status == VerificationStatus.VERIFIED
                ),
                "failed": sum(
                    1
                    for finding in findings
                    if finding.verification_status
                    in {
                        VerificationStatus.FAILED_QUOTE_MATCH,
                        VerificationStatus.FAILED_LOCATION,
                        VerificationStatus.FAILED_POLICY_SUPPORT,
                    }
                ),
            }
        )

        findings = self._assign_ids(findings)
        summary = self.classifier.classify(findings, retrieval, document)
        limitations = list(AnalysisResult.model_fields["limitations"].default_factory())
        if document.stats.needs_ocr:
            limitations.append(
                "Some pages had little extractable text. OCR is not included; those pages "
                "could not be verified."
            )
        if document.stats.truncated:
            limitations.append(
                "Only part of the PDF was processed; categories without evidence must be "
                "treated as NOT_VERIFIED, not absent."
            )
        if injection_hits:
            limitations.append(
                "Passages containing prompt-injection-like instructions were quarantined "
                "from rule matching and require human review."
            )

        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        steps.append({"step": "summary", "duration_ms": 0.0, "findings": len(findings)})
        return AnalysisResult(
            document=document,
            policy=policy,
            retrieval=retrieval,
            findings=findings,
            summary=summary,
            analysis_mode=analysis_mode,
            warnings=self._unique(warnings),
            fake_data_detected=fake_report.is_fake,
            fake_data_reasons=fake_report.reasons,
            llm_status=llm_status,
            processing_ms=elapsed_ms,
            pipeline_steps=steps,
            limitations=limitations,
        )

    # ------------------------------------------------------------- coverage
    def _ensure_category_coverage(
        self,
        findings: list[Finding],
        retrieval: dict[str, CategoryRetrieval],
        document: Document,
        injection_present: bool = False,
    ) -> list[Finding]:
        result = list(findings)
        coverage_incomplete = (
            not document.stats.extraction_ok
            or document.stats.needs_ocr
            or document.stats.truncated
            or not document.spans
            or injection_present
        )
        for category in ClauseCategory:
            if any(finding.clause_category == category for finding in result):
                continue
            category_result = retrieval.get(category.value)
            if category_result and category_result.passages:
                result.append(
                    self._review_candidate_finding(category, category_result.passages[0])
                )
            elif coverage_incomplete:
                result.append(
                    self._no_evidence_finding(
                        category,
                        FindingStatus.NOT_VERIFIED,
                        "The document could not be fully processed, so this category cannot "
                        "be treated as absent. Review the extraction warnings and OCR needs.",
                    )
                )
            else:
                result.append(
                    self._no_evidence_finding(
                        category,
                        FindingStatus.NOT_FOUND,
                        "No matching provision was identified by lexical retrieval. This is "
                        "not proof that the clause is absent; confirm manually in schedules, "
                        "annexes, and non-standard drafting.",
                    )
                )
        return result

    def _review_candidate_finding(
        self, category: ClauseCategory, passage: RetrievedPassage
    ) -> Finding:
        segment = passage.segment
        return Finding(
            finding_id="PENDING",
            clause_category=category,
            risk_level=STATUS_TO_RISK[FindingStatus.REVIEW_REQUIRED],
            status=FindingStatus.REVIEW_REQUIRED,
            exact_quote=segment.text,
            source_id=segment.source_id,
            page_number=segment.page_number,
            paragraph_index=segment.paragraph_index,
            span_start=segment.span_start,
            span_end=segment.span_end,
            policy_violated="A candidate clause was retrieved, but deterministic rules could "
            "not classify it confidently.",
            explanation=(
                "This provision matched the category's legal vocabulary, but no rule could "
                "safely extract a payment period, renewal trigger, liability cap, or forum. "
                "It is surfaced for human review rather than marked compliant."
            ),
            recommended_action=(
                "Read the full clause and confirm the commercial term in writing. Add the "
                "missing trigger, period, cap, or forum so it can be checked deterministically."
            ),
            verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
            confidence=None,
            evidence_quality="pending_verification",
            uncertainty_reason=(
                "Category coverage review: the clause was retrieved but not confidently "
                "classified by deterministic rules."
            ),
            rule_id="coverage.review_candidate",
            raw_evidence={
                "rule": "coverage.review_candidate",
                "clause_id": segment.clause_id,
                "retrieval_score": passage.score,
                "matched_terms": passage.matched_terms,
            },
        )

    def _no_evidence_finding(
        self,
        category: ClauseCategory,
        status: FindingStatus,
        explanation: str,
    ) -> Finding:
        return Finding(
            finding_id="PENDING",
            clause_category=category,
            risk_level=STATUS_TO_RISK[status],
            status=status,
            exact_quote="",
            source_id="",
            page_number=None,
            paragraph_index=None,
            policy_violated=None,
            explanation=explanation,
            recommended_action=(
                "Manually confirm this category in the full contract and its annexes. If the "
                "document is scanned or truncated, obtain a text-based copy before relying on "
                "the result."
            ),
            verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
            confidence=None,
            evidence_quality="C · no verified evidence",
            uncertainty_reason=(
                "No verified quotation supports a conclusion for this category."
            ),
            rule_id="coverage.not_verified"
            if status == FindingStatus.NOT_VERIFIED
            else "coverage.not_found",
            raw_evidence={"rule": status.value, "category": category.value},
        )

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _assign_ids(findings: list[Finding]) -> list[Finding]:
        ordered = sorted(
            findings,
            key=lambda finding: (
                _CATEGORY_ORDER[finding.clause_category],
                _STATUS_ORDER[finding.status],
                finding.source_id,
                finding.span_start if finding.span_start is not None else 10**9,
            ),
        )
        for index, finding in enumerate(ordered, start=1):
            finding.finding_id = f"F{index:04d}"
        return ordered

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = normalize_whitespace(str(value))
            if cleaned and cleaned not in seen:
                result.append(cleaned)
                seen.add(cleaned)
        return result
