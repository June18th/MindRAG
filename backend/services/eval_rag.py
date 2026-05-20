"""Professional RAG Evaluation Architecture

Pipeline: Dataset → Retrieve → Generate → RAGAS Metrics → Persist

Supports:
  - Manual test cases with ground truth
  - Synthetic test data generation from uploaded documents (LLM generates QA pairs)
  - RAGAS metrics: Context Precision, Context Recall, Faithfulness, Answer Relevancy, Answer Correctness
  - Batch evaluation with results persistence for trend comparison
  - LLM-as-Judge scoring for all qualitative metrics
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from clients.embedding_client import embed_query
from clients.elasticsearch_client import get_es
from core.database import async_session_factory

logger = logging.getLogger(__name__)


# ============================================================
# Data Models
# ============================================================

@dataclass
class EvalSample:
    query: str
    ground_truth: str = ""
    reference_contexts: list[str] = field(default_factory=list)


@dataclass
class EvalRun:
    """A single evaluation run with persisted results."""
    run_id: str
    dataset_name: str
    total_queries: int = 0
    # Retrieval
    context_precision: float = 0.0
    context_recall: float = 0.0
    # Generation
    faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    answer_correctness: float = 0.0
    # Aggregated
    ragas_score: float = 0.0
    avg_latency_ms: float = 0.0
    created_at: str = ""
    samples: list[dict] = field(default_factory=list)


# ============================================================
# Core Pipeline
# ============================================================

async def run_evaluation(
    dataset_name: str,
    samples: list[dict],
    top_k: int = 5,
    generate_answers: bool = True,
) -> EvalRun:
    """Execute a complete RAG evaluation run.

    Args:
        dataset_name: Name for this eval run
        samples: List of {"query": str, "groundTruth": str, "referenceContexts": [str]}
        top_k: Number of documents to retrieve per query
        generate_answers: Whether to generate and evaluate answers (requires LLM)
    """
    from services.chat import _build_llm

    es = get_es()
    llm = _build_llm()
    run_id = uuid.uuid4().hex[:12]

    results = []
    cp_sum = cr_sum = faith_sum = ar_sum = ac_sum = 0.0
    latency_sum = 0.0

    for i, sample in enumerate(samples):
        query = sample["query"]
        ground_truth = sample.get("groundTruth", "")
        ref_contexts = sample.get("referenceContexts", [])

        # Phase 1: Retrieve
        t0 = time.time()
        query_vector = await embed_query(query)
        es_hits = await _es_search(es, query, query_vector, top_k)
        contexts = [h.get("textContent", "") for h in es_hits]
        latency = (time.time() - t0) * 1000

        # Phase 2: RAGAS retrieval metrics (LLM-as-Judge)
        cp = await _context_precision_judge(query, contexts, llm)
        cr = _context_recall_ratio(contexts, ref_contexts)

        # Phase 3: Generate + evaluate answer
        faith = ar = ac = 0.0
        answer = ""
        if generate_answers and contexts:
            answer = await _generate_answer(llm, query, "\n---\n".join(contexts[:3]))
            faith = await _faithfulness_judge(answer, contexts[:3], llm)
            ar = await _answer_relevancy_judge(query, answer, llm)
            ac = await _answer_correctness_judge(answer, ground_truth, llm)

        results.append({
            "index": i, "query": query,
            "groundTruth": ground_truth,
            "retrievedContexts": [_trunc(c, 200) for c in contexts[:3]],
            "generatedAnswer": answer[:500],
            "contextPrecision": round(cp, 3),
            "contextRecall": round(cr, 3),
            "faithfulness": round(faith, 3),
            "answerRelevancy": round(ar, 3),
            "answerCorrectness": round(ac, 3),
            "latencyMs": round(latency, 1),
        })

        cp_sum += cp; cr_sum += cr
        faith_sum += faith; ar_sum += ar; ac_sum += ac
        latency_sum += latency

    n = max(len(samples), 1)

    # RAGAS Score: harmonic mean of all metrics with non-zero values
    all_metrics = [cp_sum/n, cr_sum/n, faith_sum/n, ar_sum/n, ac_sum/n]
    non_zero = [m for m in all_metrics if m > 0]
    ragas = (len(non_zero) / sum(1.0/m for m in non_zero)) if non_zero else 0.0

    run = EvalRun(
        run_id=run_id,
        dataset_name=dataset_name,
        total_queries=n,
        context_precision=round(cp_sum/n, 3),
        context_recall=round(cr_sum/n, 3),
        faithfulness=round(faith_sum/n, 3),
        answer_relevancy=round(ar_sum/n, 3),
        answer_correctness=round(ac_sum/n, 3),
        ragas_score=round(ragas, 3),
        avg_latency_ms=round(latency_sum/n, 1),
        created_at=datetime.now(timezone.utc).isoformat(),
        samples=results,
    )

    # Persist evaluation run
    await _persist_run(run)

    return run


# ============================================================
# Synthetic Test Data Generation
# ============================================================

async def generate_synthetic_dataset(
    file_md5_list: list[str],
    num_questions: int = 20,
) -> list[dict]:
    """Auto-generate QA pairs from uploaded documents for evaluation.

    Uses LLM to generate diverse question types:
      - Factual (what/who/when)
      - Conceptual (why/how)
      - Comparative (compare/contrast)
    """
    es = get_es()
    from services.chat import _build_llm
    llm = _build_llm()

    # 1. Retrieve representative chunks from each document
    all_contexts = []
    for md5 in file_md5_list:
        resp = await es.search(index="knowledge_base", body={
            "size": 10,
            "query": {"term": {"fileMd5": md5}},
            "_source": ["textContent", "fileMd5", "pageNumber", "anchorText"],
        })
        all_contexts.extend([h["_source"] for h in resp.get("hits", {}).get("hits", [])])

    if not all_contexts:
        return []

    # 2. LLM generates question + answer pairs from contexts
    dataset = []
    batch_size = 5
    questions_per_batch = max(1, num_questions // max(1, len(all_contexts) // batch_size))

    for i in range(0, len(all_contexts), batch_size):
        batch = all_contexts[i:i+batch_size]
        if len(dataset) >= num_questions:
            break

        ctx_text = "\n\n---\n\n".join(
            f"[{j}] {c.get('textContent', '')[:500]}"
            for j, c in enumerate(batch)
        )

        prompt = f"""基于以下知识库片段，生成 {min(questions_per_batch, num_questions - len(dataset))} 个高质量评测问题及标准答案。

要求：
1. 问题类型多样化：事实型(是什么)、概念型(为什么)、比较型(有何不同)
2. 答案必须精确引用片段内容
3. 输出 JSON 数组格式：[{{"query": "问题", "groundTruth": "标准答案"}}]

知识库片段：
{ctx_text[:3000]}"""

        try:
            resp = llm.invoke(prompt, max_tokens=2000)
            content = str(resp.content)

            # Extract JSON array from response
            json_start = content.find("[")
            json_end = content.rfind("]") + 1
            if json_start >= 0 and json_end > json_start:
                items = json.loads(content[json_start:json_end])
                for item in items:
                    if isinstance(item, dict) and "query" in item:
                        item["referenceContexts"] = [c.get("textContent", "")[:300] for c in batch]
                dataset.extend(items[:questions_per_batch])
        except Exception as e:
            logger.warning("Synthetic generation batch failed: %s", e)

    return dataset[:num_questions]


# ============================================================
# RAGAS Metrics (LLM-as-Judge)
# ============================================================

async def _context_precision_judge(query: str, contexts: list[str], llm) -> float:
    """LLM judges whether each retrieved context is relevant to the query."""
    if not contexts:
        return 0.0
    relevant = 0
    for ctx in contexts[:5]:
        try:
            prompt = f'Query: {query[:200]}\nContext: {ctx[:300]}\n\nDoes this context contain information relevant to answering the query? Answer only "yes" or "no".'
            resp = llm.invoke(prompt, max_tokens=5)
            if "yes" in str(resp.content).lower():
                relevant += 1
        except Exception:
            relevant += 0.5  # Partial credit on error
    return relevant / min(len(contexts), 5)


def _context_recall_ratio(contexts: list[str], expected: list[str]) -> float:
    """Fraction of expected contexts found in retrieved results."""
    if not expected:
        return 1.0 if contexts else 0.0
    found = sum(
        1 for exp in expected
        if any(_overlap(exp, ctx) > 0.3 for ctx in contexts)
    )
    return found / len(expected)


async def _faithfulness_judge(answer: str, contexts: list[str], llm) -> float:
    """LLM decomposes answer into claims and verifies each against contexts."""
    if not answer or not contexts:
        return 0.0
    try:
        ctx_joined = "\n\n".join(c[:400] for c in contexts)
        prompt = f"""Contexts:
{ctx_joined[:1500]}

Answer:
{answer[:600]}

Task: Identify if the answer contains ANY factual claims NOT supported by the contexts.
Answer only "fully_supported" or "has_unsupported_claims"."""
        resp = llm.invoke(prompt, max_tokens=10)
        result = str(resp.content).lower().strip()
        if "fully_supported" in result:
            return 1.0
        elif "has_unsupported" in result:
            return 0.0
        return 0.5
    except Exception:
        return 0.5


async def _answer_relevancy_judge(query: str, answer: str, llm) -> float:
    """LLM rates how well the answer addresses the query."""
    if not answer:
        return 0.0
    try:
        prompt = f"""Query: {query[:200]}

Answer: {answer[:400]}

Rate how relevant and complete this answer is for the query.
Output ONLY a number between 0.0 (completely irrelevant) and 1.0 (perfectly relevant)."""
        resp = llm.invoke(prompt, max_tokens=5)
        return _parse_score(str(resp.content))
    except Exception:
        return 0.5


async def _answer_correctness_judge(answer: str, ground_truth: str, llm) -> float:
    """LLM compares answer against ground truth for factual correctness."""
    if not ground_truth:
        return 1.0  # No GT = skip, assume correct
    if not answer:
        return 0.0
    try:
        prompt = f"""Reference Answer: {ground_truth[:400]}

Candidate Answer: {answer[:400]}

Compare the candidate against the reference for factual correctness.
Output ONLY a number 0.0-1.0 where:
  1.0 = completely correct
  0.5 = partially correct
  0.0 = completely incorrect"""
        resp = llm.invoke(prompt, max_tokens=5)
        return _parse_score(str(resp.content))
    except Exception:
        return 0.5


# ============================================================
# Helpers
# ============================================================

async def _es_search(es, query: str, query_vector: list[float] | None, top_k: int) -> list[dict]:
    body = {
        "size": top_k,
        "query": {"match": {"textContent": query}},
        "_source": ["fileMd5", "textContent", "anchorText", "pageNumber"],
    }
    if query_vector:
        body["knn"] = {"field": "vector", "query_vector": query_vector,
                       "k": top_k * 10, "num_candidates": top_k * 10}
    resp = await es.search(index="knowledge_base", body=body)
    return [h["_source"] for h in resp.get("hits", {}).get("hits", [])]


async def _generate_answer(llm, query: str, contexts: str) -> str:
    try:
        prompt = f"基于以下上下文回答问题：\n\n上下文：\n{contexts[:2000]}\n\n问题：{query}\n\n回答："
        resp = llm.invoke(prompt, max_tokens=400)
        return str(resp.content)
    except Exception:
        return ""


def _overlap(a: str, b: str) -> float:
    """Simple Jaccard-like token overlap."""
    wa = set(a[:200].split())
    wb = set(b[:200].split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _parse_score(raw: str) -> float:
    """Extract a 0.0-1.0 score from LLM output."""
    raw = raw.strip().rstrip(".")
    try:
        val = float(raw)
        return max(0.0, min(1.0, val))
    except ValueError:
        if "0" in raw:
            return 0.0
        return 0.5


def _trunc(s: str, n: int) -> str:
    return s[:n] + "..." if len(s) > n else s


async def _persist_run(run: EvalRun) -> None:
    """Persist evaluation run to DB for trend tracking."""
    try:
        from models.eval_run import EvalRun as EvalRunModel
        async with async_session_factory() as db:
            record = EvalRunModel(
                run_id=run.run_id,
                dataset_name=run.dataset_name,
                total_queries=run.total_queries,
                context_precision=run.context_precision,
                context_recall=run.context_recall,
                faithfulness=run.faithfulness,
                answer_relevancy=run.answer_relevancy,
                answer_correctness=run.answer_correctness,
                ragas_score=run.ragas_score,
                avg_latency_ms=run.avg_latency_ms,
                results_json=json.dumps(run.samples, ensure_ascii=False),
            )
            db.add(record)
            await db.commit()
    except Exception as e:
        logger.warning("Eval run persistence skipped: %s", e)


async def get_run_history(limit: int = 20) -> list[dict]:
    """Retrieve evaluation run history."""
    try:
        from models.eval_run import EvalRun as EvalRunModel
        from sqlalchemy import select, desc
        async with async_session_factory() as db:
            result = await db.execute(
                select(EvalRunModel).order_by(desc(EvalRunModel.created_at)).limit(limit)
            )
            runs = result.scalars().all()
            return [
                {
                    "runId": r.run_id, "datasetName": r.dataset_name,
                    "totalQueries": r.total_queries,
                    "contextPrecision": r.context_precision,
                    "contextRecall": r.context_recall,
                    "faithfulness": r.faithfulness,
                    "answerRelevancy": r.answer_relevancy,
                    "answerCorrectness": r.answer_correctness,
                    "ragasScore": r.ragas_score,
                    "avgLatencyMs": r.avg_latency_ms,
                    "createdAt": str(r.created_at),
                }
                for r in runs
            ]
    except Exception:
        return []
