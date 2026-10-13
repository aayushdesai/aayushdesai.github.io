"""Offline acceptance tests for durable, bounded MAST enumeration.

Every request is injected. These tests never query a live archive or sleep.
"""
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import mast_paging


def request_at(url):
    return json.loads(parse_qs(urlsplit(url).query)["request"][0])


def page_body(requested_page, rows, total, page_size=2, **paging_changes):
    paging = dict(page=requested_page, pageSize=page_size, rowsFiltered=total,
                  pagesFiltered=math.ceil(total / page_size))
    paging.update(paging_changes)
    return json.dumps(dict(status="COMPLETE", data=rows, paging=paging)).encode()


def response(body, status=200, headers=None):
    return status, headers or {}, body


def records(*identities):
    return [dict(obsid=identity, value="row-" + str(identity)) for identity in identities]


class ScriptedFetch:
    """Reject unplanned requests, including accidental page-one repetition."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []

    def __call__(self, url, timeout):
        request = request_at(url)
        self.requests.append(request)
        if not self.steps:
            raise AssertionError("Unexpected request for page " + str(request["page"]))
        expected_page, reply = self.steps.pop(0)
        if request["page"] != expected_page:
            raise AssertionError(f"Expected page {expected_page}, received {request['page']}")
        if isinstance(reply, BaseException):
            raise reply
        return reply

    @property
    def pages(self):
        return [request["page"] for request in self.requests]


class MastPagingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.out = Path(self.directory.name)
        self.sleeps = []

    def collect(self, fetcher, **options):
        settings = dict(limit=20, page_size=2, timeout=2, retries=0,
                        fetcher=fetcher, sleeper=self.sleeps.append)
        settings.update(options)
        return mast_paging.collect_pages("Mast.Caom.Cone", dict(ra=1, dec=2, radius=0.01),
                                         self.out, **settings)

    def accepted_files(self):
        """Verify the manifest's chain of hashes and return its exact bytes."""
        manifest = json.loads((self.out / "manifest.json").read_text())
        snapshot = {}
        for saved in manifest["pages"]:
            receipt_path = self.out / saved["receipt"]
            receipt_bytes = receipt_path.read_bytes()
            self.assertEqual(hashlib.sha256(receipt_bytes).hexdigest(), saved["receipt_sha256"])
            receipt = json.loads(receipt_bytes)
            raw_path = self.out / receipt["raw_response"]
            raw_bytes = raw_path.read_bytes()
            self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(), receipt["raw_sha256"])
            snapshot[receipt_path.name] = receipt_bytes
            snapshot[raw_path.name] = raw_bytes
        return manifest, snapshot

    def assert_unchanged(self, snapshot):
        for name, body in snapshot.items():
            self.assertEqual((self.out / name).read_bytes(), body, name)

    def assert_incomplete(self, result):
        self.assertFalse(result["query_complete"])
        self.assertNotEqual(result["status"], "no_matches")

    def reject_saved_checkpoint(self, fetcher):
        """Corruption may raise or return a failure, but must fail before fetch."""
        try:
            result = self.collect(fetcher)
        except (ValueError, OSError):
            pass
        else:
            self.assert_incomplete(result)
            self.assertIn(result["status"], ("partial", "unavailable"))
        self.assertEqual(fetcher.pages, [])

    def test_complete_three_pages_and_receipt_hashes(self):
        fetcher = ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 5))),
            (2, response(page_body(2, records(3, 4), 5))),
            (3, response(page_body(3, records(5), 5))))
        result = self.collect(fetcher, identity_field="obsid")
        self.assertEqual(fetcher.pages, [1, 2, 3])
        self.assertEqual(result["records"], records(1, 2, 3, 4, 5))
        self.assertEqual(result["status"], "matches")
        self.assertTrue(result["query_complete"])
        self.assertEqual((result["returned_count"], result["retained_count"]), (5, 5))
        self.assertEqual(result["service_total_rows"], 5)
        self.assertIsNone(result["next_page"])
        self.assertEqual(len(result["page_receipts"]), 3)
        manifest, _ = self.accepted_files()
        self.assertEqual(len(manifest["pages"]), 3)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["records"], result["records"])

    def test_completed_checkpoint_is_replayed_without_fetch_or_mutation(self):
        self.collect(ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 3))),
            (2, response(page_body(2, records(3), 3)))))
        _, snapshot = self.accepted_files()
        manifest_bytes = (self.out / "manifest.json").read_bytes()
        fetcher = ScriptedFetch()
        result = self.collect(fetcher)
        self.assertEqual(fetcher.pages, [])
        self.assertTrue(result["query_complete"])
        self.assertEqual(result["records"], records(1, 2, 3))
        self.assert_unchanged(snapshot)
        self.assertEqual((self.out / "manifest.json").read_bytes(), manifest_bytes)

    def test_record_budget_retains_prefix_and_stops_before_remaining_pages(self):
        fetcher = ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 6))),
            (2, response(page_body(2, records(3, 4), 6))))
        result = self.collect(fetcher, limit=3)
        self.assertEqual(fetcher.pages, [1, 2])
        self.assertEqual(result["records"], records(1, 2, 3))
        self.assertEqual((result["returned_count"], result["retained_count"]), (4, 3))
        self.assertEqual(result["service_total_rows"], 6)
        self.assertEqual(result["next_page"], 3)
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)

    def test_exact_limit_is_complete_only_when_service_total_is_exhausted(self):
        for total, expected_complete in ((4, True), (6, False)):
            with self.subTest(total=total), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                fetcher = ScriptedFetch(
                    (1, response(page_body(1, records(1, 2), total))),
                    (2, response(page_body(2, records(3, 4), total))))
                result = self.collect(fetcher, limit=4)
                self.assertEqual(fetcher.pages, [1, 2])
                self.assertEqual(result["query_complete"], expected_complete)
                self.assertEqual(result["retained_count"], 4)
                self.assertEqual(result["status"], "matches" if expected_complete else "partial")

    def test_single_page_over_budget_is_partial(self):
        fetcher = ScriptedFetch((1, response(page_body(1, records(1, 2, 3, 4), 4, page_size=4))))
        result = self.collect(fetcher, limit=3, page_size=500)
        self.assertEqual(fetcher.requests[0]["pagesize"], 4)
        self.assertEqual(result["records"], records(1, 2, 3))
        self.assertEqual((result["returned_count"], result["retained_count"]), (4, 3))
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)

    def test_keyboard_interrupt_resumes_only_missing_pages(self):
        initial = ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 5))),
            (2, KeyboardInterrupt()))
        with self.assertRaises(KeyboardInterrupt):
            self.collect(initial)
        manifest, snapshot = self.accepted_files()
        self.assertEqual(len(manifest["pages"]), 1)
        resumed = ScriptedFetch(
            (2, response(page_body(2, records(3, 4), 5))),
            (3, response(page_body(3, records(5), 5))))
        result = self.collect(resumed)
        self.assertEqual(resumed.pages, [2, 3])
        self.assertEqual(result["records"], records(1, 2, 3, 4, 5))
        self.assertTrue(result["query_complete"])
        self.assert_unchanged(snapshot)

    def test_later_page_failure_preserves_rows_and_failure_receipt(self):
        fetcher = ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 5))),
            (2, response(b"temporary failure", status=503)))
        result = self.collect(fetcher)
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)
        self.assertEqual(result["records"], records(1, 2))
        self.assertEqual((result["returned_count"], result["retained_count"]), (2, 2))
        self.assertEqual(result["next_page"], 2)
        self.assertEqual(result["service_total_rows"], 5)
        self.assertEqual(result["failure_status"], "unavailable")
        self.assertTrue(result["error"])
        failed_path = self.out / result["failed_receipt"]
        failed_body = failed_path.read_bytes()
        self.assertEqual(hashlib.sha256(failed_body).hexdigest(), result["failed_receipt_sha256"])
        manifest, snapshot = self.accepted_files()
        self.assertEqual(len(manifest["pages"]), 1)
        resumed = ScriptedFetch(
            (2, response(page_body(2, records(3, 4), 5))),
            (3, response(page_body(3, records(5), 5))))
        completed = self.collect(resumed)
        self.assertEqual(resumed.pages, [2, 3])
        self.assertTrue(completed["query_complete"])
        self.assertNotIn("error", completed)
        self.assertNotIn("failure_status", completed)
        self.assertEqual(failed_path.read_bytes(), failed_body)
        self.assert_unchanged(snapshot)

    def test_corrupt_saved_raw_bytes_stop_before_fetch(self):
        self.collect(ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 4))), (2, response(b"blocked", status=403))))
        manifest, _ = self.accepted_files()
        receipt = json.loads((self.out / manifest["pages"][0]["receipt"]).read_text())
        raw_path = self.out / receipt["raw_response"]
        changed_bytes = raw_path.read_bytes() + b" "
        raw_path.write_bytes(changed_bytes)
        self.reject_saved_checkpoint(ScriptedFetch())
        self.assertEqual(raw_path.read_bytes(), changed_bytes)

    def test_corrupt_saved_receipt_stops_before_fetch(self):
        self.collect(ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 4))), (2, response(b"blocked", status=403))))
        manifest, _ = self.accepted_files()
        receipt_path = self.out / manifest["pages"][0]["receipt"]
        changed_bytes = receipt_path.read_bytes() + b" "
        receipt_path.write_bytes(changed_bytes)
        self.reject_saved_checkpoint(ScriptedFetch())
        self.assertEqual(receipt_path.read_bytes(), changed_bytes)

    def test_verified_receipt_for_wrong_page_is_rejected(self):
        self.collect(ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 4))), (2, response(b"blocked", status=403))))
        manifest, _ = self.accepted_files()
        receipt_path = self.out / manifest["pages"][0]["receipt"]
        receipt = json.loads(receipt_path.read_text())
        receipt["page"] = 99
        receipt_path.write_text(json.dumps(receipt))
        manifest["pages"][0]["receipt_sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        (self.out / "manifest.json").write_text(json.dumps(manifest))
        self.reject_saved_checkpoint(ScriptedFetch())

    def test_overlapping_identity_across_distinct_pages_is_incomplete(self):
        fetcher = ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 4))),
            (2, response(page_body(2, records(2, 3), 4))))
        result = self.collect(fetcher, identity_field="obsid")
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)
        self.assertTrue(result["warnings"] or result.get("error"))

    def test_duplicate_identity_within_page_is_incomplete(self):
        result = self.collect(ScriptedFetch((1, response(page_body(1, records(1, 1), 2)))),
                              identity_field="obsid")
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)

    def test_large_integer_identities_remain_distinct(self):
        identities = (9007199254740992, 9007199254740993)
        result = self.collect(ScriptedFetch((1, response(page_body(1, records(*identities), 2)))),
                              identity_field="obsid")
        self.assertTrue(result["query_complete"])
        self.assertEqual([row["obsid"] for row in result["records"]], list(identities))

    def test_repeated_full_page_is_incomplete_without_identity_field(self):
        rows = records(1, 2)
        result = self.collect(ScriptedFetch(
            (1, response(page_body(1, rows, 4))), (2, response(page_body(2, rows, 4)))))
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)

    def test_zero_rows_require_full_consistent_paging(self):
        result = self.collect(ScriptedFetch((1, response(page_body(1, [], 0)))),
                              identity_field="obsid")
        self.assertEqual(result["status"], "no_matches")
        self.assertTrue(result["query_complete"])
        self.assertEqual(result["records"], [])
        self.assertEqual((result["returned_count"], result["retained_count"]), (0, 0))
        self.assertEqual(result["service_total_rows"], 0)

    def test_zero_rows_without_paging_are_partial(self):
        result = self.collect(ScriptedFetch((1, response(b'{"status":"COMPLETE","data":[]}'))))
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)

    def test_http_403_is_access_required_not_an_empty_match(self):
        fetcher = ScriptedFetch((1, response(b"permission denied", status=403)))
        result = self.collect(fetcher, retries=2)
        self.assertEqual(fetcher.pages, [1])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(result["status"], "access_required")
        self.assert_incomplete(result)
        self.assertEqual(result["records"], [])
        self.assertEqual(result["failure_status"], "access_required")
        self.assertTrue((self.out / result["failed_receipt"]).is_file())

    def test_later_403_retains_matches_with_access_failure(self):
        result = self.collect(ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 4))), (2, response(b"denied", status=403))))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["records"], records(1, 2))
        self.assertEqual(result["failure_status"], "access_required")
        self.assert_incomplete(result)

    def test_changed_total_or_page_count_keeps_query_incomplete(self):
        for changes in (dict(rowsFiltered=4, pagesFiltered=2), dict(pagesFiltered=4)):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                result = self.collect(ScriptedFetch(
                    (1, response(page_body(1, records(1, 2), 5))),
                    (2, response(page_body(2, records(3, 4), 5, **changes)))))
                self.assertEqual(result["status"], "partial")
                self.assert_incomplete(result)
                self.assertEqual(result["service_total_rows"], 5)
                self.assertEqual(result["records"], records(1, 2))
                self.assertEqual(result["next_page"], 2)
                manifest, _ = self.accepted_files()
                self.assertEqual(len(manifest["pages"]), 1)

    def test_invalid_or_missing_paging_metadata_never_completes(self):
        variations = (
            dict(page=2), dict(pageSize=3), dict(pagesFiltered=2),
            dict(rowsFiltered=-1), dict(rowsFiltered=True), dict(rowsFiltered=2.5),
            dict(rowsFiltered=None), dict(pageSize=None), dict(pagesFiltered=None))
        for changes in variations:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                result = self.collect(ScriptedFetch((1, response(page_body(1, records(1, 2), 2, **changes)))))
                self.assertEqual(result["status"], "partial")
                self.assert_incomplete(result)

    def test_short_page_before_reported_end_is_partial(self):
        result = self.collect(ScriptedFetch((1, response(page_body(1, records(1), 4)))))
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)

    def test_empty_page_with_reported_matches_is_partial(self):
        result = self.collect(ScriptedFetch((1, response(page_body(1, [], 2)))))
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)

    def test_malformed_complete_response_is_not_no_matches(self):
        for payload in (dict(status="COMPLETE"), dict(status="ERROR", data=[]),
                        dict(status="COMPLETE", data=[None]), dict(status="COMPLETE", data={})):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                result = self.collect(ScriptedFetch((1, response(json.dumps(payload).encode()))))
                self.assertEqual(result["status"], "unavailable")
                self.assert_incomplete(result)
                self.assertTrue((self.out / result["failed_receipt"]).is_file())

    def test_saved_request_cannot_be_reused_with_a_different_budget(self):
        self.collect(ScriptedFetch((1, response(page_body(1, records(1), 1)))))
        _, snapshot = self.accepted_files()
        fetcher = ScriptedFetch()
        with self.assertRaises(ValueError):
            self.collect(fetcher, limit=19)
        self.assertEqual(fetcher.pages, [])
        self.assert_unchanged(snapshot)

    def test_executing_reply_retries_and_preserves_each_attempt(self):
        fetcher = ScriptedFetch(
            (1, response(b'{"status":"EXECUTING"}')),
            (1, response(page_body(1, records(1), 1))))
        result = self.collect(fetcher, retries=2)
        self.assertEqual(fetcher.pages, [1, 1])
        self.assertEqual(self.sleeps, [1])
        self.assertTrue(result["query_complete"])
        self.assertEqual(len(list(self.out.glob("*.receipt.json"))), 2)
        manifest, _ = self.accepted_files()
        self.assertEqual(len(manifest["pages"]), 1)
        self.assertNotIn("error", result)
        self.assertNotIn("failure_status", result)

    def test_executing_retry_budget_is_bounded(self):
        executing = response(b'{"status":"EXECUTING"}')
        fetcher = ScriptedFetch((1, executing), (1, executing), (1, executing))
        result = self.collect(fetcher, retries=2)
        self.assertEqual(fetcher.pages, [1, 1, 1])
        self.assertEqual(self.sleeps, [1, 2])
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)
        self.assertEqual(len(list(self.out.glob("*.receipt.json"))), 3)
        self.assertEqual(json.loads((self.out / "manifest.json").read_text())["pages"], [])

    def test_transient_http_error_retries_then_completes(self):
        fetcher = ScriptedFetch(
            (1, response(b"try later", status=503, headers={"Retry-After": "3"})),
            (1, response(page_body(1, records(1), 1))))
        result = self.collect(fetcher, retries=2)
        self.assertEqual(fetcher.pages, [1, 1])
        self.assertEqual(self.sleeps, [3])
        self.assertTrue(result["query_complete"])
        self.assertEqual(len(list(self.out.glob("*.receipt.json"))), 2)

    def test_repeated_clock_value_cannot_overwrite_failed_page_attempt(self):
        with patch.object(mast_paging.time, "time_ns", return_value=42):
            first = self.collect(ScriptedFetch((1, response(b"failure", status=503))))
            failed_path = self.out / first["failed_receipt"]
            receipt = json.loads(failed_path.read_text())
            raw_path = self.out / receipt["raw_response"]
            before = {failed_path.name: failed_path.read_bytes(), raw_path.name: raw_path.read_bytes()}
            second = self.collect(ScriptedFetch((1, response(page_body(1, records(1), 1)))))
        self.assertTrue(second["query_complete"])
        self.assertEqual(second["status"], "matches")
        self.assert_unchanged(before)
        manifest, _ = self.accepted_files()
        self.assertNotEqual(manifest["pages"][0]["receipt"], first["failed_receipt"])
        self.assertEqual(len(list(self.out.glob("*.receipt.json"))), 2)
        self.assertEqual(len(list(self.out.glob("*.response"))), 2)

    def test_long_or_date_retry_after_stops_without_sleeping(self):
        for retry_after in ("120", "Wed, 14 Oct 2026 00:00:00 GMT"):
            with self.subTest(retry_after=retry_after), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                self.sleeps = []
                fetcher = ScriptedFetch((1, response(b"rate limited", status=429,
                                                   headers={"Retry-After": retry_after})))
                result = self.collect(fetcher, retries=2)
                self.assertEqual(fetcher.pages, [1])
                self.assertEqual(self.sleeps, [])
                self.assert_incomplete(result)
                self.assertTrue(result["error"])

    def test_network_errors_use_bounded_retries(self):
        fetcher = ScriptedFetch((1, OSError("offline")), (1, OSError("offline")), (1, OSError("offline")))
        result = self.collect(fetcher, retries=2)
        self.assertEqual(fetcher.pages, [1, 1, 1])
        self.assertEqual(self.sleeps, [1, 2])
        self.assertEqual(result["status"], "unavailable")
        self.assert_incomplete(result)
        self.assertTrue((self.out / result["failed_receipt"]).is_file())

    def test_validator_failure_on_later_page_preserves_accepted_rows(self):
        def validator(rows):
            if any("value" not in row for row in rows):
                raise ValueError("Missing required observation field")
        result = self.collect(ScriptedFetch(
            (1, response(page_body(1, records(1, 2), 3))),
            (2, response(page_body(2, [dict(obsid=3)], 3)))), validator=validator)
        self.assertEqual(result["status"], "partial")
        self.assert_incomplete(result)
        self.assertEqual(result["records"], records(1, 2))
        manifest, _ = self.accepted_files()
        self.assertEqual(len(manifest["pages"]), 1)


if __name__ == "__main__":
    unittest.main()
