from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, String, Text, text

from core.database import Base


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(32), nullable=False, index=True)
    dataset_name = Column(String(255), nullable=False)
    total_queries = Column(Integer, default=0)
    context_precision = Column(Float, default=0.0)
    context_recall = Column(Float, default=0.0)
    faithfulness = Column(Float, default=0.0)
    answer_relevancy = Column(Float, default=0.0)
    answer_correctness = Column(Float, default=0.0)
    ragas_score = Column(Float, default=0.0)
    avg_latency_ms = Column(Float, default=0.0)
    results_json = Column(Text)
    created_at = Column(DateTime, default=datetime.now, server_default=text("CURRENT_TIMESTAMP"))
