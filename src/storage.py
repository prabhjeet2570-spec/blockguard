import os
from typing import Optional

import psycopg2

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
