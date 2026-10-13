"""Offline resolver tests for explicit retries and interruption recovery."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import resolve_source


def resolved_body(*candidates):
    return json.dumps(dict(resolvedCoordinate=list(candidates))).encode()


def candidate(name="source"):
    return dict(ra=1, decl=2, canonicalName=name, resolver="SIMBAD")


def response(body, status=200):
    return status, {"Content-Type": "application/json"}, body


class ScriptedFetch:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        if not self.steps:
            raise AssertionError("Resolver made an unplanned network request")
        reply = self.steps.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class ResolverRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.out = Path(self.directory.name)

    def resolve(self, fetcher, **options):
        return resolve_source.resolve_name("source", self.out, timeout=2,
                                           fetcher=fetcher, **options)

    def history(self):
        manifest = json.loads((self.out / "attempts.json").read_text())
        self.assertIsInstance(manifest["attempts"], list)
        return manifest

    def verified_attempts(self):
        """Independently check every attempt hash and capture immutable bytes."""
        manifest = self.history()
        snapshot = {}
        for entry in manifest["attempts"]:
            receipt_path = self.out / entry["receipt"]
            receipt_bytes = receipt_path.read_bytes()
            self.assertEqual(hashlib.sha256(receipt_bytes).hexdigest(), entry["receipt_sha256"])
            receipt = json.loads(receipt_bytes)
            snapshot[receipt_path.name] = receipt_bytes
            if receipt.get("raw_response"):
                raw_path = self.out / receipt["raw_response"]
                raw_bytes = raw_path.read_bytes()
                self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(), receipt["raw_sha256"])
                snapshot[raw_path.name] = raw_bytes
        return manifest, snapshot

    def assert_unchanged(self, snapshot):
        for name, body in snapshot.items():
            self.assertEqual((self.out / name).read_bytes(), body, name)

    def assert_report_receipt(self, report):
        receipt_bytes = (self.out / report["receipt_file"]).read_bytes()
        self.assertEqual(hashlib.sha256(receipt_bytes).hexdigest(), report["receipt_sha256"])
        if report.get("raw_response"):
            raw_bytes = (self.out / report["raw_response"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(), report["raw_sha256"])

    def make_failed_then_successful(self):
        first = self.resolve(ScriptedFetch(response(b"temporary failure", status=503)))
        self.assertEqual(first["status"], "unavailable")
        second = self.resolve(ScriptedFetch(response(resolved_body(candidate()))),
                              resume=True, retry_failed=True)
        self.assertEqual(second["status"], "resolved")
        return first, second

    def assert_resume_rejects_without_fetch(self, **options):
        fetcher = ScriptedFetch()
        with self.assertRaises((ValueError, OSError, TypeError)):
            self.resolve(fetcher, resume=True, **options)
        self.assertEqual(fetcher.calls, [])

    def interrupt_publication(self, basename=None, receipt=False):
        original = resolve_source.atomic_json
        interrupted = []

        def save(path, value):
            name = Path(path).name
            target = name.endswith(".receipt.json") if receipt else name == basename
            # The initial empty history is allowed; interrupt publication of
            # an attempt only after a durable receipt exists.
            if basename == "attempts.json" and not list(self.out.glob("*.receipt.json")):
                target = False
            if target and not interrupted:
                interrupted.append(name)
                raise KeyboardInterrupt()
            return original(path, value)

        return patch.object(resolve_source, "atomic_json", side_effect=save), interrupted

    def test_success_has_verified_receipt_history_and_exact_response_bytes(self):
        body = resolved_body(candidate()) + b"\n"
        fetcher = ScriptedFetch(response(body))
        report = self.resolve(fetcher)
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(report["status"], "resolved")
        self.assertEqual(report["candidates"], [candidate()])
        self.assertEqual((self.out / report["raw_response"]).read_bytes(), body)
        self.assert_report_receipt(report)
        manifest, _ = self.verified_attempts()
        self.assertEqual(len(manifest["attempts"]), 1)
        self.assertEqual(manifest["attempts"][0]["receipt"], report["receipt_file"])

    def test_failed_retry_preserves_original_attempt_bytes(self):
        first = self.resolve(ScriptedFetch(response(b"failure", status=503)))
        _, before = self.verified_attempts()
        fetcher = ScriptedFetch(response(resolved_body(candidate())))
        second = self.resolve(fetcher, resume=True, retry_failed=True)
        self.assertEqual(second["status"], "resolved")
        self.assertEqual(len(fetcher.calls), 1)
        self.assertNotEqual(first["raw_response"], second["raw_response"])
        self.assertNotEqual(first["receipt_file"], second["receipt_file"])
        self.assert_unchanged(before)
        self.assert_report_receipt(second)
        manifest, _ = self.verified_attempts()
        self.assertEqual(len(manifest["attempts"]), 2)

    def test_repeated_clock_value_cannot_reuse_or_overwrite_attempt_files(self):
        with patch.object(resolve_source.time, "time_ns", return_value=42):
            first = self.resolve(ScriptedFetch(response(b"failure", status=503)))
            _, before = self.verified_attempts()
            second = self.resolve(ScriptedFetch(response(resolved_body(candidate()))),
                                  resume=True, retry_failed=True)
        self.assertEqual(second["status"], "resolved")
        self.assertNotEqual(first["receipt_file"], second["receipt_file"])
        self.assertNotEqual(first["raw_response"], second["raw_response"])
        self.assert_unchanged(before)
        manifest, _ = self.verified_attempts()
        self.assertEqual(len(manifest["attempts"]), 2)

    def test_ordinary_resume_of_failure_adds_no_attempt(self):
        first = self.resolve(ScriptedFetch(response(b"failure", status=503)))
        _, before = self.verified_attempts()
        history_bytes = (self.out / "attempts.json").read_bytes()
        fetcher = ScriptedFetch()
        resumed = self.resolve(fetcher, resume=True)
        self.assertEqual(resumed, first)
        self.assertEqual(fetcher.calls, [])
        self.assertEqual((self.out / "attempts.json").read_bytes(), history_bytes)
        self.assert_unchanged(before)

    def test_retry_failed_requires_resume(self):
        fetcher = ScriptedFetch()
        with self.assertRaises(ValueError):
            self.resolve(fetcher, retry_failed=True)
        self.assertEqual(fetcher.calls, [])

    def test_resolved_ambiguous_and_not_found_are_not_retried(self):
        for candidates, status in (([candidate()], "resolved"),
                                   ([candidate("a"), candidate("b")], "ambiguous"),
                                   ([], "not_found")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                first = self.resolve(ScriptedFetch(response(resolved_body(*candidates))))
                self.assertEqual(first["status"], status)
                history_bytes = (self.out / "attempts.json").read_bytes()
                fetcher = ScriptedFetch()
                self.assertEqual(self.resolve(fetcher, resume=True, retry_failed=True), first)
                self.assertEqual(fetcher.calls, [])
                self.assertEqual((self.out / "attempts.json").read_bytes(), history_bytes)

    def test_http_access_and_service_failure_stay_distinct(self):
        for http_status, expected in ((401, "access_required"), (403, "access_required"),
                                      (429, "unavailable"), (503, "unavailable")):
            with self.subTest(http_status=http_status), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                fetcher = ScriptedFetch(response(b"service failure", status=http_status))
                report = self.resolve(fetcher)
                self.assertEqual(report["status"], expected)
                self.assertEqual(report["candidates"], [])
                self.assertEqual(len(fetcher.calls), 1)
                self.assertTrue(report["error"])
                self.assert_report_receipt(report)

    def test_access_required_can_be_explicitly_retried(self):
        self.resolve(ScriptedFetch(response(b"restricted", status=403)))
        fetcher = ScriptedFetch(response(resolved_body(candidate())))
        report = self.resolve(fetcher, resume=True, retry_failed=True)
        self.assertEqual(report["status"], "resolved")
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(len(self.history()["attempts"]), 2)

    def test_oversized_response_is_partial_preserved_and_retryable(self):
        body = b" " * (resolve_source.MAX_BYTES + 1)
        first = self.resolve(ScriptedFetch(response(body)))
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["candidates"], [])
        self.assertEqual((self.out / first["raw_response"]).read_bytes(), body)
        _, before = self.verified_attempts()
        fetcher = ScriptedFetch(response(resolved_body(candidate())))
        report = self.resolve(fetcher, resume=True, retry_failed=True)
        self.assertEqual(report["status"], "resolved")
        self.assertEqual(len(fetcher.calls), 1)
        self.assert_unchanged(before)

    def test_network_failure_preserves_an_attempt_without_raw_bytes(self):
        report = self.resolve(ScriptedFetch(OSError("offline")))
        self.assertEqual(report["status"], "unavailable")
        self.assertTrue(report["error"])
        self.assert_report_receipt(report)
        manifest, _ = self.verified_attempts()
        self.assertEqual(len(manifest["attempts"]), 1)
        fetcher = ScriptedFetch(response(resolved_body(candidate())))
        self.assertEqual(self.resolve(fetcher, resume=True, retry_failed=True)["status"], "resolved")

    def test_invalid_candidates_are_unavailable_not_not_found(self):
        for body in (b"{}", b"<html>error</html>", resolved_body(dict(ra=999, decl=2)),
                     resolved_body(None), resolved_body(dict(ra=1))):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                report = self.resolve(ScriptedFetch(response(body)))
                self.assertEqual(report["status"], "unavailable")
                self.assertTrue(report["error"])
                self.assertEqual((self.out / report["raw_response"]).read_bytes(), body)

    def test_keyboard_interrupt_during_fetch_can_resume_explicit_retry(self):
        with self.assertRaises(KeyboardInterrupt):
            self.resolve(ScriptedFetch(KeyboardInterrupt()))
        before = {path.name: path.read_bytes() for path in self.out.glob("*.receipt.json")}
        fetcher = ScriptedFetch(response(resolved_body(candidate())))
        report = self.resolve(fetcher, resume=True, retry_failed=True)
        self.assertEqual(report["status"], "resolved")
        self.assertEqual(len(fetcher.calls), 1)
        self.assert_unchanged(before)
        self.verified_attempts()

    def test_orphan_raw_response_does_not_block_or_get_overwritten(self):
        publication, interrupted = self.interrupt_publication(receipt=True)
        with publication, self.assertRaises(KeyboardInterrupt):
            self.resolve(ScriptedFetch(response(resolved_body(candidate()))))
        self.assertTrue(interrupted)
        before = {path.name: path.read_bytes() for path in self.out.glob("*.response")}
        self.assertTrue(before)
        fetcher = ScriptedFetch(response(resolved_body(candidate())))
        report = self.resolve(fetcher, resume=True, retry_failed=True)
        self.assertEqual(report["status"], "resolved")
        self.assertEqual(len(fetcher.calls), 1)
        self.assert_unchanged(before)
        self.assertNotIn(report["raw_response"], before)

    def test_report_publication_interrupt_recovers_without_fetch(self):
        publication, interrupted = self.interrupt_publication(basename="report.json")
        with publication, self.assertRaises(KeyboardInterrupt):
            self.resolve(ScriptedFetch(response(resolved_body(candidate()))))
        self.assertTrue(interrupted)
        self.assertFalse((self.out / "report.json").exists())
        manifest, before = self.verified_attempts()
        self.assertEqual(len(manifest["attempts"]), 1)
        fetcher = ScriptedFetch()
        report = self.resolve(fetcher, resume=True)
        self.assertEqual(report["status"], "resolved")
        self.assertEqual(fetcher.calls, [])
        self.assert_report_receipt(report)
        self.assert_unchanged(before)

    def test_manifest_publication_interrupt_recovers_persisted_success(self):
        publication, interrupted = self.interrupt_publication(basename="attempts.json")
        with publication, self.assertRaises(KeyboardInterrupt):
            self.resolve(ScriptedFetch(response(resolved_body(candidate()))))
        self.assertTrue(interrupted)
        before = {path.name: path.read_bytes() for path in self.out.glob("*.receipt.json")}
        self.assertEqual(len(before), 1)
        fetcher = ScriptedFetch()
        report = self.resolve(fetcher, resume=True)
        self.assertEqual(report["status"], "resolved")
        self.assertEqual(fetcher.calls, [])
        self.assert_unchanged(before)
        self.assertEqual(len(self.history()["attempts"]), 1)

    def test_lost_report_is_reconstructed_from_manifested_success(self):
        first = self.resolve(ScriptedFetch(response(resolved_body(candidate()))))
        _, before = self.verified_attempts()
        (self.out / "report.json").unlink()
        fetcher = ScriptedFetch()
        resumed = self.resolve(fetcher, resume=True)
        self.assertEqual(resumed, first)
        self.assertEqual(fetcher.calls, [])
        self.assert_unchanged(before)

    def test_lost_report_after_failure_does_not_trigger_ordinary_retry(self):
        first = self.resolve(ScriptedFetch(response(b"failure", status=503)))
        (self.out / "report.json").unlink()
        fetcher = ScriptedFetch()
        self.assertEqual(self.resolve(fetcher, resume=True), first)
        self.assertEqual(fetcher.calls, [])

    def test_old_raw_corruption_is_detected_even_after_a_successful_retry(self):
        first, _ = self.make_failed_then_successful()
        raw = self.out / first["raw_response"]
        changed = raw.read_bytes() + b" "
        raw.write_bytes(changed)
        self.assert_resume_rejects_without_fetch(retry_failed=True)
        self.assertEqual(raw.read_bytes(), changed)

    def test_old_receipt_corruption_is_detected_before_fetch(self):
        first, _ = self.make_failed_then_successful()
        receipt = self.out / first["receipt_file"]
        changed = receipt.read_bytes() + b" "
        receipt.write_bytes(changed)
        self.assert_resume_rejects_without_fetch(retry_failed=True)
        self.assertEqual(receipt.read_bytes(), changed)

    def test_current_raw_corruption_blocks_explicit_failure_retry(self):
        report = self.resolve(ScriptedFetch(response(b"failure", status=503)))
        raw = self.out / report["raw_response"]
        raw.write_bytes(b"modified")
        self.assert_resume_rejects_without_fetch(retry_failed=True)

    def test_malformed_attempt_history_is_rejected_before_fetch(self):
        malformed = (b"not JSON", b"[]", b'{"attempts":"not a list"}', b"{}")
        for body in malformed:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                self.resolve(ScriptedFetch(response(b"failure", status=503)))
                (self.out / "attempts.json").write_bytes(body)
                self.assert_resume_rejects_without_fetch(retry_failed=True)

    def test_history_missing_hash_or_duplicate_receipt_is_rejected(self):
        for alteration in ("missing_hash", "duplicate"):
            with self.subTest(alteration=alteration), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                self.resolve(ScriptedFetch(response(b"failure", status=503)))
                manifest = self.history()
                if alteration == "missing_hash":
                    manifest["attempts"][0].pop("receipt_sha256")
                else:
                    manifest["attempts"].append(dict(manifest["attempts"][0]))
                (self.out / "attempts.json").write_text(json.dumps(manifest))
                self.assert_resume_rejects_without_fetch(retry_failed=True)

    def test_verified_receipt_for_another_query_is_rejected(self):
        report = self.resolve(ScriptedFetch(response(b"failure", status=503)))
        receipt_path = self.out / report["receipt_file"]
        receipt = json.loads(receipt_path.read_text())
        receipt["query_url"] = "https://example.invalid/another-query"
        receipt_path.write_text(json.dumps(receipt))
        manifest = self.history()
        manifest["attempts"][0]["receipt_sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        (self.out / "attempts.json").write_text(json.dumps(manifest))
        self.assert_resume_rejects_without_fetch(retry_failed=True)

    def test_changed_report_cannot_override_verified_receipt(self):
        report = self.resolve(ScriptedFetch(response(b"failure", status=503)))
        report["status"] = "resolved"
        report["candidates"] = [candidate("invented")]
        (self.out / "report.json").write_text(json.dumps(report))
        fetcher = ScriptedFetch()
        try:
            resumed = self.resolve(fetcher, resume=True)
        except (ValueError, OSError, TypeError):
            pass
        else:
            self.assertEqual(resumed["status"], "unavailable")
            self.assertEqual(resumed["candidates"], [])
        self.assertEqual(fetcher.calls, [])


if __name__ == "__main__":
    unittest.main()
