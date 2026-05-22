import json
import os
from typing import Optional

import psycopg2
import psycopg2.extras

CREATE_EDIT_LOG = """
CREATE TABLE IF NOT EXISTS edit_log (
    id SERIAL PRIMARY KEY,
    instruction TEXT NOT NULL,
    proposed_operation JSONB,
    validation_passed BOOLEAN,
    validation_reason TEXT,
    outcome TEXT NOT NULL,
    validation_enabled BOOLEAN NOT NULL,
    latency_ms INTEGER,
    estimated_cost_usd NUMERIC(10,6),
    model TEXT NOT NULL DEFAULT 'openai/gpt-4o-mini',
    eval_run_tag TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
"""

CREATE_EVAL_RUNS = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id SERIAL PRIMARY KEY,
    tag TEXT NOT NULL,
    eval_set_version TEXT NOT NULL,
    validation_enabled BOOLEAN NOT NULL,
    total_instructions INTEGER,
    accuracy NUMERIC(5,4),
    refusal_precision NUMERIC(5,4),
    refusal_recall NUMERIC(5,4),
    false_accept_rate NUMERIC(5,4),
    avg_latency_ms INTEGER,
    p95_latency_ms INTEGER,
    total_cost_usd NUMERIC(10,4),
    created_at TIMESTAMPTZ DEFAULT now()
);
"""


class Storage:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or os.environ.get("DATABASE_URL", "")
        self.conn = None

    def connect(self):
        if self.conn is None or self.conn.closed:
            self.conn = psycopg2.connect(self.database_url)
            self.conn.autocommit = True
        return self.conn

    def close(self):
        if self.conn and not self.conn.closed:
            self.conn.close()

    def setup(self):
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(CREATE_EDIT_LOG)
            cur.execute(CREATE_EVAL_RUNS)

    def log_edit(
        self,
        instruction: str,
        proposed_operation: Optional[dict] = None,
        validation_passed: Optional[bool] = None,
        validation_reason: Optional[str] = None,
        outcome: str = "applied",
        validation_enabled: bool = True,
        latency_ms: int = 0,
        estimated_cost_usd: float = 0.0,
        model: str = "openai/gpt-4o-mini",
        eval_run_tag: Optional[str] = None,
    ):
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO edit_log
                   (instruction, proposed_operation, validation_passed, validation_reason,
                    outcome, validation_enabled, latency_ms, estimated_cost_usd,
                    model, eval_run_tag)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    instruction,
                    json.dumps(proposed_operation) if proposed_operation else None,
                    validation_passed,
                    validation_reason,
                    outcome,
                    validation_enabled,
                    latency_ms,
                    estimated_cost_usd,
                    model,
                    eval_run_tag,
                ),
            )

    def get_recent_logs(self, limit: int = 10) -> list[dict]:
        conn = self.connect()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM edit_log ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()

    def get_edit_logs_by_tag(self, tag: str) -> list[dict]:
        conn = self.connect()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM edit_log WHERE eval_run_tag = %s ORDER BY id",
                (tag,),
            )
            return cur.fetchall()

    def save_eval_run(
        self,
        tag: str,
        eval_set_version: str,
        validation_enabled: bool = True,
        total_instructions: int = 0,
        accuracy: Optional[float] = None,
        refusal_precision: Optional[float] = None,
        refusal_recall: Optional[float] = None,
        false_accept_rate: Optional[float] = None,
        avg_latency_ms: Optional[int] = None,
        p95_latency_ms: Optional[int] = None,
        total_cost_usd: Optional[float] = None,
    ):
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO eval_runs
                   (tag, eval_set_version, validation_enabled, total_instructions,
                    accuracy, refusal_precision, refusal_recall, false_accept_rate,
                    avg_latency_ms, p95_latency_ms, total_cost_usd)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    tag,
                    eval_set_version,
                    validation_enabled,
                    total_instructions,
                    accuracy,
                    refusal_precision,
                    refusal_recall,
                    false_accept_rate,
                    avg_latency_ms,
                    p95_latency_ms,
                    total_cost_usd,
                ),
            )

    def get_eval_runs(self, tag: Optional[str] = None) -> list[dict]:
        conn = self.connect()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if tag:
                cur.execute(
                    "SELECT * FROM eval_runs WHERE tag = %s ORDER BY created_at",
                    (tag,),
                )
            else:
                cur.execute("SELECT * FROM eval_runs ORDER BY created_at")
            return cur.fetchall()

    def get_stats(self) -> dict:
        conn = self.connect()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) as total FROM edit_log")
            log_count = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) as total FROM eval_runs")
            run_count = cur.fetchone()["total"]
            cur.execute(
                "SELECT outcome, COUNT(*) as cnt FROM edit_log GROUP BY outcome"
            )
            outcomes = {r["outcome"]: r["cnt"] for r in cur.fetchall()}
        return {
            "total_edits": log_count,
            "total_eval_runs": run_count,
            "outcomes": outcomes,
        }
