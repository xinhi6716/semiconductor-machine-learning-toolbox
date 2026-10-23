"""Original synthetic fixtures test software contracts, not device performance."""
import contextlib
import csv
import hashlib
import io
import json
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


class FeatureWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "input"
        pipeline.write_outputs(self.inputs, generator.fixture_payloads())

    def rows(self, name):
        with (self.inputs / name).open(newline="") as stream:
            return list(csv.DictReader(stream))

    def replace(self, name, rows):
        columns = {"manifest.csv": pipeline.MANIFEST_COLUMNS,
                   "conditions.csv": pipeline.CONDITION_COLUMNS,
                   "iv_sweeps.csv": pipeline.IV_COLUMNS}[name]
        (self.inputs / name).write_text(pipeline.csv_text(columns, rows))

    def build(self):
        return pipeline.build(self.inputs, max_bracket_span_V="0.11", assumptions_accepted=True)

    def reject(self, fragment=None):
        with self.assertRaises(pipeline.InputError) as caught:
            self.build()
        if fragment:
            self.assertIn(fragment, str(caught.exception))

    def test_fixture_generator_byte_for_byte(self):
        for name, text in generator.fixture_payloads().items():
            self.assertEqual(text.encode(), (EXAMPLE / "fixtures" / name).read_bytes())
        self.assertEqual(generator.fixture_payloads(), generator.fixture_payloads())

    def test_end_to_end_six_devices_not_eighteen_cycles(self):
        result = self.build()
        rows = list(csv.DictReader(io.StringIO(result["features.csv"])))
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({row["device"] for row in rows}), 6)
        self.assertTrue(all(row["cycle_count"] == "3" for row in rows))
        self.assertEqual(len({row["split_group"] for row in rows}), 2)
        self.assertAlmostEqual(float(rows[0]["hrs_resistance_median_ohm"]), 1e6 / 3)
        self.assertAlmostEqual(float(rows[0]["lrs_resistance_median_ohm"]), 1e6 / 101)
        self.assertAlmostEqual(float(rows[0]["resistance_ratio_median"]), 101 / 3)
        self.assertEqual(json.loads(result["rram_windows.json"])["counts"]["valid_pairs"], 18)
        self.assertEqual(len(json.loads(result["iv_readpoints.json"])["results"]), 36)
        self.assertEqual(result, self.build())

    def test_calls_both_existing_tools_not_duplicate_algorithms(self):
        with patch.object(pipeline.iv, "extract_group", wraps=pipeline.iv.extract_group) as extraction:
            with patch.object(pipeline.rram, "evaluate_stream", wraps=pipeline.rram.evaluate_stream) as pairing:
                self.build()
        self.assertEqual(extraction.call_count, 36)
        self.assertEqual(pairing.call_count, 1)

    def test_input_hashes_and_row_lineage(self):
        outputs = self.build()
        provenance = json.loads(outputs["provenance.json"])
        for name in pipeline.INPUT_FILES:
            self.assertEqual(provenance["inputs_sha256"][name], hashlib.sha256((self.inputs / name).read_bytes()).hexdigest())
        self.assertEqual(provenance["condition_declarations"][0]["temperature_K"], "300")
        self.assertEqual(provenance["condition_declarations"][0]["read_delay_ms"], "10")
        self.assertEqual(provenance["condition_declarations"][0]["protocol"], "invented_fixed_state_dc_read")
        raw = self.rows("iv_sweeps.csv")
        converted = list(csv.DictReader(io.StringIO(outputs["resistance_input.csv"])))
        for item in provenance["acquisition_lineage"]:
            for source_row in item["iv_source_rows"]:
                original = raw[source_row - 2]
                for field in ("device", "cycle", "branch"):
                    self.assertEqual(original[field], item[field])
            self.assertEqual(converted[item["resistance_input_source_row"] - 2]["state"], item["state"])

    def test_feature_allowlist_excludes_identifiers_and_no_target(self):
        schema = json.loads(self.build()["feature_schema.json"])
        self.assertEqual(schema["model_feature_allowlist"], list(pipeline.FEATURE_COLUMNS))
        self.assertEqual(schema["target_columns"], [])
        self.assertTrue(set(schema["metadata_columns"]).isdisjoint(schema["model_feature_allowlist"]))

    def test_duplicate_manifest_rejected_not_deduplicated(self):
        rows = self.rows("manifest.csv")
        self.replace("manifest.csv", rows + [rows[0]])
        self.reject("duplicate manifest")

    def test_duplicate_measured_point_rejected_not_deduplicated(self):
        rows = self.rows("iv_sweeps.csv")
        self.replace("iv_sweeps.csv", rows + [rows[0]])
        self.reject("I-V extraction")

    def test_missing_measurement_group(self):
        rows = self.rows("iv_sweeps.csv")
        key = tuple(rows[0][name] for name in pipeline.IV_COLUMNS[:3])
        self.replace("iv_sweeps.csv", [row for row in rows if tuple(row[name] for name in pipeline.IV_COLUMNS[:3]) != key])
        self.reject("keys must match exactly")

    def test_extra_measurement_group(self):
        rows = self.rows("iv_sweeps.csv")
        extra = dict(rows[0], branch="unplanned_branch")
        self.replace("iv_sweeps.csv", rows + [extra])
        self.reject("keys must match exactly")

    def test_missing_manifest_state(self):
        rows = self.rows("manifest.csv")
        self.replace("manifest.csv", rows[1:])
        self.reject("one HRS and one LRS")

    def test_duplicate_state_on_different_branch(self):
        rows = self.rows("manifest.csv")
        rows[1]["state"] = rows[0]["state"]
        self.replace("manifest.csv", rows)
        self.reject("duplicate state")

    def test_state_labels_are_not_inferred_from_branch_names(self):
        rows = self.rows("manifest.csv")
        for row in rows:
            row["state"] = "LRS" if row["state"] == "HRS" else "HRS"
        self.replace("manifest.csv", rows)
        features = list(csv.DictReader(io.StringIO(self.build()["features.csv"])))
        self.assertTrue(all(float(row["resistance_ratio_median"]) < 1 for row in features))
        self.assertTrue(all(row["ratio_below_one_cycle_count"] == "3" for row in features))

    def test_condition_mismatch_between_states(self):
        conditions = self.rows("conditions.csv")
        conditions.append(dict(conditions[0], condition="different_protocol", read_delay_ms="11"))
        self.replace("conditions.csv", conditions)
        rows = self.rows("manifest.csv")
        rows[1]["condition"] = "different_protocol"
        self.replace("manifest.csv", rows)
        self.reject("condition changes")

    def test_condition_changes_between_cycles(self):
        conditions = self.rows("conditions.csv")
        conditions.append(dict(conditions[0], condition="different_bias", read_voltage_V="0.15"))
        self.replace("conditions.csv", conditions)
        rows = self.rows("manifest.csv")
        rows[2]["condition"] = rows[3]["condition"] = "different_bias"
        self.replace("manifest.csv", rows)
        self.reject("condition changes")

    def test_unknown_unused_and_duplicate_conditions(self):
        original = self.rows("conditions.csv")
        self.replace("conditions.csv", original + [dict(original[0], condition="unused")])
        self.reject("unused condition")
        self.replace("conditions.csv", original + original)
        self.reject("duplicate condition")
        self.replace("conditions.csv", [dict(original[0], condition="not_referenced")])
        self.reject("no declaration")

    def test_same_device_cannot_be_redeclared_in_new_batch(self):
        rows = self.rows("manifest.csv")
        rows[2]["batch"] = "different_batch"
        self.replace("manifest.csv", rows)
        self.reject("device identity")

    def test_repeat_keys_are_not_hidden_extra_devices(self):
        rows = self.rows("manifest.csv")
        rows[2]["replicate"] = rows[3]["replicate"] = rows[0]["replicate"]
        self.replace("manifest.csv", rows)
        self.reject("replicate identifier is reused")

    def test_mismatched_replicates_within_pair(self):
        rows = self.rows("manifest.csv")
        rows[1]["replicate"] = "different_repeat"
        self.replace("manifest.csv", rows)
        self.reject("replicate declarations do not match")

    def test_zero_and_opposite_sign_current_rejected(self):
        rows = self.rows("iv_sweeps.csv")
        for value in ("0", "-1e-6"):
            bad = [dict(row) for row in rows]
            for row in bad[:3]:
                row["current"] = value
            self.replace("iv_sweeps.csv", bad)
            self.reject("same sign")

    def test_negative_voltage_and_current_supported_without_absolute_values(self):
        conditions = self.rows("conditions.csv")
        conditions[0]["read_voltage_V"] = "-0.1"
        self.replace("conditions.csv", conditions)
        rows = self.rows("iv_sweeps.csv")
        for row in rows:
            row["voltage"] = "-" + row["voltage"]
            row["current"] = "-" + row["current"]
        self.replace("iv_sweeps.csv", rows)
        features = list(csv.DictReader(io.StringIO(self.build()["features.csv"])))
        self.assertTrue(all(row["read_voltage_V"] == "-0.1" for row in features))
        self.assertTrue(all(float(row["hrs_resistance_median_ohm"]) > 0 for row in features))

    def test_bracket_limit_no_extrapolation(self):
        with self.assertRaises(pipeline.InputError):
            pipeline.build(self.inputs, max_bracket_span_V="0.01", assumptions_accepted=True)
        rows = self.rows("conditions.csv")
        rows[0]["read_voltage_V"] = "0.3"
        self.replace("conditions.csv", rows)
        self.reject("I-V extraction")

    def test_extra_columns_nonfinite_numbers_and_formula_ids_rejected(self):
        source = self.inputs / "iv_sweeps.csv"
        original = source.read_text()
        source.write_text(original.replace("voltage,current", "voltage,current,extra", 1))
        self.reject("documented columns")
        source.write_text(original)
        rows = self.rows("iv_sweeps.csv")
        rows[0]["current"] = "NaN"
        self.replace("iv_sweeps.csv", rows)
        self.reject("numeric field")
        source.write_text(original)
        rows = self.rows("manifest.csv")
        rows[0]["device"] = "=PRIVATE_FORMULA"
        self.replace("manifest.csv", rows)
        self.reject("spreadsheet-safe")

    def test_no_implicit_assumption_acceptance(self):
        with self.assertRaises(pipeline.InputError):
            pipeline.build(self.inputs, max_bracket_span_V="0.11")

    def test_manifests_can_be_reordered_without_altering_features(self):
        baseline = self.build()["features.csv"]
        self.replace("manifest.csv", list(reversed(self.rows("manifest.csv"))))
        self.assertEqual(self.build()["features.csv"], baseline)

    def test_cli_and_no_overwrite_or_symlink(self):
        destination = self.root / "output"
        command = [sys.executable, str(EXAMPLE / "build_features.py"), "--input-dir", str(self.inputs),
                   "--output-dir", str(destination), "--max-bracket-span-V", "0.11", "--accept-measurement-assumptions"]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotIn("synthetic_device", first.stdout + first.stderr)
        original = (destination / "features.csv").read_bytes()
        again = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(again.returncode, 2)
        self.assertEqual((destination / "features.csv").read_bytes(), original)
        link = self.root / "link"
        link.symlink_to(destination, target_is_directory=True)
        command[command.index("--output-dir") + 1] = str(link)
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in destination.iterdir()))

    def test_validation_failure_creates_no_output_and_private_diagnostic(self):
        rows = self.rows("manifest.csv")
        rows[0]["condition"] = "PRIVATE_CONDITION_DO_NOT_LOG"
        self.replace("manifest.csv", rows)
        destination = self.root / "PRIVATE_PATH_DO_NOT_LOG"
        stderr, stdout = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            code = pipeline.main(["--input-dir", str(self.inputs), "--output-dir", str(destination),
                                  "--max-bracket-span-V", "0.11", "--accept-measurement-assumptions"])
        self.assertEqual(code, 2)
        self.assertFalse(destination.exists())
        self.assertNotIn("PRIVATE", stderr.getvalue() + stdout.getvalue())

    def test_nonregular_input_rejected_without_hanging(self):
        path = self.inputs / "manifest.csv"
        path.unlink()
        path.mkdir()
        self.reject("regular file")


if __name__ == "__main__":
    unittest.main()
