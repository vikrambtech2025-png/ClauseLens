"""Fine-tuned local analyzer.

Uses a GGUF model served by llama-cpp-python. The prompt, context shape and
output schema are shared with training via ``finetune.prompt`` so the format
cannot drift. Every model-produced finding is passed through the existing
``EvidenceVerifier`` (the gate stays in place) — the model decides, the
verifier checks the quote.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from config.settings import get_settings
from core.clause_splitter import ClauseSplitter
from core.document_loader import DocumentLoader
from core.evidence_verifier import EvidenceVerifier
from core.policy_engine import STATUS_TO_RISK
from core.prompt_safety import find_injection_hits, injection_warning
from core.retrieval import ClauseRetriever
from core.risk_classifier import RiskClassifier
from finetune.prompt import (
    CATEGORY_ORDER,
    MAX_PASSAGES_PER_CATEGORY,
    build_context,
    build_messages,
    parse_model_output,
)
from models.schemas import (
    AnalysisResult,
    ClauseCategory,
    ClauseSegment,
    Document,
    Finding,
    FindingStatus,
    PolicyConfig,
    RiskLevel,
    VerificationStatus,
)


def load_document(path, filename=None):
    return DocumentLoader().load(path, filename=filename)


def llm_backend_available() -> bool:
    try:
        import llama_cpp  # noqa: F401
    except Exception:
        return False
    try:
        return bool(DEFAULT_MODEL_PATH.exists())
    except Exception:
        return False


DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "finetuned" / "clause-review-qwen2.5-3b-q4_k_m.gguf"
SYSTEM_FALLBACK = Path(__file__).resolve().parents[1] / "models" / "finetuned" / "SYSTEM_PROMPT.txt"


def _load_system_prompt() -> str:
    if SYSTEM_FALLBACK.exists():
        return SYSTEM_FALLBACK.read_text(encoding="utf-8")
    from finetune.prompt import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def _clause_category(value: str) -> ClauseCategory:
    return ClauseCategory(value)


def _to_finding(decision, segment, policy: PolicyConfig, source_name: str) -> Finding:
    category = _clause_category(decision.category)
    status = FindingStatus(decision.status) if decision.status != "NOT_FOUND" else FindingStatus.NOT_FOUND

    fact = None
    if decision.payment is not None:
        fact = {
            "type": "payment",
            "days": decision.payment.days,
            "basis": decision.payment.basis,
            "condition": decision.payment.condition,
        }
    elif decision.renewal is not None:
        fact = {
            "type": "renewal",
            "auto_renewal": decision.renewal.auto_renewal,
            "notice_days": decision.renewal.notice_days,
        }
    elif decision.liability is not None:
        fact = {
            "type": "liability",
            "cap": decision.liability.cap,
            "indemnity": decision.liability.indemnity,
        }
    elif decision.jurisdiction is not None:
        fact = {
            "type": "jurisdiction",
            "governing_law": decision.jurisdiction.governing_law,
            "exclusive": decision.jurisdiction.exclusive,
        }

    raw_evidence = {
        "rule": f"finetuned.{decision.category}",
        "category": category.value,
        "model": "qwen2.5-3b-instruct-finetuned",
        "fact": fact,
    }
    if decision.source_id:
        raw_evidence["source_id"] = decision.source_id

    return Finding(
        finding_id=f"ft-{category.value}",
        clause_category=category,
        risk_level=STATUS_TO_RISK.get(status, RiskLevel.LOW),
        status=status,
        exact_quote=decision.exact_quote or "",
        source_id=segment.source_id,
        page_number=segment.page_number,
        paragraph_index=segment.paragraph_index,
        span_start=segment.span_start,
        span_end=segment.span_end,
        policy_violated=None,
        explanation=decision.reason or "Model assessment based on retrieved clause and policy.",
        recommended_action="Review the quoted passage and negotiate if it conflicts with policy.",
        verification_status=VerificationStatus.UNVERIFIED,
        confidence=None,
        rule_id=f"finetuned.{decision.category}",
        raw_evidence=raw_evidence,
    )


class FineTunedAnalyzer:
    def __init__(self, model_path: Optional[Path] = None, settings=None, llm=None):
        self.settings = settings or get_settings()
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self.retriever = ClauseRetriever()
        self.segmenter = ClauseSplitter()
        self.verifier = EvidenceVerifier()
        self.classifier = RiskClassifier()
        self._llm = llm

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Fine-tuned GGUF not found at {self.model_path}. "
                "Place the trained model as models/finetuned/clause-review-qwen2.5-3b-q4_k_m.gguf "
                "or pass model_path to FineTunedAnalyzer."
            )
        try:
            from llama_cpp import Llama
        except Exception as exc:
            raise RuntimeError(
                "llama-cpp-python is required for local inference. "
                "Install it in your environment (e.g. pip install llama-cpp-python)."
            ) from exc

        self._llm = Llama(
            model_path=str(self.model_path),
            n_ctx=2048,
            n_gpu_layers=-1,
            n_threads=4,
            verbose=False,
        )
        return self._llm

    def _chat(self, messages: list[dict[str, str]]) -> str:
        llm = self._get_llm()
        resp = llm.create_chat_completion(
            messages=messages,
            temperature=0.0,
            top_p=1.0,
            max_tokens=512,
            stop=["</s>"],
        )
        try:
            return resp["choices"][0]["message"]["content"] or ""
        except Exception:
            return json.dumps(resp)

    def analyze_text(
        self,
        text: str,
        filename: str = "contract.txt",
        policy: Optional[PolicyConfig] = None,
    ) -> AnalysisResult:
        policy = policy or PolicyConfig()
        document = DocumentLoader().load_text(text, filename)
        return self.analyze_document(document, policy)

    def analyze_document(
        self,
        document: Document,
        policy: Optional[PolicyConfig] = None,
    ) -> AnalysisResult:
        policy = policy or PolicyConfig()
        started = time.perf_counter()
        segments = self.segmenter.split(document)
        injection_hits = find_injection_hits(segments)
        quarantined = {hit.clause_id for hit in injection_hits}
        analyzed_segments = [s for s in segments if s.clause_id not in quarantined]

        warnings = list(document.stats.warnings)
        if injection_hits:
            warnings.append(injection_warning(injection_hits))

        retrieval = self.retriever.retrieve(analyzed_segments)
        context = build_context(retrieval, policy, max_passages=MAX_PASSAGES_PER_CATEGORY)
        messages = build_messages(context, target=None)
        messages[0]["content"] = _load_system_prompt()

        raw = self._chat(messages)
        batch, err = parse_model_output(raw)
        findings: list[Finding] = []

        if batch is not None:
            for decision in batch.decisions:
                seg = None
                if decision.exact_quote:
                    for category in context.categories:
                        if category.category != decision.category:
                            continue
                        for passage in category.passages:
                            if decision.exact_quote in passage.text:
                                seg = ClauseSegment(
                                    clause_id=passage.clause_id,
                                    text=passage.text,
                                    source_id=passage.source_id,
                                    span_start=0,
                                    span_end=len(passage.text),
                                    page_number=passage.page_number,
                                )
                                break
                        if seg:
                            break
                if seg is None:
                    for category in context.categories:
                        if category.category != decision.category or not category.passages:
                            continue
                        p = category.passages[0]
                        seg = ClauseSegment(
                            clause_id=p.clause_id,
                            text=p.text,
                            source_id=p.source_id,
                            span_start=0,
                            span_end=len(p.text),
                            page_number=p.page_number,
                        )
                        break
                if seg is None:
                    continue
                findings.append(_to_finding(decision, seg, policy, document.filename))

        verified = self.verifier.verify(findings, document, policy)
        verified_list = list(verified)

        seen = {f.clause_category for f in verified_list if f.status != FindingStatus.NOT_FOUND}
        for category in CATEGORY_ORDER:
            cat = _clause_category(category)
            if cat in seen:
                continue
            result_cat = retrieval.get(category)
            passages = getattr(result_cat, "passages", None) or []
            if not passages:
                verified_list.append(
                    Finding(
                        finding_id=f"ft-cover-{category}",
                        clause_category=cat,
                        risk_level=STATUS_TO_RISK[FindingStatus.NOT_FOUND],
                        status=FindingStatus.NOT_FOUND,
                        exact_quote="",
                        source_id="",
                        page_number=None,
                        explanation="No relevant clause was retrieved for this category.",
                        recommended_action="Check the contract manually for this category.",
                        verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                        rule_id=f"finetuned.{category}.coverage",
                        raw_evidence={"rule": f"finetuned.{category}.coverage", "coverage": True},
                    )
                )

        summary = self.classifier.classify(verified_list, retrieval, document)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return AnalysisResult(
            document=document,
            findings=verified_list,
            retrieval=retrieval,
            policy=policy,
            summary=summary,
            warnings=self._unique(warnings),
            processing_ms=elapsed_ms,
            pipeline_steps=[],
            limitations=[],
        )

    def analyze_file(
        self,
        file_path: str,
        source_name: Optional[str] = None,
        policy: Optional[PolicyConfig] = None,
    ) -> AnalysisResult:
        loaded = load_document(file_path)
        name = source_name or os.path.basename(file_path)
        return self.analyze_text(loaded.raw_text, filename=name, policy=policy)

    def analyze_document(
        self,
        document: Document,
        policy: Optional[PolicyConfig] = None,
    ) -> AnalysisResult:
        return self.analyze_text(document.raw_text, filename=document.filename, policy=policy)

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        result = []
        seen = set()
        for value in values:
            cleaned = " ".join(str(value).split())
            if cleaned and cleaned not in seen:
                result.append(cleaned)
                seen.add(cleaned)
        return result
