"""
telemetry/metrics.py
────────────────────────────────────────────────────────────────────────────
Per-request metrics accumulator.

Captures every stage duration, row counts, retry counts, and
success/failure tallies.  The final snapshot is embedded in the
API response under the "metrics" key so a React dashboard can
display it without any backend changes.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class PipelineMetrics:
    """Mutable metrics bag — one instance per pipeline execution."""

    # ── Stage durations (seconds, converted to ms on export) ─────────────────
    cmdb_lookup_ms:          Optional[float] = None
    relationship_lookup_ms:  Optional[float] = None
    postgres_discovery_ms:   Optional[float] = None
    db_connection_ms:        Optional[float] = None
    health_read_ms:          Optional[float] = None
    diagnosis_ms:            Optional[float] = None
    rule_lookup_ms:          Optional[float] = None
    execution_ms:            Optional[float] = None
    verification_ms:         Optional[float] = None
    incident_history_ms:     Optional[float] = None
    total_ms:                Optional[float] = None

    # ── Query metrics ─────────────────────────────────────────────────────────
    db_queries_executed:     int = 0
    db_rows_returned:        int = 0
    db_connections_opened:   int = 0

    # ── Execution counters ────────────────────────────────────────────────────
    actions_total:           int = 0
    actions_succeeded:       int = 0
    actions_failed:          int = 0
    retry_count:             int = 0

    # ── Overall outcome ───────────────────────────────────────────────────────
    pipeline_success:        bool = False
    verification_passed:     bool = False

    # ── Internal timers (not exported) ───────────────────────────────────────
    _timers: Dict[str, float] = field(default_factory=dict, repr=False)

    def start_timer(self, label: str) -> None:
        self._timers[label] = time.perf_counter()

    def stop_timer(self, label: str) -> float:
        """Stop timer, store result in the matching field, return ms."""
        elapsed_s = time.perf_counter() - self._timers.pop(label, time.perf_counter())
        elapsed_ms = round(elapsed_s * 1000, 2)
        _LABEL_MAP = {
            "cmdb_lookup":         "cmdb_lookup_ms",
            "relationship_lookup": "relationship_lookup_ms",
            "postgres_discovery":  "postgres_discovery_ms",
            "db_connection":       "db_connection_ms",
            "health_read":         "health_read_ms",
            "diagnosis":           "diagnosis_ms",
            "rule_lookup":         "rule_lookup_ms",
            "execution":           "execution_ms",
            "verification":        "verification_ms",
            "incident_history":    "incident_history_ms",
            "total":               "total_ms",
        }
        attr = _LABEL_MAP.get(label)
        if attr:
            setattr(self, attr, elapsed_ms)
        return elapsed_ms

    def record_query(self, rows: int = 0) -> None:
        self.db_queries_executed += 1
        self.db_rows_returned    += rows

    def record_connection(self) -> None:
        self.db_connections_opened += 1

    def record_action(self, success: bool) -> None:
        self.actions_total += 1
        if success:
            self.actions_succeeded += 1
        else:
            self.actions_failed += 1

    def to_dict(self) -> dict:
        return {
            "stage_durations_ms": {
                "cmdb_lookup":         self.cmdb_lookup_ms,
                "relationship_lookup": self.relationship_lookup_ms,
                "postgres_discovery":  self.postgres_discovery_ms,
                "db_connection":       self.db_connection_ms,
                "health_read":         self.health_read_ms,
                "diagnosis":           self.diagnosis_ms,
                "rule_lookup":         self.rule_lookup_ms,
                "execution":           self.execution_ms,
                "verification":        self.verification_ms,
                "incident_history":    self.incident_history_ms,
                "total":               self.total_ms,
            },
            "database": {
                "queries_executed":   self.db_queries_executed,
                "rows_returned":      self.db_rows_returned,
                "connections_opened": self.db_connections_opened,
            },
            "execution": {
                "actions_total":     self.actions_total,
                "actions_succeeded": self.actions_succeeded,
                "actions_failed":    self.actions_failed,
                "retry_count":       self.retry_count,
                "success_rate":      (
                    round(self.actions_succeeded / self.actions_total, 4)
                    if self.actions_total else None
                ),
            },
            "outcome": {
                "pipeline_success":    self.pipeline_success,
                "verification_passed": self.verification_passed,
            },
        }
