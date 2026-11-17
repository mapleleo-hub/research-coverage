"""Offline fault tests. Live reference recovery is a separate, explicitly labelled run."""
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import coverage as c


PLAN = {"topic": "Find reusable agent components", "max_queries": 8, "max_inspections": 4, "limit": 2,
        "axes": [{"id": "memory", "label": "Memory", "queries": ["agent memory", "context database"]},
                 {"id": "history", "label": "History", "queries": ["session history", "agent handoff"]}]}


def fake_gh(argv):
    if argv[-1].endswith("/readme"):
        data = {"encoding": "base64", "content": base64.b64encode(
            b"Useful link https://github.com/Else/Third.git#readme").decode(), "html_url": "https://github.com/example/readme"}
    else:
        data = {"total_count": 10, "items": [
            {"html_url": "https://github.com/Alpha/One", "full_name": "Alpha/One", "description": "memory"},
            {"html_url": "https://github.com/Beta/Two", "full_name": "Beta/Two", "description": "history"}]}
    return data, json.dumps(data).encode()


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plan_file = self.root / "input.json"
        self.plan_file.write_text(json.dumps(PLAN), encoding="utf-8")
        self.run = self.root / "run"
        c.initialize(self.plan_file, self.run)
        self.state = c.load(self.run)

    def tearDown(self):
        self.temp.cleanup()

    def ready(self):
        with patch.object(c, "gh", fake_gh):
            c.run_initial(self.run, self.state)
            c.inspect_repo(self.run, self.state, "alpha/one")
            c.inspect_repo(self.run, self.state, "beta/two")
        for ident in self.state["candidates"]:
            c.review_candidate(self.run, self.state, ident, "shortlist", "Test screening evidence")

    def test_plan_never_discards_extra_queries(self):
        plan = copy.deepcopy(PLAN)
        plan["axes"][0]["queries"] += ["semantic store", "knowledge service"]
        checked = c.validate_plan(plan)
        self.assertEqual(len(checked["axes"][0]["queries"]), 4)
        plan["max_queries"] = 4
        with self.assertRaises(ValueError): c.validate_plan(plan)
        plan["max_queries"] = True
        with self.assertRaises(ValueError): c.validate_plan(plan)

    def test_plan_rejects_duplicate_queries_and_axes(self):
        plan = copy.deepcopy(PLAN)
        plan["axes"][0]["queries"] = ["Agent Memory", "agent  memory"]
        with self.assertRaises(ValueError): c.validate_plan(plan)
        plan = copy.deepcopy(PLAN); plan["axes"][1]["id"] = "memory"
        with self.assertRaises(ValueError): c.validate_plan(plan)

    def test_ready_is_only_process_checked_and_origins_are_preserved(self):
        self.ready()
        result = c.audit(self.run, self.state)
        self.assertEqual(result["status"], "process_checked")
        self.assertEqual(len(self.state["candidates"]), 3)
        self.assertEqual(len(self.state["candidates"]["alpha/one"]["origins"]), 4)
        self.assertEqual(len(result["truncated_queries"]), 4)
        self.assertIn("unproven", result["limits"])

    def test_missing_axis_and_duplicate_success_do_not_complete_coverage(self):
        with patch.object(c, "gh", fake_gh):
            c.search(self.run, self.state, "memory", "Agent Memory")
            c.search(self.run, self.state, "memory", "agent  memory")
        result = c.audit(self.run, self.state)
        self.assertIn("insufficient_distinct_queries:memory", result["issues"])
        self.assertIn("insufficient_distinct_queries:history", result["issues"])

    def test_failed_request_consumes_budget_and_native_retry_resolves_it(self):
        with patch.object(c, "gh", side_effect=ValueError("HTTP 429")):
            with self.assertRaises(ValueError): c.search(self.run, self.state, "memory", "agent memory")
        self.assertEqual(self.state["queries"][0]["status"], "failed")
        self.assertEqual(len(self.state["queries"]), 1)
        with self.assertRaises(ValueError): c.run_initial(self.run, self.state)
        with patch.object(c, "gh", fake_gh): c.search(self.run, self.state, "memory", "agent memory")
        self.assertEqual(c.unresolved(self.state["queries"], "query"), [])
        self.assertEqual(c.audit(self.run, self.state)["historical_failed_attempts"], 1)

    def test_import_is_labelled_and_cannot_resolve_native_failure(self):
        with patch.object(c, "gh", side_effect=ValueError("offline")):
            with self.assertRaises(ValueError): c.search(self.run, self.state, "memory", "agent memory")
        file = self.root / "web.json"
        file.write_text(json.dumps({"provider": "web-tool", "results": [{"url": "https://github.com/alpha/one"}]}))
        c.import_results(self.run, self.state, "memory", "agent memory", file)
        self.assertEqual(self.state["queries"][-1]["kind"], "imported")
        self.assertEqual(c.unresolved(self.state["queries"], "query"), ["q0001"])

    def test_valid_import_changes_status_without_claiming_native_execution(self):
        self.ready()
        file = self.root / "web.json"
        file.write_text(json.dumps({"provider": "github-api", "results": [{"url": "https://github.com/alpha/one"}]}))
        c.import_results(self.run, self.state, "memory", "other terminology", file)
        result = c.audit(self.run, self.state)
        self.assertEqual(result["status"], "documented_with_imports")
        self.assertEqual(result["imported_attempts"], 1)

    def test_budget_rejects_network_call_before_it_occurs(self):
        self.state["plan"]["max_queries"] = 4
        with patch.object(c, "gh", fake_gh): c.run_initial(self.run, self.state)
        with patch.object(c, "gh") as mocked:
            with self.assertRaises(ValueError): c.search(self.run, self.state, "memory", "more")
            mocked.assert_not_called()

    def test_empty_results_do_not_prove_no_candidate_exists(self):
        empty = {"total_count": 0, "items": []}
        with patch.object(c, "gh", return_value=(empty, json.dumps(empty).encode())):
            c.run_initial(self.run, self.state)
        result = c.audit(self.run, self.state)
        self.assertIn("no_candidates_found:memory", result["issues"])
        self.assertEqual(result["status"], "needs_attention")

    def test_snapshot_corruption_and_escape_fail_audit(self):
        self.ready()
        row = self.state["queries"][0]
        (self.run / row["snapshot"]).write_bytes(b"changed")
        self.assertIn("snapshot_changed:q0001", c.audit(self.run, self.state)["issues"])
        row["snapshot"] = "../outside.json"
        self.assertIn("snapshot_unreadable_or_escaped:q0001", c.audit(self.run, self.state)["issues"])

    def test_reference_found_missing_and_query_contamination_are_separate(self):
        self.ready()
        clean = c.audit(self.run, self.state, ["alpha/one", "beta/two"])
        self.assertEqual(clean["reference_check"]["found_count"], 2)
        missing = c.audit(self.run, self.state, ["missing/repository"])
        self.assertIn("reference_items_missing", missing["issues"])
        # Distinctive project slug avoids accidental natural-language overlap in this test.
        self.state["queries"][0]["query"] = "find unique-project-name"
        polluted = c.audit(self.run, self.state, ["owner/unique-project-name"])
        self.assertIn("reference_query_contamination", polluted["issues"])

    def test_defer_and_empty_review_reason_cannot_be_hidden(self):
        self.ready()
        with self.assertRaises(ValueError): c.review_candidate(self.run, self.state, "alpha/one", "exclude", " ")
        c.review_candidate(self.run, self.state, "alpha/one", "defer", "Needs further information")
        self.assertIn("alpha/one", c.audit(self.run, self.state)["pending_candidates"])

    def test_running_attempt_and_lock_are_not_treated_as_completed(self):
        c.new_query(self.run, self.state, "memory", "agent memory", "native", "github-api")
        self.assertIn("unresolved_attempt:q0001", c.audit(self.run, self.state)["issues"])
        with c.locked(self.run):
            with self.assertRaises(ValueError):
                with c.locked(self.run): pass
        self.assertFalse((self.run / ".lock").exists())

    def test_explicit_unlock_is_the_only_recovery_path(self):
        (self.run / ".lock").write_text(json.dumps({"pid": 999999, "started_at": "2026-11-17T00:00:00Z"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            with c.locked(self.run):
                pass
        c.unlock_run(self.run)
        self.assertFalse((self.run / ".lock").exists())
        with self.assertRaises(ValueError):
            c.unlock_run(self.run)

    def test_timeouts_and_bad_api_shapes_are_recorded_as_failure(self):
        with patch.object(c, "gh", side_effect=subprocess.TimeoutExpired("gh", 40)):
            with self.assertRaises(ValueError): c.search(self.run, self.state, "memory", "agent memory")
        with patch.object(c, "gh", return_value=({"message": "invalid"}, b'{"message":"invalid"}')):
            with self.assertRaises(ValueError): c.search(self.run, self.state, "memory", "another query")
        self.assertEqual([r["status"] for r in self.state["queries"]], ["failed", "failed"])

    def test_plan_immutable_and_reference_names_are_not_in_example_queries(self):
        saved = c.read_json(self.run / "plan.json")
        saved["topic"] = "changed"
        c.atomic_json(self.run / "plan.json", saved)
        with self.assertRaises(ValueError): c.load(self.run)
        example = c.read_json(Path(__file__).resolve().parents[1] / "assets/agent-memory-plan.json")
        serialized = json.dumps(example).lower()
        self.assertNotIn("openviking", serialized)
        self.assertNotIn("deja-vu", serialized)

    def test_canonicalization_deduplicates_github_but_preserves_web_query(self):
        self.assertEqual(c.canonical("https://GitHub.com/Owner/Repo.git#readme")[0], "owner/repo")
        self.assertEqual(c.canonical("https://example.com/?q=one#two")[0], "https://example.com/?q=one")
        for url in ("javascript:alert(1)", "file:///secret", "https://user:password@example.com/"):
            with self.assertRaises(ValueError): c.canonical(url)

    def test_every_planned_query_must_be_attempted_not_only_first_two(self):
        self.ready()
        self.state["plan"]["axes"][0]["queries"].append("a third distinct direction")
        result = c.audit(self.run, self.state)
        self.assertIn("planned_query_not_run:memory:a third distinct direction", result["issues"])

    def test_incomplete_github_results_require_a_full_explicit_retry(self):
        partial, _ = fake_gh(["search"])
        partial["incomplete_results"] = True
        with patch.object(c, "gh", return_value=(partial, json.dumps(partial).encode())):
            c.search(self.run, self.state, "memory", "agent memory")
        self.assertEqual(self.state["queries"][0]["status"], "partial")
        self.assertEqual(c.unresolved(self.state["queries"], "query"), ["q0001"])
        with patch.object(c, "gh", fake_gh): c.search(self.run, self.state, "memory", "agent memory")
        self.assertEqual(c.unresolved(self.state["queries"], "query"), [])

    def test_import_rejects_non_object_and_inspect_rejects_unknown_repo(self):
        file = self.root / "invalid.json"; file.write_text("[]")
        with self.assertRaises(ValueError): c.import_results(self.run, self.state, "memory", "query", file)
        with patch.object(c, "gh") as mocked:
            with self.assertRaises(ValueError): c.inspect_repo(self.run, self.state, "unknown/project")
            mocked.assert_not_called()


class PaginationAndScreeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        plan_file = self.root / "input.json"
        plan_file.write_text(json.dumps(PLAN), encoding="utf-8")
        self.run = self.root / "run"
        c.initialize(plan_file, self.run)
        self.state = c.load(self.run)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def item(name, description="memory tool", **extra):
        row = {"html_url": f"https://github.com/{name}", "full_name": name, "description": description,
               "stargazers_count": 120, "pushed_at": "2026-11-01T00:00:00Z", "archived": False,
               "fork": False, "language": "Python", "topics": ["memory"], "license": {"spdx_id": "MIT"}}
        row.update(extra)
        return row

    @staticmethod
    def paged(pages):
        def fake(argv):
            number = 1
            for index, value in enumerate(argv):
                if value == "-f" and index + 1 < len(argv) and argv[index + 1].startswith("page="):
                    number = int(argv[index + 1].split("=")[1])
            data = pages.get(number, {"total_count": 0, "items": []})
            return data, json.dumps(data).encode()
        return fake

    def test_pagination_stops_on_short_page_and_records_coverage(self):
        pages = {1: {"total_count": 5, "items": [self.item("Alpha/One"), self.item("Beta/Two")]},
                 2: {"total_count": 5, "items": [self.item("Beta/Two"), self.item("Gamma/Three")]},
                 3: {"total_count": 5, "items": []}}
        with patch.object(c, "gh", self.paged(pages)):
            c.search(self.run, self.state, "memory", "agent memory")
        row = self.state["queries"][0]
        self.assertEqual(row["pages_fetched"], 3)
        self.assertEqual(row["items_seen"], 4)
        self.assertEqual(row["count"], 3)
        self.assertTrue(row["truncated"])
        self.assertEqual(row["coverage"], 0.8)
        self.assertEqual(len(row["snapshots"]), 3)
        self.assertEqual(len(self.state["candidates"]), 3)

    def test_pagination_respects_page_budget_and_reports_no_truncation_when_complete(self):
        self.state["plan"]["pages"] = 1
        pages = {1: {"total_count": 2, "items": [self.item("Alpha/One"), self.item("Beta/Two")]}}
        with patch.object(c, "gh", self.paged(pages)):
            c.search(self.run, self.state, "memory", "agent memory")
        row = self.state["queries"][0]
        self.assertEqual(row["pages_fetched"], 1)
        self.assertFalse(row["truncated"])
        self.assertEqual(row["coverage"], 1.0)

    def test_hard_filters_are_kept_traceable_outside_cards(self):
        pages = {1: {"total_count": 3, "items": [self.item("Alpha/One"), self.item("Beta/Two"), self.item("Cold/Three")]}}
        with patch.object(c, "gh", self.paged(pages)):
            c.search(self.run, self.state, "memory", "agent memory")
        self.state["candidates"]["alpha/one"]["archived"] = True
        self.state["candidates"]["beta/two"]["fork"] = True
        self.state["candidates"]["cold/three"]["description"] = ""
        self.state["candidates"]["cold/three"]["topics"] = []
        records = c.screen_run(self.run, self.state)
        memory = [r for r in records if r["axis"] == "memory"][0]
        self.assertEqual(memory["screened"], 0)
        self.assertEqual(memory["filtered"], 3)
        deferred = json.loads((self.run / "cards/memory.deferred.json").read_text(encoding="utf-8"))
        self.assertEqual({item["filtered_by"] for item in deferred["items"]}, {"archived", "fork", "no_description"})
        self.assertEqual(len(self.state["candidates"]), 3)

    def test_cards_hold_only_the_screened_slice_and_never_drop_candidates(self):
        self.state["plan"]["screen"] = 1
        pages = {1: {"total_count": 2, "items": [self.item("Alpha/One", "memory layer"), self.item("Beta/Two", "history sync")]}}
        with patch.object(c, "gh", self.paged(pages)):
            c.search(self.run, self.state, "memory", "agent memory")
        records = c.screen_run(self.run, self.state)
        memory = [r for r in records if r["axis"] == "memory"][0]
        self.assertEqual(memory["screened"], 1)
        self.assertEqual(memory["deferred"], 1)
        self.assertEqual(memory["cards"][0]["id"], "alpha/one")
        deferred = json.loads((self.run / "cards/memory.deferred.json").read_text(encoding="utf-8"))
        self.assertEqual(deferred["items"][0]["id"], "beta/two")
        self.assertTrue((self.run / "cards/memory.md").exists())
        self.assertEqual(len(self.state["candidates"]), 2)

    def test_audit_reports_context_budget_columns(self):
        with patch.object(c, "gh", fake_gh):
            c.run_initial(self.run, self.state)
            c.inspect_repo(self.run, self.state, "alpha/one")
            c.inspect_repo(self.run, self.state, "beta/two")
        for ident in self.state["candidates"]:
            c.review_candidate(self.run, self.state, ident, "shortlist", "screening evidence")
        result = c.audit(self.run, self.state)
        self.assertEqual(len(result["coverage"]), 2)
        self.assertEqual(result["coverage"][0]["total"], 3)
        self.assertEqual(result["coverage"][0]["filtered"], 1)
        self.assertEqual(len(result["card_items"]), 4)
        self.assertIn("cards/memory.md", (self.run / "report.md").read_text(encoding="utf-8"))

    def test_upstream_parses_manifest_and_survives_single_package_failure(self):
        pages = {1: {"total_count": 1, "items": [self.item("Alpha/One")]}}
        body = json.dumps({"dependencies": {"left-pad": "^1.0.0"}, "devDependencies": {"jest": "^29"}}).encode()

        def fake(argv):
            if argv[-1] == "repos/alpha/one/contents/package.json":
                data = {"encoding": "base64", "content": base64.b64encode(body).decode()}
                return data, json.dumps(data).encode()
            raise ValueError("gh exit 1: Not Found")

        def fake_deps(system, name):
            if name == "jest":
                raise ValueError("deps.dev request failed: URLError: offline")
            return {"system": system, "name": name, "version": "1.0.0", "licenses": ["MIT"], "advisories": ["GHSA-x"]}

        with patch.object(c, "gh", self.paged(pages)):
            c.search(self.run, self.state, "memory", "agent memory")
        with patch.object(c, "gh", fake), patch.object(c, "deps_package", fake_deps):
            c.upstream_run(self.run, self.state, "alpha/one")
        row = self.state["upstreams"][0]
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["declared"], 2)
        self.assertEqual(row["count"], 2)
        self.assertEqual(row["failures"], 1)
        self.assertEqual(row["advisories"], 1)
        self.assertTrue((self.run / row["result_file"]).exists())

    def test_upstream_rejects_undiscovered_repo_before_network(self):
        with patch.object(c, "gh") as mocked:
            with self.assertRaises(ValueError):
                c.upstream_run(self.run, self.state, "unknown/project")
            mocked.assert_not_called()

    def test_imports_cannot_satisfy_native_coverage_or_planned_queries(self):
        file = self.root / "web.json"
        file.write_text(json.dumps({"provider": "web-tool", "results": [{"url": "https://github.com/alpha/one"}]}),
                        encoding="utf-8")
        c.import_results(self.run, self.state, "memory", "agent memory", file)
        c.import_results(self.run, self.state, "memory", "context database", file)
        result = c.audit(self.run, self.state)
        self.assertIn("insufficient_distinct_queries:memory", result["issues"])
        self.assertIn("planned_query_not_run:memory:agent memory", result["issues"])
        self.assertEqual(result["imported_attempts"], 2)
        self.assertEqual(result["status"], "needs_attention")

    def test_empty_vocabulary_scores_zero_instead_of_dividing_by_zero(self):
        score, _ = c.score_candidate({"id": "a/b", "title": "a/b", "description": "nothing",
                                      "origins": ["q0001"], "stars": 0}, set())
        self.assertEqual(score, 6)

    def test_scoring_uses_the_fixed_state_clock(self):
        pages = {1: {"total_count": 1, "items": [self.item("Alpha/One", "memory layer")]}}
        with patch.object(c, "gh", self.paged(pages)):
            c.search(self.run, self.state, "memory", "agent memory")
        fresh = c.screen_run(self.run, self.state)[0]["cards"][0]["score"]
        self.state["scoring_at"] = "2030-01-01T00:00:00Z"
        stale = c.screen_run(self.run, self.state)[0]["cards"][0]["score"]
        self.assertEqual(fresh - stale, 15)
        self.assertEqual(stale, c.screen_run(self.run, self.state)[0]["cards"][0]["score"])

    def test_upstream_shares_the_inspection_budget(self):
        self.state["plan"]["max_inspections"] = 2
        c.add_candidate(self.state, {"url": "https://github.com/alpha/one"}, "manual", ["memory"])
        with patch.object(c, "gh_manifest", return_value=(None, None)):
            c.upstream_run(self.run, self.state, "alpha/one")
            c.upstream_run(self.run, self.state, "alpha/one")
            with self.assertRaises(ValueError):
                c.upstream_run(self.run, self.state, "alpha/one")

    def test_same_owner_with_identical_description_does_not_take_several_slots(self):
        self.state["plan"]["screen"] = 5
        shared = ("A code graph toolkit that lowers agent token costs for large repositories "
                  "with incremental indexing and blast radius analysis across many languages")
        variant = ("A code graph toolkit that lowers agent token costs for large repositories "
                   "with incremental indexing and blast radius analysis across many language")
        items = [self.item("Same/Impl", shared), self.item("Same/Mcp", variant), self.item("Same/Docs", shared),
                 self.item("Other/Impl", "Gradle plugin wrapping the analyser"), self.item("Other/Fork", shared)]
        with patch.object(c, "gh", self.paged({1: {"total_count": 5, "items": items}})):
            c.search(self.run, self.state, "memory", "agent memory")
        record = c.screen_run(self.run, self.state)[0]
        # One copy of the shared description survives, the unrelated plugin also survives.
        self.assertEqual(record["screened"], 2)
        self.assertEqual(record["duplicated"], 3)
        ids = {item["id"] for item in record["cards"]}
        self.assertIn("other/impl", ids)
        self.assertEqual(len(ids & {"other/fork", "same/docs", "same/impl", "same/mcp"}), 1)
        deferred = json.loads((self.run / "cards/memory.deferred.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(1 for item in deferred["items"] if item["filtered_by"] == "duplicate_description"), 3)

    def test_adjacent_lists_candidates_just_below_the_cut_without_review_duty(self):
        self.state["plan"]["screen"] = 1
        self.state["plan"]["adjacent"] = 2
        items = [self.item("Alpha/One", "memory layer for agents", stargazers_count=900),
                 self.item("Beta/Two", "memory layer for agents", stargazers_count=100),
                 self.item("Gamma/Three", "memory layer for agents", stargazers_count=50),
                 self.item("Delta/Four", "memory layer for agents", stargazers_count=10)]
        with patch.object(c, "gh", self.paged({1: {"total_count": 4, "items": items}})):
            c.search(self.run, self.state, "memory", "agent memory")
        record = c.screen_run(self.run, self.state)[0]
        self.assertEqual(record["screened"], 1)
        self.assertEqual(record["adjacent"], 2)
        self.assertEqual(record["deferred"], 1)
        near = json.loads((self.run / "cards/memory.adjacent.json").read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in near["items"]], ["beta/two", "gamma/three"])
        self.assertIn("紧邻候补", (self.run / "cards/memory.md").read_text(encoding="utf-8"))
        result = c.audit(self.run, self.state)
        self.assertNotIn("screened_candidates_need_review:3", result["issues"])

    def test_plan_bounds_cover_new_fields(self):
        checked = c.validate_plan(copy.deepcopy(PLAN))
        self.assertEqual((checked["pages"], checked["screen"], checked["max_upstream"]), (3, 25, 40))
        for key, value in (("pages", 0), ("pages", 11), ("screen", 0), ("screen", 201), ("max_upstream", 0), ("screen", True)):
            plan = copy.deepcopy(PLAN)
            plan[key] = value
            with self.assertRaises(ValueError):
                c.validate_plan(plan)
        plan = copy.deepcopy(PLAN)
        plan["limit"] = 50
        plan["pages"] = 30
        with self.assertRaises(ValueError):
            c.validate_plan(plan)


if __name__ == "__main__":
    unittest.main()
