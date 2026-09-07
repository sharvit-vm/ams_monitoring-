from pathlib import Path
import tempfile
import unittest

from rag.retrieval.incident_parser import parse_traceback_frames, resolve_traceback_frames
from rag.retrieval.reranker import rerank_hits
from rag.domain.schemas import RetrievalHit
from issuelayer.intake.schemas import ErrorEvent, make_fingerprint


class TracebackCandidateTests(unittest.TestCase):
    def test_java_traceback_preserves_framework_and_application_frames(self):
        traceback = (
            "java.lang.NumberFormatException: bad\n"
            "\tat java.base/java.lang.Integer.parseInt(Integer.java:652)\n"
            "\tat org.example.NumberUtils.createNumber(NumberUtils.java:474)\n"
        )

        frames = parse_traceback_frames(traceback)

        self.assertEqual([frame.function_name for frame in frames], ["parseInt", "createNumber"])
        self.assertTrue(frames[0].framework_frame)
        self.assertFalse(frames[1].framework_frame)
        self.assertEqual(frames[1].line_number, 474)

    def test_traceback_resolution_prefers_package_matching_repository_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "src" / "main" / "java" / "org" / "example" / "NumberUtils.java"
            source.parent.mkdir(parents=True)
            source.write_text("class NumberUtils {}\n", encoding="utf-8")

            traceback = "\tat org.example.NumberUtils.createNumber(NumberUtils.java:474)"
            frames = resolve_traceback_frames(traceback, directory)

            self.assertEqual(frames[0].file_path, "src/main/java/org/example/NumberUtils.java")
            self.assertEqual(frames[0].function_name, "createNumber")

    def test_verified_upstream_caller_outranks_leaf_failure_frame(self):
        traceback = (
            "java.lang.NumberFormatException: bad\n"
            "\tat java.base/java.lang.Integer.parseInt(Integer.java:652)\n"
            "\tat org.example.NumberUtils.createInteger(NumberUtils.java:684)\n"
            "\tat org.example.NumberUtils.createNumber(NumberUtils.java:474)\n"
        )
        event = ErrorEvent(
            id="test-event",
            fingerprint=make_fingerprint("NumberFormatException", "bad"),
            error_type="NumberFormatException",
            message="bad",
            traceback=traceback,
            file_path="NumberUtils.java",
            function_name="createInteger",
            line_number=684,
            is_code_issue=True,
            issue_category="code",
        )
        hits = [
            RetrievalHit(
                chunk_id="leaf", file_path="src/NumberUtils.java",
                start_line=679, end_line=685, score=0.95,
                content_type="code_symbol", symbol_name="createInteger",
                content="return Integer.decode(str);",
                metadata={"called_symbols": ["Integer"]},
            ),
            RetrievalHit(
                chunk_id="caller", file_path="src/NumberUtils.java",
                start_line=450, end_line=586, score=0.80,
                content_type="code_symbol", symbol_name="createNumber",
                content="return createInteger(str);",
                metadata={},
            ),
        ]

        ranked = rerank_hits(event, "NumberFormatException createInteger createNumber", hits, limit=2)

        self.assertEqual(ranked[0].symbol_name, "createNumber")
        self.assertTrue(ranked[0].metadata["traceback_upstream_candidate"])
