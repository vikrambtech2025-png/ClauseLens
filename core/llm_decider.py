"""RAG -> LLM Decider

Flow:
1. Load document
2. Segment -> Retrieve (reuse existing ClauseRetriever)
3. Take ONLY retrieved passages + policy (build_context from finetune.prompt)
4. Send to local GGUF via llama-cpp-python with strict JSON
5. Parse to decisions
6. Map to Findings
7. Gate through EvidenceVerifier (must keep this)
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
    SYSTEM_PROMPT,
    build_context,
    build_messages,
    parse_model_output,
)
from models.schemas import (
    AnalysisResult,
    ClauseCategory,
    ClauseSegment,
    Document,
    DocumentStats,
    Finding,
    FindingStatus,
    PolicyConfig,
    RiskLevel,
    VerificationStatus,
)


def load_document(path, filename=None):
    return DocumentLoader().load(path, filename=filename)


def llm_backend_available() -> bool:
    """Return True if llama-cpp-python and a model file are available."""
    try:
        import llama_cpp  # noqa: F401
    except Exception:
        return False
    try:
        return bool(DEFAULT_MODEL_PATH.exists())
    except Exception:
        return False


_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODEL_CANDIDATES = [
    _REPO_ROOT / "models" / "llm_decider" / "Qwen2.5-1.5B-Instruct-IQ2_XS.gguf",
    _REPO_ROOT / "models" / "llm_decider" / "Qwen2.5-1.5B-Instruct.IQ4_XS.gguf",
    _REPO_ROOT / "Qwen2.5-1.5B-Instruct.IQ4_XS.gguf",
]


def _default_model_path() -> Path:
    for candidate in _MODEL_CANDIDATES:
        if candidate.exists():
            return candidate
    return _MODEL_CANDIDATES[0]


DEFAULT_MODEL_PATH = _default_model_path()


def _clause_category(value: str) -> ClauseCategory:
    return ClauseCategory(value)


def _to_finding(decision, segment: ClauseSegment, policy: PolicyConfig, source_name: str) -> Finding:
    category = _clause_category(decision.category)
    status = FindingStatus(decision.status) if decision.status != "NOT_FOUND" else FindingStatus.NOT_FOUND

    fact = None
    if getattr(decision, "payment", None) is not None:
        fact = {
            "type": "payment",
            "days": decision.payment.days,
            "basis": decision.payment.basis,
            "condition": decision.payment.condition,
        }
    elif getattr(decision, "renewal", None) is not None:
        fact = {
            "type": "renewal",
            "auto_renewal": decision.renewal.auto_renewal,
            "notice_days": decision.renewal.notice_days,
        }
    elif getattr(decision, "liability", None) is not None:
        fact = {
            "type": "liability",
            "cap": decision.liability.cap,
            "indemnity": decision.liability.indemnity,
        }
    elif getattr(decision, "jurisdiction", None) is not None:
        fact = {
            "type": "jurisdiction",
            "governing_law": decision.jurisdiction.governing_law,
            "exclusive": decision.jurisdiction.exclusive,
        }

    raw_evidence = {
        "rule": f"llm_decider.{decision.category}",
        "category": category.value,
        "model": "qwen2.5-1.5b-instruct-local",
        "fact": fact,
        "source": "rag->llm",
    }
    if decision.source_id:
        raw_evidence["source_id"] = decision.source_id

    return Finding(
        finding_id=f"ld-{category.value}",
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
        explanation=decision.reason or "LLM decision based strictly on retrieved passages and policy.",
        recommended_action="Review the quoted passage and negotiate if it conflicts with policy.",
        verification_status=VerificationStatus.UNVERIFIED,
        confidence=None,
        rule_id=f"llm_decider.{decision.category}",
        raw_evidence=raw_evidence,
    )


class LLMDecider:
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
                f"Local GGUF not found at {self.model_path}. "
                "Download bartowski/Qwen2.5-1.5B-Instruct-GGUF (IQ2_XS) and put it in models/llm_decider/ "
                "or pass model_path=..."
            )
        try:
            from llama_cpp import Llama
        except Exception as exc:
            raise RuntimeError("llama-cpp-python required. pip install llama-cpp-python") from exc

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
        messages[0]["content"] = SYSTEM_PROMPT

        raw = self._chat(messages)
        batch, err = parse_model_output(raw)
        findings: list[Finding] = []

        if batch is not None:
            for decision in batch.decisions:
                seg = None
                if decision.exact_quote:
                    for cat_ctx in context.categories:
                        if cat_ctx.category != decision.category:
                            continue
                        for passage in cat_ctx.passages:
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
                    for cat_ctx in context.categories:
                        if cat_ctx.category != decision.category or not cat_ctx.passages:
                            continue
                        p = cat_ctx.passages[0]
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
                        finding_id=f"ld-cover-{category}",
                        clause_category=cat,
                        risk_level=STATUS_TO_RISK[FindingStatus.NOT_FOUND],
                        status=FindingStatus.NOT_FOUND,
                        exact_quote="",
                        source_id="",
                        page_number=None,
                        explanation="No relevant clause was retrieved for this category by RAG.",
                        recommended_action="Check the contract manually for this category.",
                        verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                        rule_id=f"llm_decider.{category}.coverage",
                        raw_evidence={"rule": f"llm_decider.{category}.coverage", "coverage": True},
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

    def analyze_file(self, file_path: str, source_name: Optional[str] = None, policy: Optional[PolicyConfig] = None) -> AnalysisResult:
        loaded = load_document(file_path)
        name = source_name or os.path.basename(file_path)
        return self.analyze_text(loaded.raw_text, filename=name, policy=policy)

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
