"""Offline tests for a lossless record and archive-status CSV export."""
import copy
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import inventory_export


def observation_report(archives):
    return dict(request=dict(archives=list(archives), target=dict(ra_deg=1, dec_deg=2)),
                archives=archives)


def archive_result(status="matches", records=None, **fields):
    result = dict(status=status, query_complete=status in ("matches", "no_matches"),
                  records=[] if records is None else records,
                  query_url="https://example.invalid/search?ra=1&dec=2",
                  coverage="Only the requested cone", warnings=[])
    result.update(fields)
    return result


class InventoryExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.out = Path(self.directory.name)

    def export(self, report, **options):
        inventory_export.write_inventory(report, self.out, **options)
        return json.loads((self.out / "export.json").read_text())

    def read_csv(self, name):
        with (self.out / name).open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            return reader.fieldnames, list(reader)

    def snapshot(self):
        return {name: (self.out / name).read_bytes()
                for name in ("records.csv", "archives.csv", "export.json")}

    def assert_boolean(self, cell, expected):
        self.assertEqual(cell.lower(), "true" if expected else "false")

    def test_unknown_filter_footprint_and_access_fields_are_preserved(self):
        row = dict(archive="mast", record_id="observation-1", record_kind="observation",
                   filter_name="F275W", footprint="POLYGON ICRS 1 2 3 4 5 6",
                   access_status="proprietary", release_at="2027-04-12",
                   provider_extension=dict(exposure=[12, 34], unit="s"))
        manifest = self.export(observation_report(dict(mast=archive_result(records=[row]))))
        columns, rows = self.read_csv("records.csv")
        self.assertEqual(len(rows), 1)
        for field in ("filter_name", "footprint", "access_status", "release_at"):
            self.assertIn(field, columns)
            self.assertEqual(rows[0][field], row[field])
        self.assertEqual(json.loads(rows[0]["provider_extension"]), row["provider_extension"])
        self.assertEqual(manifest["record_count"], 1)

    def test_nested_arrays_nulls_booleans_and_large_identifiers(self):
        exact_id = 9007199254740993
        row = dict(archive="mast", record_id=str(exact_id), numeric_id=exact_id,
                   empty_value=None, false_value=False, numeric_zero=0,
                   modes=["SCIENCE", None, {"order": 12}],
                   units=dict(wavelength="Å", missing=None), empty_array=[], empty_object={})
        self.export(observation_report(dict(mast=archive_result(records=[row]))))
        _, rows = self.read_csv("records.csv")
        exported = rows[0]
        self.assertEqual(exported["record_id"], "9007199254740993")
        self.assertEqual(exported["numeric_id"], "9007199254740993")
        self.assertEqual(exported["empty_value"], "")
        self.assert_boolean(exported["false_value"], False)
        self.assertEqual(exported["numeric_zero"], "0")
        for field in ("modes", "units", "empty_array", "empty_object"):
            self.assertEqual(json.loads(exported[field]), row[field])

    def test_csv_quotes_unicode_commas_and_newlines_without_losing_text(self):
        description = 'A "raw", ultraviolet exposure\nwith a second line: λ = 1500 Å'
        row = dict(archive="mast", record_id="a", description=description)
        self.export(observation_report(dict(mast=archive_result(records=[row]))))
        _, rows = self.read_csv("records.csv")
        self.assertEqual(rows[0]["description"], description)

    def test_union_of_fields_from_every_archive_is_retained(self):
        report = observation_report(dict(
            mast=archive_result(records=[dict(archive="mast", record_id="m", filter_name="NUV")]),
            eso=archive_result(records=[dict(archive="eso", record_id="e", slit_width_arcsec=0.9)])))
        self.export(report)
        columns, rows = self.read_csv("records.csv")
        self.assertIn("filter_name", columns)
        self.assertIn("slit_width_arcsec", columns)
        by_archive = {row["archive"]: row for row in rows}
        self.assertEqual(by_archive["mast"]["filter_name"], "NUV")
        self.assertEqual(by_archive["mast"]["slit_width_arcsec"], "")
        self.assertEqual(by_archive["eso"]["filter_name"], "")
        self.assertEqual(by_archive["eso"]["slit_width_arcsec"], "0.9")

    def test_query_context_is_attached_to_each_record(self):
        report = observation_report(dict(
            mast=archive_result("partial", records=[dict(archive="mast", record_id="m")],
                                query_complete=False, coverage="Two of three pages"),
            eso=archive_result(records=[dict(archive="eso", record_id="e")],
                               query_url="https://example.invalid/tap", coverage="ESO public table")))
        self.export(report)
        columns, rows = self.read_csv("records.csv")
        for column in ("query_status", "query_complete", "query_url", "query_coverage"):
            self.assertIn(column, columns)
        by_archive = {row["archive"]: row for row in rows}
        self.assertEqual(by_archive["mast"]["query_status"], "partial")
        self.assert_boolean(by_archive["mast"]["query_complete"], False)
        self.assertEqual(by_archive["mast"]["query_coverage"], "Two of three pages")
        self.assertEqual(by_archive["eso"]["query_status"], "matches")
        self.assert_boolean(by_archive["eso"]["query_complete"], True)
        self.assertEqual(by_archive["eso"]["query_url"], "https://example.invalid/tap")

    def test_empty_archives_keep_distinct_status_and_dynamic_metadata(self):
        statuses = dict(mast="partial", eso="unavailable", gemini="no_matches", keck="unsupported")
        archives = {name: archive_result(status, error="service unavailable" if name == "eso" else None,
                                         arbitrary_provider_state=dict(release="R1", count=0))
                    for name, status in statuses.items()}
        manifest = self.export(observation_report(archives))
        _, rows = self.read_csv("archives.csv")
        by_archive = {row["archive"]: row for row in rows}
        self.assertEqual(set(by_archive), set(statuses))
        for name, status in statuses.items():
            self.assertEqual(by_archive[name]["status"], status)
            self.assert_boolean(by_archive[name]["query_complete"], status == "no_matches")
            self.assertEqual(json.loads(by_archive[name]["arbitrary_provider_state"]),
                             dict(release="R1", count=0))
        self.assertEqual(by_archive["eso"]["error"], "service unavailable")
        self.assertEqual(manifest["archive_count"], 4)
        self.assertEqual(manifest["record_count"], 0)
        _, records = self.read_csv("records.csv")
        self.assertEqual(records, [])

    def test_archive_warnings_receipts_and_nonrecord_fields_are_preserved(self):
        warnings = ["No footprint check", "Further pages remain"]
        receipts = [dict(receipt="page-1.json", receipt_sha256="a" * 64)]
        result = archive_result("partial", warnings=warnings, page_receipts=receipts,
                                retained_count=2, service_total_rows=10, next_page=2,
                                provider_limits=dict(bytes=4096, rows=500), optional=None)
        self.export(observation_report(dict(mast=result)))
        columns, rows = self.read_csv("archives.csv")
        self.assertNotIn("records", columns)
        self.assertEqual(json.loads(rows[0]["warnings"]), warnings)
        self.assertEqual(json.loads(rows[0]["page_receipts"]), receipts)
        self.assertEqual(json.loads(rows[0]["provider_limits"]), result["provider_limits"])
        self.assertEqual(rows[0]["service_total_rows"], "10")
        self.assertEqual(rows[0]["next_page"], "2")
        self.assertEqual(rows[0]["optional"], "")

    def test_product_report_retains_parent_relationship_and_query_context(self):
        parent = "9007199254740993"
        row = dict(archive="mast", record_kind="product", record_id="mast:HST/product/a.fits",
                   parent_query_obsid=parent, product_observation_id="hst-a",
                   filename="a.fits", size_bytes=9007199254740995,
                   product_uri="mast:HST/product/a.fits", calibration_level=2)
        report = dict(parent_obsid=parent, status="partial", query_complete=False,
                      query_url="https://example.invalid/products?obsid=" + parent,
                      coverage="Associated products", records=[row], warnings=["Association unverified"])
        manifest = self.export(report)
        _, rows = self.read_csv("records.csv")
        self.assertEqual(rows[0]["archive"], "mast")
        self.assertEqual(rows[0]["record_kind"], "product")
        self.assertEqual(rows[0]["parent_query_obsid"], parent)
        self.assertEqual(rows[0]["product_observation_id"], "hst-a")
        self.assertEqual(rows[0]["size_bytes"], "9007199254740995")
        self.assertEqual(rows[0]["query_status"], "partial")
        _, archives = self.read_csv("archives.csv")
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0]["archive"], "mast")
        self.assertEqual(archives[0]["parent_obsid"], parent)
        self.assertEqual(manifest["record_count"], 1)
        self.assertEqual(manifest["archive_count"], 1)

    def test_manifest_hashes_and_byte_counts_match_both_csv_files(self):
        report = observation_report(dict(mast=archive_result(records=[dict(archive="mast", record_id="a")]),
                                         eso=archive_result("no_matches")))
        manifest = self.export(report)
        self.assertEqual(manifest["record_count"], 1)
        self.assertEqual(manifest["archive_count"], 2)
        for name in ("records.csv", "archives.csv"):
            body = (self.out / name).read_bytes()
            self.assertEqual(manifest["files"][name]["sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual(manifest["files"][name]["bytes"], len(body))

    def test_source_report_hash_uses_exact_bytes(self):
        report = observation_report(dict(mast=archive_result("no_matches", note="λ")))
        source = self.out / "original-report.json"
        body = json.dumps(report, indent=3, ensure_ascii=False).encode() + b"\n\n"
        source.write_bytes(body)
        manifest = self.export(report, source_report_path=source)
        self.assertEqual(manifest["source_report"]["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(Path(manifest["source_report"]["path"]).resolve(), source.resolve())
        self.assertEqual(source.read_bytes(), body)

    def test_export_does_not_modify_report(self):
        report = observation_report(dict(mast=archive_result(records=[dict(archive="mast", record_id="m",
                                                                         fields={"list": [1, None]})])))
        before = copy.deepcopy(report)
        self.export(report)
        self.assertEqual(report, before)

    def test_archive_status_must_be_present_and_recognised(self):
        for result in (dict(records=[]), dict(status="unknown", records=[])):
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    self.export(observation_report(dict(mast=result)))

    def test_request_schema_and_selected_archives_must_agree(self):
        for request in ([], None, dict(archives=["mast", "eso"]), dict(archives=["mast", "mast"])):
            with self.subTest(request=request):
                report = dict(request=request, archives=dict(mast=archive_result("no_matches")))
                with self.assertRaises((ValueError, TypeError)):
                    self.export(report)

    def test_source_mismatch_preserves_existing_exports(self):
        report = observation_report(dict(mast=archive_result("no_matches")))
        self.export(report)
        before = self.snapshot()
        source = self.out / "other-report.json"
        source.write_text(json.dumps(observation_report(dict(eso=archive_result("no_matches")))))
        with self.assertRaises(ValueError):
            self.export(report, source_report_path=source)
        self.assertEqual(self.snapshot(), before)

    def test_source_report_cannot_be_overwritten_by_an_export_file(self):
        report = observation_report(dict(mast=archive_result("no_matches")))
        for name in ("records.csv", "archives.csv", "export.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                self.out = Path(directory)
                source = self.out / name
                body = json.dumps(report).encode()
                source.write_bytes(body)
                with self.assertRaises(ValueError):
                    self.export(report, source_report_path=source)
                self.assertEqual(source.read_bytes(), body)

    def test_nonfinite_metadata_is_rejected_before_replacing_exports(self):
        self.export(observation_report(dict(mast=archive_result("no_matches"))))
        before = self.snapshot()
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                row = dict(archive="mast", record_id="a", invalid_value=value)
                with self.assertRaises(ValueError):
                    self.export(observation_report(dict(mast=archive_result(records=[row]))))
                self.assertEqual(self.snapshot(), before)

    def test_record_archive_disagreement_is_rejected(self):
        report = observation_report(dict(mast=archive_result(records=[dict(archive="eso", record_id="a")])))
        with self.assertRaises(ValueError):
            self.export(report)

    def test_result_archive_disagreement_is_rejected(self):
        report = observation_report(dict(mast=archive_result("no_matches", archive="eso")))
        with self.assertRaises(ValueError):
            self.export(report)

    def test_product_record_cannot_claim_a_different_archive(self):
        report = dict(parent_obsid="123", status="matches", query_complete=True,
                      records=[dict(archive="eso", record_kind="product", record_id="a")])
        with self.assertRaises(ValueError):
            self.export(report)

    def test_added_query_context_collisions_are_rejected(self):
        for field in ("query_status", "query_complete", "query_url", "query_coverage"):
            with self.subTest(field=field):
                row = dict(archive="mast", record_id="a", **{field: "original record value"})
                with self.assertRaises(ValueError):
                    self.export(observation_report(dict(mast=archive_result(records=[row]))))

    def test_collision_failure_preserves_prior_exports(self):
        self.export(observation_report(dict(mast=archive_result("no_matches"))))
        before = self.snapshot()
        bad = observation_report(dict(mast=archive_result(records=[dict(archive="mast", record_id="a",
                                                                       query_status="saved provider value")])))
        with self.assertRaises(ValueError):
            self.export(bad)
        self.assertEqual(self.snapshot(), before)

    def test_malformed_report_result_and_record_shapes_are_rejected(self):
        bad_reports = (
            [], {}, dict(archives=[]), dict(archives=dict(mast=[])),
            dict(archives=dict(mast=dict(status="matches", records={}))),
            dict(archives=dict(mast=dict(status="matches", records=[None]))),
            dict(archives=dict(mast=dict(status="matches", records=[[]]))),
            dict(archives=dict(mast=dict(status="matches", records=[{1: "non-string field"}]))))
        for report in bad_reports:
            with self.subTest(report=report):
                with self.assertRaises((ValueError, TypeError)):
                    self.export(report)


if __name__ == "__main__":
    unittest.main()
