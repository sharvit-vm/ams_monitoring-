import unittest
from types import SimpleNamespace

from observability.token_usage import (
    UsageCollector,
    combine_usage,
    provider_call,
    track_usage,
    workflow_usage,
)


class TokenUsageTests(unittest.TestCase):
    def test_collector_aggregates_provider_reported_calls(self):
        collector = UsageCollector()
        collector.start("one", "model-a")
        collector.finish("one", {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14})
        collector.start("two", "model-a")
        collector.finish("two", {"input_tokens": 8, "output_tokens": 2})

        usage = collector.snapshot()

        self.assertEqual(usage["input_tokens"], 18)
        self.assertEqual(usage["output_tokens"], 6)
        self.assertEqual(usage["total_tokens"], 24)
        self.assertEqual(usage["measurement"], "provider_reported")
        self.assertEqual(usage["calls"], 2)

    def test_unreported_call_is_not_presented_as_zero_tokens(self):
        collector = UsageCollector()
        collector.start("failed")

        usage = collector.snapshot()

        self.assertIsNone(usage["total_tokens"])
        self.assertEqual(usage["measurement"], "unavailable")

    def test_stage_decorator_attaches_usage_without_calls(self):
        @track_usage
        def stage():
            return {}

        self.assertEqual(stage()["token_usage"]["measurement"], "not_used")

    def test_provider_call_usage_is_attached_to_stage_result(self):
        @track_usage
        def stage():
            provider_call(
                lambda **_: SimpleNamespace(
                    model="test-model",
                    usage=SimpleNamespace(
                        model_dump=lambda: {
                            "prompt_tokens": 7,
                            "completion_tokens": 3,
                            "total_tokens": 10,
                        }
                    ),
                ),
                model="test-model",
            )
            return {}

        usage = stage()["token_usage"]

        self.assertEqual(usage["total_tokens"], 10)
        self.assertEqual(usage["measurement"], "provider_reported")
        self.assertEqual(usage["models"], ["test-model"])

    def test_combined_usage_marks_partial_totals(self):
        combined = combine_usage(
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
             "calls": 1, "reported_calls": 1, "measurement": "provider_reported"},
            {"input_tokens": None, "output_tokens": None, "total_tokens": None,
             "calls": 1, "reported_calls": 0, "measurement": "unavailable"},
        )
        self.assertEqual(combined["total_tokens"], 15)
        self.assertEqual(combined["measurement"], "partial")

    def test_workflow_usage_maps_stage_contracts(self):
        usage = {"total_tokens": 12}
        response = {
            "categorisation": {"token_usage": usage},
            "l2_rca": {"token_usage_details": usage},
            "l3_rca": {"token_usage": usage},
            "codefix": {"token_usage": usage},
            "l3_context_token_usage": usage,
        }
        mapped = workflow_usage(response)
        self.assertEqual(set(mapped), {"categorisation", "l2_rca", "l3_context", "l3_rca", "codefix"})
        self.assertTrue(all(item == usage for item in mapped.values()))


if __name__ == "__main__":
    unittest.main()
