"""Known-answer checks for scientific semantics and checkpoint reuse, no network."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import archive_discovery as archive


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.target = archive.validate_target(0, 0, 5)

    def test_angular_units_and_wrap(self):
        self.assertAlmostEqual(archive.angular_separation(359.999, 0, 0.001, 0), 7.2, places=7)
        self.assertAlmostEqual(archive.angular_separation(0, 0, 0, 1), 3600, places=7)
        for args in [(360, 0, 5), (0, 91, 5), (0, 0, 0), (float('nan'), 0, 5)]:
            with self.assertRaises(ValueError):
                archive.validate_target(*args)

    def test_catalogue_target_is_not_a_detection(self):
        row = dict(sdss_id=9007199254740993, ra_sdss_id=0, dec_sdss_id=0,
                   has_been_observed=False, mjd=None)
        record = archive.normalise("sdss", [row], self.target)[0]
        self.assertEqual(record["record_id"], "9007199254740993")
        self.assertIs(record["has_been_observed"], False)
        self.assertEqual(record["record_kind"], "catalogue_target")
        self.assertIsNone(record["product_url"])
        self.assertIsNone(record["observation_mjd"])
        self.assertEqual(record["association_status"], "candidate_not_verified")

    def test_pointing_match_does_not_claim_footprint(self):
        row = dict(name="test.fits", ra=0.01, dec=0, exposure_time=None,
                   engineering=True, observation_class="acq")
        record = archive.normalise("gemini", [row], self.target)[0]
        self.assertFalse(record["within_requested_cone"])
        self.assertIsNone(record["exposure_seconds"])
        self.assertEqual(record["match_method"], "archive_pointing_cone")
        self.assertTrue(record["engineering"])
        self.assertEqual(record["observation_class"], "acq")

    def test_possible_service_cap_stays_incomplete(self):
        body = json.dumps([dict(name=f"test-{i}.fits") for i in range(500)]).encode()
        with tempfile.TemporaryDirectory() as directory:
            result = archive.search_archive("gemini", self.target, Path(directory), 1, 1000,
                lambda url, timeout: (200, {}, body))
            self.assertEqual(result["retained_count"], 500)
            self.assertEqual(result["status"], "partial")
            self.assertTrue(result["possibly_truncated"])
            self.assertFalse(result["query_complete"])

    def test_checkpoint_resume_preserves_requests_and_raw_bytes(self):
        calls = []
        def fake(url, timeout):
            calls.append(url)
            return 200, {"Content-Type": "application/json"}, b'[]'
        with tempfile.TemporaryDirectory() as directory:
            first = archive.run(self.target, ["gemini", "gtc"], directory, fetcher=fake)
            second = archive.run(self.target, ["gemini", "gtc"], directory, resume=True, fetcher=fake)
            self.assertEqual(len(calls), 1)
            self.assertEqual(first, second)
            self.assertEqual(second["archives"]["gtc"]["status"], "unsupported")
            self.assertEqual(second["archives"]["gemini"]["status"], "no_matches")
            with self.assertRaises(ValueError):
                archive.run(archive.validate_target(1, 0, 5), ["gemini", "gtc"], directory, resume=True)
            raw = Path(directory) / first["archives"]["gemini"]["raw_response"]
            raw.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                archive.run(self.target, ["gemini", "gtc"], directory, resume=True)

    def test_errors_are_not_empty_success(self):
        cases = [(403, b'blocked', 'access_required'), (429, b'limit', 'unavailable'),
                 (200, b'<html>login</html>', 'unavailable'),
                 (200, b'{"unexpected":"schema"}', 'unavailable')]
        with tempfile.TemporaryDirectory() as directory:
            for status, body, expected in cases:
                result = archive.search_archive("gemini", self.target, Path(directory), 1, 5,
                    lambda url, timeout: (status, {}, body))
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["raw_sha256"], hashlib.sha256(body).hexdigest())

    def test_row_and_byte_budgets_are_visible(self):
        body = json.dumps([dict(name="a.fits"), dict(name="b.fits")]).encode()
        with tempfile.TemporaryDirectory() as directory:
            result = archive.search_archive("gemini", self.target, Path(directory), 1, 1,
                lambda url, timeout: (200, {}, body))
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["returned_count"], 2)
            self.assertEqual(result["retained_count"], 1)
            huge = b'x' * (archive.MAX_BYTES + 1)
            result = archive.search_archive("gemini", self.target, Path(directory), 1, 1,
                lambda url, timeout: (200, {}, huge))
            self.assertEqual(result["status"], "partial")
            self.assertFalse(result["query_complete"])

    def test_interrupted_batch_resumes_from_completed_archive(self):
        calls = []
        def interrupted(url, timeout):
            calls.append(url)
            if len(calls) == 2:
                raise KeyboardInterrupt()
            return 200, {}, b'[]'
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyboardInterrupt):
                archive.run(self.target, ["gemini", "sdss"], directory, fetcher=interrupted)
            done = json.loads((Path(directory) / 'report.json').read_text())
            self.assertEqual(list(done['archives']), ['gemini'])
            def finish(url, timeout):
                self.assertIn('api.sdss.org', url)
                return 200, {}, b'[]'
            archive.run(self.target, ["gemini", "sdss"], directory, resume=True, fetcher=finish)


if __name__ == '__main__':
    unittest.main()
