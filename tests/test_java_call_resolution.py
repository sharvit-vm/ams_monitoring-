import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from models import FileInfo, PipelineState
from parsers.java_parser import JavaParser
from phases.java_calls import java_call_batch
from phases import neo4j_ingest as graph
from tools import neo4j_tool


class JavaCallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.files = []

    def parse(self, path, source):
        target = Path(self.temp.name) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        file = JavaParser().parse(FileInfo(path=path, absolute_path=str(target), language="java"))
        self.assertIsNone(file.parse_error)
        self.files.append(file)
        return file

    def batch(self):
        return java_call_batch(PipelineState(repo_path=self.temp.name, knowledge_id="test", files=self.files))[0]

    def setup_repository(self):
        self.parse("Repository.java", """package p;
            interface Repository extends ExternalRepository<Client, Integer> {
                Client findByEmail(String email);
            }
        """)
        return self.parse("Service.java", """package p;
            class Service {
                Repository repository;
                Client findById(int id) { return repository.findById(id); }
                Client update(int id) { return repository.findById(id); }
                Client findByEmail(String email) { return repository.findByEmail(email); }
                void recursive() { this.recursive(); }
            }
        """)

    def test_receiver_calls_do_not_fall_back_to_same_name(self):
        file = self.setup_repository()
        self.assertEqual(file.functions[1].call_sites[0].receiver_type, "Repository")
        batch = self.batch()
        self.assertFalse(any(r["caller_name"] == "update" for r in batch))
        self.assertTrue(any(r["caller_name"] == "findByEmail" and r["callee_file"] == "Repository.java" for r in batch))
        self.assertTrue(any(r["caller_name"] == "recursive" and r["callee_name"] == "recursive" for r in batch))
        self.assertEqual(self.files[0].classes[0].extended_classes, ["ExternalRepository"])

    def test_interface_resolves_to_declaration_not_all_implementations(self):
        self.parse("Api.java", "package p; interface Api { void run(); }")
        self.parse("Impl.java", "package p; class Impl implements Api { public void run() {} }")
        self.parse("Caller.java", "package p; class Caller { Api api; void start() { api.run(); } }")
        self.assertEqual([r["callee_file"] for r in self.batch()], ["Api.java"])

    def test_import_package_and_receiver_shadowing(self):
        self.parse("a/Repo.java", "package a; class Repo { void save() {} }")
        self.parse("b/Repo.java", "package b; class Repo { void save() {} }")
        self.parse("Other.java", "package c; class Other { void save() {} }")
        self.parse("Caller.java", """package c; import a.Repo;
            class Caller { Repo repo;
                void field() { repo.save(); }
                void parameter(Other repo) { repo.save(); this.repo.save(); }
                void local() { Other repo = new Other(); repo.save(); }
            }""")
        edges = {(r["caller_name"], r["callee_file"]) for r in self.batch()}
        self.assertIn(("field", "a/Repo.java"), edges)
        self.assertIn(("parameter", "Other.java"), edges)
        self.assertIn(("parameter", "a/Repo.java"), edges)
        self.assertIn(("local", "Other.java"), edges)
        self.assertFalse(any(path == "b/Repo.java" for _, path in edges))

    def test_ambiguous_overload_is_not_a_confirmed_call(self):
        self.parse("A.java", """class A {
            void run(String x) {} void run(Integer x) {}
            void run() {} void start() { run(null); run(); }
        }""")
        self.assertEqual(len(self.batch()), 1)

    def test_duplicate_qualified_type_abstains(self):
        self.parse("one/A.java", "package p; class A { void run() {} }")
        self.parse("two/A.java", "package p; class A { void run() {} }")
        self.parse("Caller.java", "package p; class Caller { A a; void start() { a.run(); } }")
        self.assertEqual(self.batch(), [])

    def test_varargs_and_unknown_chained_receivers_abstain(self):
        self.parse("A.java", """class A {
            void run(String... args) {} void call() { run(); factory().call(); }
            A factory() { return this; }
        }""")
        self.assertEqual([r["callee_name"] for r in self.batch()], ["factory"])

    def test_java_call_rebuild_is_scoped_and_rejects_missing_targets(self):
        self.setup_repository()
        state = PipelineState(repo_path=self.temp.name, knowledge_id="test", files=self.files)
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        tx = MagicMock()
        session.execute_write.side_effect = lambda callback: callback(tx)
        tx.run.return_value.single.return_value = {"matched": len(self.batch())}
        graph.create_calls_relationships(driver, state)
        removal = tx.run.call_args_list[0]
        self.assertEqual(removal.kwargs["knowledge_id"], "test")
        self.assertEqual(removal.kwargs["paths"], [f.path for f in self.files])
        tx.run.return_value.single.return_value = {"matched": 0}
        with self.assertRaises(ValueError):
            graph.create_calls_relationships(driver, state)

    def test_graph_failure_does_not_mark_ingestion_complete(self):
        self.setup_repository()
        state = PipelineState(repo_path=self.temp.name, knowledge_id="test", files=self.files)
        names = ["create_constraints", "create_knowledge_node", "create_file_nodes",
                 "create_function_nodes", "create_class_nodes", "create_level_nodes"]
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(graph, "get_neo4j_driver", return_value=None))
            for name in names:
                stack.enter_context(patch.object(graph, name))
            stack.enter_context(patch.object(graph, "create_file_in_folder_relationships", side_effect=RuntimeError("test failure")))
            with self.assertRaises(RuntimeError):
                graph.neo4j_ingest(state)
        self.assertFalse(state.neo4j_complete)

    def test_java_import_uses_class_name(self):
        self.parse("a/Repo.java", "package a; class Repo {}")
        self.parse("Caller.java", "package b; import a.Repo; class Caller {}")
        state = PipelineState(repo_path=self.temp.name, knowledge_id="test", files=self.files)
        with patch.object(graph, "_run_batch") as run:
            graph.create_imports_class_relationships(None, state)
        self.assertEqual(run.call_args.args[2][0]["class_path"], "a/Repo.java")

    def test_path_lookup_accepts_either_ingestion_os(self):
        for stored in ["src/A.java", "src\\A.java"]:
            with patch.object(neo4j_tool, "run_query", return_value=[{"path": stored}]):
                self.assertEqual(neo4j_tool._normalise_file_path("src/A.java", "test"), stored)
        with patch.object(neo4j_tool, "run_query", return_value=[{"path": "src/A.java"}, {"path": "src\\A.java"}]):
            with self.assertRaises(ValueError):
                neo4j_tool._normalise_file_path("src/A.java", "test")

    def test_java_cache_reparsed_when_source_or_version_changes(self):
        from phases import file_analysis
        file = self.parse("A.java", "class A { void oldName() {} }")
        def analyze():
            fresh = FileInfo(path=file.path, absolute_path=file.absolute_path, language="java")
            return file_analysis.analyze_files(PipelineState(
                repo_path=self.temp.name, knowledge_id="cache-test", files=[fresh])).files[0]
        with patch.object(file_analysis, "CACHE_DIR", str(Path(self.temp.name) / "cache")):
            first = analyze()
            Path(file.absolute_path).write_text("class A { void newName() {} }", encoding="utf-8")
            second = analyze()
            self.assertNotEqual(first.source_hash, second.source_hash)
            self.assertEqual(second.functions[0].name, "newName")
            with patch.object(JavaParser, "VERSION", "test-version"):
                self.assertEqual(analyze().parser_version, "test-version")


if __name__ == "__main__":
    unittest.main()
