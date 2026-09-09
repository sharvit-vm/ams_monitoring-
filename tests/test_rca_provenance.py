import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from agents.l3_rca.agent import _build_parallel_context
from issuelayer.intake.schemas import ErrorEvent
from rag.domain.schemas import EvidenceCandidate, RetrievalHit, RetrievalResult
from rag.retrieval.evidence_bundle import build_evidence_bundle
from rag.retrieval.graph_expander import expand_graph_context
from rag.retrieval.incident_parser import incident_evidence_context


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        Path(self.root, "reported.py").write_text("def lookup(value):\n    pass\n    pass\n    return value.strip()\n", encoding="utf-8")
        self.event = ErrorEvent(id="provenance", fingerprint="test", error_type="Error",
            message="lookup failed", file_path="reported.py", line_number=4,
            traceback='File "reported.py", line 4, in lookup')
        self.retrieval = RetrievalResult(query="lookup failed", knowledge_id="snapshot",
            hits=[RetrievalHit(chunk_id="one", file_path="candidate.py", symbol_name="lookup")])

    def bundle(self, retrieval):
        return build_evidence_bundle(event=self.event, knowledge_id="snapshot",
            retrieval_result=retrieval, deterministic_context={}, repo_dir=self.root)

    def test_usable_traceback_remains_primary_despite_other_rag_candidate(self):
        with patch("rag.retrieval.evidence_bundle.expand_graph_candidates", return_value=[
                {"status": "completed", "seed": {"file_path": "candidate.py"}}]) as graph:
            bundle = self.bundle(self.retrieval)
        self.assertEqual(bundle.primary_candidate.file_path, "reported.py")
        self.assertEqual(bundle.traceback_candidate.file_path, "reported.py")
        self.assertEqual([c.file_path for c in graph.call_args.args[0]], ["reported.py", "candidate.py"])
        self.assertEqual(bundle.confidence_inputs["entry_status"], "traceback-backed")

    def test_neither_traceback_nor_retrieval_leaves_entry_unresolved(self):
        self.event.traceback = ""
        with patch("rag.retrieval.graph_expander.expand_graph_context") as graph:
            bundle = self.bundle(RetrievalResult(query="error", knowledge_id="snapshot"))
        graph.assert_not_called()
        self.assertIsNone(bundle.primary_candidate)
        self.assertEqual(bundle.confidence_inputs["entry_status"], "unresolved")
        self.assertEqual(bundle.graph_context["status"], "skipped")

    def test_prefetch_retrieves_before_expansion_without_event_file_tools(self):
        stages = []
        def retrieve(*args):
            stages.append("retrieval")
            return self.retrieval
        def expand(*args, **kwargs):
            stages.append("graph")
            return []
        with patch("agents.l3_rca.agent.retrieve_incident_context", side_effect=retrieve), \
             patch("rag.retrieval.evidence_bundle.expand_graph_candidates", side_effect=expand), \
             patch("agents.l3_rca.agent.get_file_summary") as old_lookup:
            context = _build_parallel_context(self.event, "snapshot", self.root)
        self.assertEqual(stages, ["retrieval", "graph"])
        old_lookup.invoke.assert_not_called()
        self.assertEqual(context["entry_status"], "traceback-backed")

    def test_retrieval_outage_preserves_usable_traceback(self):
        with patch("agents.l3_rca.agent.retrieve_incident_context", side_effect=TimeoutError("secret")), \
             patch("rag.retrieval.evidence_bundle.expand_graph_candidates", return_value=[]):
            context = _build_parallel_context(self.event, "snapshot", self.root)
        self.assertEqual(context["entry_status"], "traceback-backed")
        self.assertEqual(context["rag_result"]["retrieval_trace"]["error_type"], "TimeoutError")
        self.assertNotIn("secret", str(context))

    def test_empty_retrieval_still_expands_traceback(self):
        with patch("rag.retrieval.evidence_bundle.expand_graph_candidates", return_value=[]) as graph:
            bundle = self.bundle(RetrievalResult(query="error", knowledge_id="snapshot"))
        self.assertEqual(bundle.primary_candidate.file_path, "reported.py")
        self.assertEqual(graph.call_args.args[0][0].source, "traceback_frame")

    def test_invalid_line_falls_back_to_retrieval_and_records_warning(self):
        self.event.traceback = 'File "reported.py", line 400, in lookup'
        with patch("rag.retrieval.evidence_bundle.expand_graph_candidates", return_value=[]):
            bundle = self.bundle(self.retrieval)
        self.assertEqual(bundle.primary_candidate.file_path, "candidate.py")
        self.assertEqual(bundle.confidence_inputs["entry_status"], "retrieval-backed")
        self.assertTrue(bundle.confidence_inputs["traceback_location_warnings"])

    def test_duplicate_package_paths_are_not_arbitrarily_selected(self):
        for module in ("one", "two"):
            path = Path(self.root, module, "example", "Service.java")
            path.parent.mkdir(parents=True)
            path.write_text("class Service {}\n", encoding="utf-8")
        self.event.traceback = "at example.Service.run(Service.java:1)"
        with patch("rag.retrieval.evidence_bundle.expand_graph_candidates", return_value=[]):
            bundle = self.bundle(self.retrieval)
        self.assertEqual(bundle.confidence_inputs["entry_status"], "retrieval-backed")
        self.assertEqual(bundle.traceback_frames[0].file_path, "")

    def test_python_innermost_frame_is_primary_but_original_order_is_preserved(self):
        Path(self.root, "caller.py").write_text("lookup(None)\n", encoding="utf-8")
        self.event.traceback = ('File "caller.py", line 1, in handle\n'
                                'File "reported.py", line 4, in lookup')
        with patch("rag.retrieval.evidence_bundle.expand_graph_candidates", return_value=[]):
            bundle = self.bundle(self.retrieval)
        self.assertEqual(bundle.primary_candidate.file_path, "reported.py")
        self.assertEqual([f.file_path for f in bundle.traceback_frames], ["caller.py", "reported.py"])
        self.assertFalse(bundle.confidence_inputs["traceback_revision_verified"])

    def test_source_change_after_traceback_is_not_claimed_to_match_revision(self):
        Path(self.root, "reported.py").write_text("# changed\n# changed\n# changed\npass\n", encoding="utf-8")
        with patch("rag.retrieval.evidence_bundle.expand_graph_candidates", return_value=[]):
            bundle = self.bundle(self.retrieval)
        self.assertEqual(bundle.confidence_inputs["entry_status"], "traceback-backed")
        self.assertFalse(bundle.confidence_inputs["traceback_revision_verified"])

    def test_missing_traceback_does_not_invent_execution(self):
        self.event.traceback = ""
        evidence = incident_evidence_context(self.event)
        self.assertEqual(evidence["reported_frames"], [])
        self.assertFalse(evidence["runtime_independently_verified"])

    def test_reported_frames_preserved_in_supplied_order(self):
        self.event.traceback = ('at example.Service.run(Service.java:10)\n'
                                'at example.Controller.handle(Controller.java:20)')
        evidence = incident_evidence_context(self.event)
        self.assertEqual([f["function_name"] for f in evidence["reported_frames"]], ["run", "handle"])
        self.assertEqual(evidence["trace_completeness"], "unknown")

    def test_failed_graph_lookup_is_not_completed_evidence(self):
        with patch("rag.retrieval.graph_expander._invoke_tool", return_value={"error": "unavailable"}):
            graph = expand_graph_context(EvidenceCandidate(source="rag", file_path="candidate.py"), "snapshot")
        self.assertEqual(graph["status"], "partial")
        self.assertFalse(graph["proves_runtime_execution"])
        self.assertIn("file_summary", graph["failed_lookups"])


if __name__ == "__main__":
    unittest.main()
