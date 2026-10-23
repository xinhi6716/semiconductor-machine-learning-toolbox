"""Original synthetic workflow tests, not physical-regime or model validation."""
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

EXAMPLE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE))
import build_features as pipeline
import generate_synthetic as generator


class FETFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.csv"
        self.source.write_bytes(generator.fixture_payloads()["transfer.csv"])
        self.options = dict(voltage_unit="V", current_unit="nA", criterion_current="50",
                            criterion_current_unit="nA", current_polarity="positive", max_bracket_span="0.1",
                            window_min="0.1", window_max="0.4", assumptions_accepted=True)

    def build(self, **options):
        return pipeline.build(self.source, **{**self.options, **options})

    def rows(self):
        return list(csv.DictReader(io.StringIO(self.source.read_text())))

    def replace(self, rows):
        self.source.write_bytes(pipeline.csv_bytes(pipeline.vth.COLUMNS, rows))

    def csv(self, payload, name="features.csv"):
        return list(csv.DictReader(io.StringIO(payload[name].decode())))

    def one_branch(self):
        rows = self.rows()[:7]
        self.replace(rows)
        return rows

    def assert_audit_only(self, outputs):
        for name in ("features.csv", "predictors.csv", "feature_schema.json"):
            self.assertNotIn(name, outputs)
        status = json.loads(outputs["workflow_status.json"])
        self.assertEqual(status["status"], "audit_only")
        self.assertFalse(status["feature_exported"])
        self.assertEqual(status["feature_row_count"], 0)
        self.assertEqual(outputs["transfer_input.csv"], self.source.read_bytes())
        for lineage in json.loads(outputs["provenance.json"])["acquisition_lineage"]:
            self.assertIsNone(lineage["feature_csv_source_row"])
        return status

    def test_fixture_regeneration_byte_for_byte(self):
        self.assertEqual(generator.fixture_payloads()["transfer.csv"], (EXAMPLE / "fixtures/transfer.csv").read_bytes())
        self.assertEqual(generator.fixture_payloads(), generator.fixture_payloads())

    def test_sixteen_branches_four_namespaced_devices(self):
        outputs = self.build()
        rows = self.csv(outputs)
        self.assertEqual(len(rows), 16)
        self.assertEqual(len({tuple(row[name] for name in pipeline.GROUP_COLUMNS[:3]) for row in rows}), 4)
        self.assertEqual(len({row["device"] for row in rows}), 2)  # Device labels alone are not identities.
        self.assertTrue(all(row["branch_sample_count"] == "7" and row["log_selected_sample_count"] == "4" for row in rows))
        self.assertAlmostEqual(float(rows[0][pipeline.FEATURE_COLUMNS[0]]), 0.1 + 0.1 * 40 / 90)
        self.assertAlmostEqual(float(rows[1][pipeline.FEATURE_COLUMNS[0]]), 0.1 + 0.1 * 30 / 180)
        self.assertEqual(float(rows[0][pipeline.FEATURE_COLUMNS[1]]), 10)
        self.assertAlmostEqual(float(rows[4][pipeline.FEATURE_COLUMNS[1]]), 10 * math.log10(4))
        self.assertEqual(outputs, self.build())

    def test_calls_both_tools_and_preserves_full_reports(self):
        with patch.object(pipeline.vth, "evaluate", wraps=pipeline.vth.evaluate) as threshold:
            with patch.object(pipeline.log_slope, "evaluate", wraps=pipeline.log_slope.evaluate) as logarithmic:
                outputs = self.build()
        self.assertEqual(threshold.call_count, 1)
        self.assertEqual(logarithmic.call_count, 1)
        expected_vth = pipeline.vth.evaluate(self.source, "V", "nA", "50", "nA", "positive", True, None, "0.1")
        expected_log = pipeline.log_slope.evaluate(self.source, "V", "nA", "0.1", "0.4", "positive", True)
        self.assertEqual(json.loads(outputs["vth_report.json"]), expected_vth)
        self.assertEqual(json.loads(outputs["log_slope_report.json"]), expected_log)
        self.assertEqual(expected_log["counts"]["outside_window_rows"], 48)
        self.assertEqual(expected_log["counts"]["fitted_rows"], 64)

    def test_exact_seven_field_calculator_manifest(self):
        outputs = self.build()
        schema = json.loads(outputs["feature_schema.json"])
        self.assertEqual(set(schema), {"schema_version", "row_key", "model_feature_allowlist", "metadata_columns", "target_columns", "feature_units", "warnings"})
        self.assertEqual(schema["model_feature_allowlist"], list(pipeline.FEATURE_COLUMNS))
        self.assertEqual(schema["target_columns"], [])
        self.assertEqual(schema["row_key"], list(pipeline.GROUP_COLUMNS))
        self.assertFalse(set(schema["model_feature_allowlist"]) & set(schema["metadata_columns"]))
        self.assertEqual(set(schema["metadata_columns"]) | set(schema["model_feature_allowlist"]), set(pipeline.OUTPUT_COLUMNS))
        self.assertIn("Never use a target or its algebraic proxy", " ".join(schema["warnings"]))
        numeric = self.csv(outputs, "predictors.csv")
        self.assertEqual(list(numeric[0]), list(pipeline.FEATURE_COLUMNS))
        for full, predictor in zip(self.csv(outputs), numeric):
            self.assertEqual(predictor, {name: full[name] for name in pipeline.FEATURE_COLUMNS})
            self.assertTrue(all(math.isfinite(float(value)) for value in predictor.values()))

    def test_input_and_output_hashes(self):
        outputs = self.build()
        provenance = json.loads(outputs["provenance.json"])
        self.assertEqual(provenance["input_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(outputs["transfer_input.csv"], self.source.read_bytes())
        for name, expected in provenance["output_sha256"].items():
            self.assertEqual(hashlib.sha256(outputs[name]).hexdigest(), expected)
        for name, expected in provenance["source_sha256"].items():
            self.assertEqual(hashlib.sha256((pipeline.ROOT / name).read_bytes()).hexdigest(), expected)

    def test_row_lineage_and_reverse_order(self):
        outputs = self.build()
        raw, features = self.rows(), self.csv(outputs)
        for item in json.loads(outputs["provenance.json"])["acquisition_lineage"]:
            row = features[item["feature_csv_source_row"] - 2]
            self.assertEqual(item["feature_csv_source_row"], item["predictor_csv_source_row"])
            for field in pipeline.GROUP_COLUMNS:
                self.assertEqual(row[field], item[field])
                self.assertTrue(all(raw[index - 2][field] == item[field] for index in item["transfer_source_rows"]))
            self.assertEqual(set(item["log_selected_source_rows"]) | set(item["log_outside_window_source_rows"]), set(item["transfer_source_rows"]))
        reverse = json.loads(outputs["log_slope_report.json"])["results"][1]
        self.assertEqual(reverse["acquisition_direction"], "decreasing_vgs")
        self.assertEqual([o["gate_source_voltage_V"] for o in reverse["observations"]], [0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0])

    def test_snapshot_protects_both_tools_from_source_change(self):
        before = self.source.read_bytes()
        original = pipeline.vth.evaluate
        def mutate_source(*args, **kwargs):
            self.source.write_bytes(b"source changed after snapshot\n")
            return original(*args, **kwargs)
        with patch.object(pipeline.vth, "evaluate", side_effect=mutate_source):
            outputs = self.build()
        self.assertEqual(outputs["transfer_input.csv"], before)
        self.assertEqual(json.loads(outputs["vth_report.json"])["provenance"]["input_sha256"], json.loads(outputs["log_slope_report.json"])["provenance"]["input_sha256"])

    def test_interleaving_preserves_each_branch_order(self):
        rows = self.rows()
        groups = [rows[index:index + 7] for index in range(0, len(rows), 7)]
        expected = self.csv(self.build())
        self.replace([group[index] for index in range(7) for group in groups])
        self.assertEqual(self.csv(self.build()), expected)

    def test_negative_current_preserves_signed_criterion(self):
        rows = self.rows()
        for row in rows:
            row["drain_current"] = "-" + row["drain_current"]
        self.replace(rows)
        outputs = self.build(current_polarity="negative", criterion_current="-50")
        first = self.csv(outputs)[0]
        self.assertEqual(float(first["signed_current_criterion_A"]), -5e-8)
        self.assertEqual(float(first[pipeline.FEATURE_COLUMNS[1]]), 10)
        self.assertAlmostEqual(float(first[pipeline.FEATURE_COLUMNS[0]]), 0.1 + 0.1 * 40 / 90)

    def test_negative_log_slope_is_not_absolute_value(self):
        rows = self.one_branch()
        currents = [row["drain_current"] for row in rows][::-1]
        for row, current in zip(rows, currents):
            row["drain_current"] = current
        self.replace(rows)
        self.assertEqual(float(self.csv(self.build())[0][pipeline.FEATURE_COLUMNS[1]]), -10)

    def test_zero_log_slope_remains_valid_zero_not_missing(self):
        rows = self.one_branch()
        for row in rows[1:5]:
            row["drain_current"] = "10"
        self.replace(rows)
        result = self.build()
        self.assertEqual(float(self.csv(result)[0][pipeline.FEATURE_COLUMNS[1]]), 0)
        self.assertIsNone(json.loads(result["log_slope_report.json"])["results"][0]["reciprocal_slope"])

    def test_exact_hit_is_not_interpolation(self):
        self.one_branch()
        row = self.csv(self.build(criterion_current="100"))[0]
        self.assertEqual(row["criterion_method"], "measured_exact_criterion")
        self.assertEqual(float(row["criterion_bracket_span_V"]), 0)
        self.assertEqual(float(row[pipeline.FEATURE_COLUMNS[0]]), 0.2)

    def test_units_and_criterion_unit_are_explicitly_converted(self):
        expected = self.csv(self.build())
        rows = self.rows()
        from decimal import Decimal
        for row in rows:
            for field in ("gate_voltage", "drain_voltage", "body_voltage"):
                row[field] = str(Decimal(row[field]) * 1000)
            row["drain_current"] = str(Decimal(row["drain_current"]) / 1000)
        self.replace(rows)
        actual = self.csv(self.build(voltage_unit="mV", current_unit="uA", criterion_current="0.05", criterion_current_unit="uA", max_bracket_span="100", window_min="100", window_max="400"))
        self.assertEqual(actual, expected)

    def test_decimal_window_boundary_not_float_rounded(self):
        self.one_branch()
        output = self.build(window_min="0.10000000000000000000000000001")
        row = self.csv(output)[0]
        self.assertEqual(row["log_selected_sample_count"], "3")
        selected = json.loads(output["log_slope_report.json"])["results"][0]["selected_source_rows"]
        self.assertEqual(selected, [4, 5, 6])

    def test_vth_rejection_log_success_preserves_both_statuses(self):
        rows = self.rows()
        rows[0]["drain_current"] = "-1"  # Outside the chosen log window, but forbidden by Vth.
        self.replace(rows)
        outputs = self.build()
        status = self.assert_audit_only(outputs)
        self.assertEqual(status["branch_count"], 16)
        self.assertEqual(status["branch_statuses"][0]["vth_status"], "rejected")
        self.assertEqual(status["branch_statuses"][0]["log_slope_status"], "ok")
        self.assertEqual(len(json.loads(outputs["vth_report.json"])["results"]), 16)
        self.assertEqual(len(json.loads(outputs["log_slope_report.json"])["results"]), 16)

    def test_vth_success_log_rejection_preserves_all_observations(self):
        rows = self.one_branch()
        rows[0]["drain_current"] = "0"
        rows[1]["drain_current"] = "0"
        self.replace(rows)
        outputs = self.build()
        status = self.assert_audit_only(outputs)
        self.assertEqual(status["branch_statuses"][0]["vth_status"], "ok")
        self.assertEqual(status["branch_statuses"][0]["log_slope_reason"], "zero_current_in_user_window")
        log = json.loads(outputs["log_slope_report.json"])["results"][0]
        self.assertEqual(len(log["observations"]), 7)
        self.assertFalse(any(row["used_in_fit"] for row in log["observations"]))

    def test_nonmonotonic_current_is_not_trimmed_to_fill_features(self):
        rows = self.one_branch()
        rows[5]["drain_current"] = "9000"
        self.replace(rows)
        status = self.assert_audit_only(self.build())
        self.assertEqual(status["branch_statuses"][0]["vth_reason"], "nonmonotonic_drain_current")

    def test_absent_crossing_does_not_extrapolate(self):
        self.assert_audit_only(self.build(criterion_current="1e10"))

    def test_too_wide_bracket_keeps_rejection(self):
        self.assert_audit_only(self.build(max_bracket_span="0.099"))

    def test_too_few_log_points_does_not_extend_window(self):
        self.assert_audit_only(self.build(window_min="0.2", window_max="0.3"))

    def test_duplicate_voltage_rejects_without_deduplication(self):
        rows = self.one_branch()
        rows[2]["gate_voltage"] = rows[1]["gate_voltage"]
        self.replace(rows)
        self.assert_audit_only(self.build())

    def test_changed_bias_outside_window_rejects_both(self):
        rows = self.one_branch()
        rows[0]["drain_voltage"] = "0.06"
        self.replace(rows)
        status = self.assert_audit_only(self.build())
        self.assertEqual(status["branch_statuses"][0]["vth_status"], "rejected")
        self.assertEqual(status["branch_statuses"][0]["log_slope_status"], "rejected")

    def test_formula_prefixes_rejected_for_csv_export(self):
        for prefix in "=+-@":
            with self.subTest(prefix=prefix):
                rows = self.rows()
                for row in rows:
                    row["device"] = prefix + "private_identifier_" + row["device"].lstrip("=+-@")
                self.replace(rows)
                with self.assertRaises(pipeline.InputError) as caught:
                    self.build()
                self.assertNotIn("private_identifier", str(caught.exception))

    def test_csv_quoted_identifiers_have_lossless_tuple_keys(self):
        rows = self.rows()
        for row in rows:
            row["lot"] += ",quoted|part"
            row["condition"] = 'protocol"quoted'
        self.replace(rows)
        out = self.csv(self.build())
        self.assertEqual(out[0]["lot"], rows[0]["lot"])
        self.assertEqual(out[0]["condition"], rows[0]["condition"])
        self.assertEqual(len(out), 16)

    def test_bom_blank_lines_and_reordered_header_preserved(self):
        rows = self.rows()
        raw = b"\xef\xbb\xbf" + pipeline.csv_bytes(tuple(reversed(pipeline.vth.COLUMNS)), rows)
        raw = raw.replace(b"\n", b"\n\n", 1)
        self.source.write_bytes(raw)
        outputs = self.build()
        self.assertEqual(outputs["transfer_input.csv"], raw)
        self.assertEqual(json.loads(outputs["log_slope_report.json"])["results"][0]["source_rows"][0], 3)

    def test_invalid_arguments_do_not_infer_assumptions_or_bounds(self):
        for change in ({"assumptions_accepted": False}, {"max_bracket_span": None}, {"max_bracket_span": "0"},
                       {"window_min": "0.4", "window_max": "0.1"}, {"criterion_current": "0"},
                       {"criterion_current": "-50"}, {"current_unit": "unknown"}, {"window_min": "NaN"}):
            with self.subTest(change=change), self.assertRaises(pipeline.InputError):
                self.build(**change)

    def test_malformed_extra_column_invalid_numeric_fail_fatally(self):
        for data in (b"bad header\n", generator.fixture_payloads()["transfer.csv"].replace(b"300,", b"NaN,", 1),
                     generator.fixture_payloads()["transfer.csv"].replace(b"drain_current\n", b"drain_current,target\n", 1)):
            self.source.write_bytes(data)
            with self.assertRaises(pipeline.InputError):
                self.build()

    def test_size_limit_and_special_files(self):
        with patch.object(pipeline.vth, "MAX_BYTES", 1), self.assertRaises(pipeline.InputError):
            self.build()
        if hasattr(os, "mkfifo"):
            fifo = self.root / "pipe"
            os.mkfifo(fifo)
            with self.assertRaises(pipeline.InputError):
                pipeline.build(fifo, **self.options)
        with self.assertRaises(pipeline.InputError):
            pipeline.build(self.root, **self.options)

    def test_extractor_schema_and_source_change_fail_closed(self):
        with patch.object(pipeline.log_slope, "COLUMNS", ("wrong",)), self.assertRaises(pipeline.InputError):
            self.build()
        with patch.dict(pipeline.LOADED_TOOL_HASHES, {pipeline.TOOL_PATHS["vth"]: "wrong"}), self.assertRaises(pipeline.InputError):
            self.build()

    def test_report_key_mismatch_and_duplicate_fail_closed(self):
        original = pipeline.log_slope.evaluate
        for kind in ("mismatch", "duplicate", "hash", "schema"):
            def corrupt(*args, **kwargs):
                report = original(*args, **kwargs)
                if kind == "mismatch":
                    report["results"][0]["branch"] = "different"
                elif kind == "duplicate":
                    report["results"].append(report["results"][0])
                elif kind == "hash":
                    report["provenance"]["input_sha256"] = "wrong"
                else:
                    report["schema_version"] = 2
                return report
            with self.subTest(kind=kind), patch.object(pipeline.log_slope, "evaluate", side_effect=corrupt), self.assertRaises(pipeline.InputError):
                self.build()

    def test_no_overwrite_and_private_permissions(self):
        outputs = self.build()
        destination = self.root / "output"
        pipeline.write_outputs(destination, outputs)
        with self.assertRaises(FileExistsError):
            pipeline.write_outputs(destination, outputs)
        self.assertEqual(self.source.read_bytes(), generator.fixture_payloads()["transfer.csv"])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode) & 0o077, 0)
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0 for path in destination.iterdir()))

    def test_live_and_dangling_symlink_output_never_overwritten(self):
        outputs = self.build()
        for target in (self.source, self.root / "missing"):
            link = self.root / ("link_" + target.name)
            link.symlink_to(target)
            with self.assertRaises(FileExistsError):
                pipeline.write_outputs(link, outputs)
            self.assertTrue(link.is_symlink())

    def cli_args(self):
        return [str(self.source), "--output-dir", str(self.root / "cli"), "--voltage-unit", "V", "--current-unit", "nA",
                "--criterion-current", "50", "--criterion-current-unit", "nA", "--current-polarity", "positive",
                "--max-bracket-span", "0.1", "--window-min", "0.1", "--window-max", "0.4", "--accept-measurement-assumptions"]

    def test_cli_complete_exit_zero_no_identifiers_in_summary(self):
        result = subprocess.run([sys.executable, str(EXAMPLE / "build_features.py"), *self.cli_args()], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("invented_device", result.stdout + result.stderr)
        self.assertTrue((self.root / "cli/features.csv").exists())

    def test_cli_audit_only_exit_one_no_partial_table(self):
        rows = self.rows()
        rows[0]["drain_current"] = "-1"
        self.replace(rows)
        result = subprocess.run([sys.executable, str(EXAMPLE / "build_features.py"), *self.cli_args()], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((self.root / "cli/features.csv").exists())
        self.assertTrue((self.root / "cli/vth_report.json").exists())
        self.assertNotIn("invented_device", result.stdout + result.stderr)

    def test_cli_fatal_validation_exit_two_no_output(self):
        self.source.write_bytes(b"private secret invalid header\n")
        result = subprocess.run([sys.executable, str(EXAMPLE / "build_features.py"), *self.cli_args()], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "cli").exists())
        self.assertNotIn("private secret", result.stdout + result.stderr)
        self.assertNotIn(str(self.source), result.stdout + result.stderr)

    def test_cli_missing_flags_exit_two_and_no_raw_values(self):
        result = subprocess.run([sys.executable, str(EXAMPLE / "build_features.py"), "private-path"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("private-path", result.stderr)


if __name__ == "__main__":
    unittest.main()
