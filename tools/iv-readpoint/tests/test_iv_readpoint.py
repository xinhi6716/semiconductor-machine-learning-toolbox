"""Software tests using only invented semiconductor measurements."""
import importlib.util
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
from contextlib import redirect_stderr, redirect_stdout

MODULE = Path(__file__).resolve().parents[1] / "iv_readpoint.py"
spec = importlib.util.spec_from_file_location("iv_readpoint", MODULE)
iv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iv)
HEADER = "device,cycle,branch,voltage,current\n"


class IVTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.input = self.directory / "input.csv"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, body, header=HEADER):
        self.input.write_text(header + body, encoding="utf-8")
        return self.input

    def evaluate(self, body, **kwargs):
        self.write(body)
        defaults = dict(voltage_unit="V", current_unit="uA", read_voltage="0.1", read_voltage_unit="V")
        defaults.update(kwargs)
        return iv.evaluate(self.input, **defaults)

    def single(self, body="d,1,up,0.05,5\nd,1,up,0.15,15\n", **kwargs):
        return self.evaluate(body, **kwargs)["results"][0]

    def cli(self, extra=(), body="d,1,up,0.1,10\n"):
        self.write(body)
        command = [sys.executable, str(MODULE), str(self.input), "--voltage-unit", "V", "--current-unit", "uA", "--read-voltage", "0.1", "--read-voltage-unit", "V", *extra]
        return subprocess.run(command, capture_output=True, text=True, timeout=10)

    def test_adjacent_linear_interpolation(self):
        result = self.single()
        self.assertEqual(result["method"], "linear_interpolation")
        self.assertAlmostEqual(result["current_A"], 10e-6)
        self.assertAlmostEqual(result["conductance_S"], 100e-6)
        self.assertEqual(result["source_rows"], [2, 3])

    def test_uses_adjacent_bracket_not_global_fit(self):
        result = self.single("d,1,up,0,0\nd,1,up,0.05,1\nd,1,up,0.15,5\nd,1,up,1,1000\n")
        self.assertAlmostEqual(result["current_A"], 3e-6)
        self.assertEqual(result["source_rows"], [3, 4])

    def test_descending_keeps_direction(self):
        result = self.single("d,1,down,0.15,15\nd,1,down,0.05,5\n")
        self.assertEqual(result["acquisition_direction"], "decreasing")
        self.assertEqual(result["source_rows"], [3, 2])

    def test_exact_point_is_not_interpolated(self):
        result = self.single("d,1,up,0.05,2\nd,1,up,0.1,17\nd,1,up,0.15,3\n")
        self.assertEqual(result["method"], "measured")
        self.assertAlmostEqual(result["current_A"], 17e-6)
        self.assertEqual(result["source_rows"], [3])

    def test_single_point_exact_read(self):
        result = self.single("d,1,up,0.1,10\n")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["acquisition_direction"], "single_point")

    def test_single_point_no_extrapolation(self):
        self.assertEqual(self.single("d,1,up,0.2,10\n")["reason"], "read_voltage_outside_branch")

    def test_outside_both_ends_rejected(self):
        for target in ("0.01", "0.3"):
            with self.subTest(target=target):
                self.assertEqual(self.single(read_voltage=target)["reason"], "read_voltage_outside_branch")

    def test_branch_partition_prevents_hysteresis_merging(self):
        report = self.evaluate("d,1,up,0.05,5\nd,1,down,0.15,30\nd,1,up,0.15,15\nd,1,down,0.05,10\n")
        self.assertEqual(report["counts"]["extracted_groups"], 2)
        self.assertAlmostEqual(report["results"][0]["current_A"], 10e-6)
        self.assertAlmostEqual(report["results"][1]["current_A"], 20e-6)

    def test_device_and_cycle_stay_separate(self):
        report = self.evaluate("a,1,u,0.1,10\na,2,u,0.1,20\nb,1,u,0.1,30\n")
        self.assertEqual(report["counts"]["selected_groups"], 3)
        self.assertEqual([x["current_A"] for x in report["results"]], [1e-5, 2e-5, 3e-5])

    def test_branch_filter(self):
        report = self.evaluate("d,1,a,0.1,1\nd,1,b,0.1,2\n", branch="b")
        self.assertEqual(report["counts"]["excluded_by_branch_filter"], 1)
        self.assertEqual(report["results"][0]["branch"], "b")

    def test_unmatched_branch_filter_error(self):
        with self.assertRaises(iv.InputError):
            self.evaluate("d,1,a,0.1,1\n", branch="missing")

    def test_equal_duplicate_voltage_is_rejected(self):
        self.assertEqual(self.single("d,1,u,0.1,10\nd,1,u,0.1,10\n")["reason"], "duplicate_voltage")

    def test_conflicting_duplicate_voltage_is_rejected(self):
        self.assertEqual(self.single("d,1,u,0.1,10\nd,1,u,0.1,99\n")["reason"], "duplicate_voltage")

    def test_nonmonotonic_not_silently_sorted(self):
        self.assertEqual(self.single("d,1,u,0.05,5\nd,1,u,0.15,15\nd,1,u,0.12,12\n")["reason"], "nonmonotonic_voltage")

    def test_duplicate_outside_requested_bracket_still_rejects(self):
        self.assertEqual(self.single("d,1,u,0.1,10\nd,1,u,0.2,20\nd,1,u,0.2,20\n")["reason"], "duplicate_voltage")

    def test_input_and_read_units_normalized_independently(self):
        result = self.single("d,1,u,50,5\nd,1,u,150,15\n", voltage_unit="mV", current_unit="nA", read_voltage="100000", read_voltage_unit="uV")
        self.assertAlmostEqual(result["read_voltage_V"], 0.1)
        self.assertAlmostEqual(result["current_A"], 1e-8)

    def test_zero_read_voltage_is_error(self):
        for zero in ("0", "-0", "0e10"):
            with self.subTest(zero=zero), self.assertRaises(iv.InputError):
                self.single(read_voltage=zero)

    def test_negative_voltage_and_current_sign(self):
        result = self.single("d,1,u,-0.15,-15\nd,1,u,-0.05,-5\n", read_voltage="-0.1")
        self.assertLess(result["current_A"], 0)
        self.assertGreater(result["conductance_S"], 0)

    def test_negative_conductance_is_retained(self):
        result = self.single("d,1,u,0.1,-10\n")
        self.assertAlmostEqual(result["conductance_S"], -1e-4)

    def test_zero_current_is_allowed(self):
        self.assertEqual(self.single("d,1,u,0.1,0\n")["conductance_S"], 0)

    def test_bracket_span_limit_rejects_wide_gap(self):
        self.assertEqual(self.single(max_bracket_span="0.05")["reason"], "bracket_span_exceeds_limit")

    def test_bracket_span_uses_read_voltage_units(self):
        result = self.single(read_voltage="100", read_voltage_unit="mV", max_bracket_span="110")
        self.assertEqual(result["status"], "ok")

    def test_bracket_limit_does_not_reject_exact_sample(self):
        result = self.single("d,1,u,0.1,10\n", max_bracket_span="0.00001")
        self.assertEqual(result["status"], "ok")

    def test_nonpositive_bracket_span_is_error(self):
        for span in ("0", "-1"):
            with self.subTest(span=span), self.assertRaises(iv.InputError):
                self.single(max_bracket_span=span)

    def test_nonfinite_and_malformed_numeric_fields(self):
        for value in ("NaN", "inf", "-Infinity", "1_000", "0x10", "", "1V", "1e999999999", "1e-999999999"):
            with self.subTest(value=value), self.assertRaises(iv.InputError):
                self.single("d,1,u,0.1," + value + "\n")

    def test_input_overflow_and_underflow(self):
        for value in ("1e309", "1e-324"):
            with self.subTest(value=value), self.assertRaises(iv.InputError):
                self.single("d,1,u,0.1," + value + "\n", current_unit="A")

    def test_interpolation_avoids_intermediate_overflow(self):
        result = self.single("d,1,u,0,1e308\nd,1,u,2,-1e308\n", current_unit="A", read_voltage="1")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["current_A"], 0)

    def test_derived_conductance_overflow_rejected(self):
        result = self.single("d,1,u,1e-300,1e300\n", current_unit="A", read_voltage="1e-300")
        self.assertEqual(result["reason"], "derived_numeric_range_error")

    def test_derived_conductance_underflow_rejected(self):
        result = self.single("d,1,u,1e300,1e-300\n", current_unit="A", read_voltage="1e300")
        self.assertEqual(result["reason"], "derived_numeric_range_error")

    def test_schema_missing_duplicate_extra_or_empty_header(self):
        for header in ("", "device,cycle,branch,voltage\n", "device,cycle,branch,voltage,voltage\n", HEADER.rstrip() + ",temperature\n"):
            with self.subTest(header=header), self.assertRaises(iv.InputError):
                self.write("", header)
                iv.evaluate(self.input, "V", "A", "0.1", "V")

    def test_header_column_order_can_change(self):
        self.write("0.1,1,d,u,1\n", "voltage,current,device,branch,cycle\n")
        self.assertEqual(iv.evaluate(self.input, "V", "A", "0.1", "V")["counts"]["extracted_groups"], 1)

    def test_malformed_width_quotes_and_empty_rows(self):
        for body in ("d,1,u,0.1\n", "d,1,u,0.1,1,extra\n", 'd,1,u,0.1,"1\n', ",,,,\n", "   \n"):
            with self.subTest(body=body), self.assertRaises(iv.InputError):
                self.single(body)

    def test_blank_physical_lines_are_ignored(self):
        result = self.single("\nd,1,u,0.1,1\n\n")
        self.assertEqual(result["source_rows"], [3])

    def test_empty_input_and_header_only_rejected(self):
        with self.assertRaises(iv.InputError):
            self.single("")

    def test_identifiers_must_be_nonempty_unpadded_and_control_free(self):
        for body in (",1,u,0.1,1\n", "d,,u,0.1,1\n", "d,1,,0.1,1\n", " d,1,u,0.1,1\n", '"d\nname",1,u,0.1,1\n'):
            with self.subTest(body=body), self.assertRaises(iv.InputError):
                self.single(body)

    def test_utf8_bom_supported(self):
        self.input.write_text("\ufeff" + HEADER + "晶片,1,u,0.1,1\n", encoding="utf-8")
        result = iv.evaluate(self.input, "V", "A", "0.1", "V")
        self.assertEqual(result["results"][0]["device"], "晶片")

    def test_cli_output_destination_is_explicit(self):
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn(str(self.input), result.stderr)

    def test_cli_stdout_json_and_success_exit(self):
        result = self.cli(["--stdout"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["counts"]["extracted_groups"], 1)
        self.assertEqual(result.stderr, "")

    def test_cli_rejected_group_exit_one_has_report(self):
        result = self.cli(["--stdout"], body="d,1,u,0.2,1\n")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["counts"]["rejected_groups"], 1)

    def test_cli_invalid_input_no_payload_or_data_in_error(self):
        result = self.cli(["--stdout"], body="PRIVATE_DEVICE,1,u,0.1,SECRET_BAD_NUMBER\n")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("PRIVATE_DEVICE", result.stderr)
        self.assertNotIn("SECRET_BAD_NUMBER", result.stderr)

    def test_output_never_overwrites_existing_file_or_input(self):
        self.write("d,1,u,0.1,1\n")
        original = self.input.read_bytes()
        result = self.cli(["--output", str(self.input)], body="d,1,u,0.1,1\n")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.input.read_bytes(), original)
        target = self.directory / "existing.json"
        target.write_text("KEEP")
        result = self.cli(["--output", str(target)])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(target.read_text(), "KEEP")

    @unittest.skipIf(os.name == "nt", "POSIX permissions/symlinks")
    def test_output_owner_permissions_and_symlink_safety(self):
        target = self.directory / "report.json"
        self.assertEqual(self.cli(["--output", str(target)]).returncode, 0)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        link = self.directory / "link.json"
        link.symlink_to(self.directory / "missing.json")
        self.assertEqual(self.cli(["--output", str(link)]).returncode, 2)
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.directory / "missing.json").exists())

    def test_cli_help_and_version(self):
        for flag in ("--help", "--version"):
            result = subprocess.run([sys.executable, str(MODULE), flag], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.stdout)

    def test_full_synthetic_fixture(self):
        report = iv.evaluate(MODULE.parent / "examples" / "synthetic_iv.csv", "V", "uA", "100", "mV")
        self.assertEqual(report["counts"]["extracted_groups"], 4)
        self.assertEqual(report["counts"]["rejected_groups"], 0)
        self.assertTrue(all(math.isfinite(item["conductance_S"]) for item in report["results"]))
        json.dumps(report, allow_nan=False)

    def test_mixed_usable_and_rejected_groups_are_counted(self):
        report = self.evaluate("good,1,u,0.1,1\nbad,1,u,0.3,1\n")
        self.assertEqual(report["counts"]["extracted_groups"], 1)
        self.assertEqual(report["counts"]["rejected_groups"], 1)
        self.assertEqual(report["counts"]["rejected_by_reason"], {"read_voltage_outside_branch": 1})


if __name__ == "__main__":
    unittest.main()
