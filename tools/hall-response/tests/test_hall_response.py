"""Original synthetic regressions: software behavior, not laboratory validation."""
import csv
import hashlib
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
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "hall_response.py"
spec = importlib.util.spec_from_file_location("hall_response", MODULE)
hall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hall)
HEADER = ",".join(hall.COLUMNS) + "\n"


class HallResponseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.input = self.directory / "input.csv"

    def tearDown(self):
        self.temp.cleanup()

    def body(self, fields=(-1, 0, 1), volts=(-1.7, 0.3, 2.3), current=1,
             temperature=300, lot="l", wafer="w", device="d", condition="c", replicate="r", branch="up"):
        return "".join(f"{lot},{wafer},{device},{condition},{replicate},{branch},{temperature},{current},{x},{y}\n"
                       for x, y in zip(fields, volts))

    def write(self, body=None, header=HEADER):
        self.input.write_text(header + (self.body() if body is None else body), encoding="utf-8")
        return self.input

    def evaluate(self, body=None, **kwargs):
        self.write(body)
        params = {"field_unit": "T", "voltage_unit": "mV", "current_unit": "mA", "assumptions_accepted": True}
        params.update(kwargs)
        return hall.evaluate(self.input, **params)

    def single(self, body=None, **kwargs):
        return self.evaluate(body, **kwargs)["results"][0]

    def cli(self, extra=(), body=None, include_assumptions=True):
        self.write(body)
        args = [sys.executable, str(MODULE), str(self.input), "--field-unit", "T",
                "--voltage-unit", "mV", "--current-unit", "mA"]
        if include_assumptions:
            args.append("--accept-hall-assumptions")
        return subprocess.run(args + list(extra), capture_output=True, text=True, timeout=10)

    def test_exact_signed_fit_and_offset(self):
        r = self.single()
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["fit"]["slope_V_per_T"], 0.002)
        self.assertEqual(r["fit"]["zero_field_intercept_V"], 0.0003)
        self.assertEqual(r["current_normalized_sensitivity_V_per_A_T"], 2)
        self.assertEqual(r["fit"]["r_squared"], 1)
        self.assertEqual(r["fit"]["rmse_V"], 0)
        self.assertEqual([p["residual_V"] for p in r["observations"]], [0, 0, 0])
        self.assertIn("zero_residuals_do_not_validate_linearity", r["warnings"])

    def test_all_one_hundred_unit_combinations(self):
        for fu, ff in hall.FIELD_UNITS.items():
            for vu, vf in hall.VOLTAGE_UNITS.items():
                for cu, cf in hall.CURRENT_UNITS.items():
                    with self.subTest(field=fu, voltage=vu, current=cu):
                        x = [str(hall.Decimal(v) / hall.Decimal(ff)) for v in (-1, 0, 1)]
                        y = [str(hall.Decimal(v) / hall.Decimal(vf)) for v in ("-0.0017", "0.0003", "0.0023")]
                        current = str(hall.Decimal("0.001") / hall.Decimal(cf))
                        r = self.single(self.body(x, y, current), field_unit=fu, voltage_unit=vu, current_unit=cu)
                        self.assertEqual(r["fit"]["slope_V_per_T"], 0.002)
                        self.assertEqual(r["current_normalized_sensitivity_V_per_A_T"], 2)

    def test_negative_current_normalization_retains_sign(self):
        r = self.single(self.body(current=-1))
        self.assertEqual(r["current_normalized_sensitivity_V_per_A_T"], -2)
        r = self.single(self.body(volts=(2.3, 0.3, -1.7), current=-1))
        self.assertEqual(r["fit"]["slope_V_per_T"], -0.002)
        self.assertEqual(r["current_normalized_sensitivity_V_per_A_T"], 2)

    def test_zero_current_only_voltage_diagnostic(self):
        r = self.single(self.body(current=0))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["fit"]["slope_V_per_T"], 0.002)
        self.assertIsNone(r["current_normalized_sensitivity_V_per_A_T"])
        self.assertIn("zero_bias_current_normalization_unavailable", r["warnings"])

    def test_curvature_residuals_and_descriptive_scale(self):
        r = self.single(self.body((-1, 0, 1), (1, 0, 1)), voltage_unit="V")
        self.assertEqual(r["fit"]["slope_V_per_T"], 0)
        self.assertAlmostEqual(r["fit"]["zero_field_intercept_V"], 2 / 3)
        self.assertEqual(r["fit"]["r_squared"], 0)
        self.assertAlmostEqual(r["fit"]["rmse_V"], math.sqrt(2 / 9))
        self.assertAlmostEqual(r["fit"]["max_abs_residual_over_voltage_span"], 2 / 3)
        for actual, expected in zip(r["observations"], (1 / 3, -2 / 3, 1 / 3)):
            self.assertAlmostEqual(actual["residual_V"], expected)
        self.assertIn("nonzero_residuals_possible_nonlinearity_noise_or_drift", r["warnings"])
        self.assertIn("zero_slope_does_not_establish_absence_of_hall_effect", r["warnings"])

    def test_constant_voltage_r_squared_and_normalized_residual_null(self):
        r = self.single(self.body(volts=(3, 3, 3)))
        self.assertIsNone(r["fit"]["r_squared"])
        self.assertIsNone(r["fit"]["max_abs_residual_over_voltage_span"])
        self.assertEqual(r["fit"]["rmse_V"], 0)
        self.assertEqual(r["current_normalized_sensitivity_V_per_A_T"], 0)

    def test_nonzero_residual_warning_has_no_hidden_tolerance(self):
        r = self.single(self.body(volts=("-1.7", "0.30000000000000000001", "2.3")))
        self.assertGreater(r["fit"]["rmse_V"], 0)
        self.assertIn("nonzero_residuals_possible_nonlinearity_noise_or_drift", r["warnings"])

    def test_decreasing_branch_not_sorted(self):
        r = self.single(self.body((1, 0, -1), (2.3, 0.3, -1.7), branch="down"))
        self.assertEqual(r["acquisition_direction"], "decreasing_field")
        self.assertEqual([p["magnetic_field_T"] for p in r["observations"]], [1, 0, -1])
        self.assertEqual(r["fit"]["slope_V_per_T"], 0.002)

    def test_every_group_dimension_isolates_fit(self):
        body = self.body()
        for name in hall.GROUP_COLUMNS:
            body += self.body(**{name: "separate"})
        report = self.evaluate(body)
        self.assertEqual(report["counts"]["input_groups"], 7)
        self.assertEqual(report["counts"]["distinct_physical_device_ids"], 4)
        self.assertEqual(report["counts"]["rejected_groups"], 0)
        self.assertTrue(all("branches_and_replicates_not_independent_devices" in r["warnings"] for r in report["results"]))

    def test_interleaved_groups_preserve_within_group_order(self):
        a = self.body().splitlines(True)
        b = self.body(device="other").splitlines(True)
        report = self.evaluate("".join(x + y for x, y in zip(a, b)))
        self.assertEqual([r["source_rows"] for r in report["results"]], [[2, 4, 6], [3, 5, 7]])
        self.assertTrue(all(r["status"] == "ok" for r in report["results"]))

    def test_current_and_temperature_changes_reject_group(self):
        for column in ("bias_current", "temperature_K"):
            lines = list(csv.reader(io.StringIO(self.body())))
            lines[1][hall.COLUMNS.index(column)] = "2" if column == "bias_current" else "301"
            body = "".join(",".join(line) + "\n" for line in lines)
            r = self.single(body)
            self.assertEqual(r["reason"], "inconsistent_temperature_or_bias_current")
            self.assertIsNone(r["fit"])

    def test_signed_current_change_rejects_even_same_magnitude(self):
        body = self.body().replace(",300,1,0,", ",300,-1,0,")
        self.assertEqual(self.single(body)["reason"], "inconsistent_temperature_or_bias_current")

    def test_exact_conditions_do_not_float_collapse(self):
        body = self.body().replace(",300,1,0,", ",300,1.000000000000000000000000000001,0,")
        self.assertEqual(self.single(body)["reason"], "inconsistent_temperature_or_bias_current")
        body = self.body().replace(",300,1,0,", ",300.00000000000000000000000001,1,0,")
        self.assertEqual(self.single(body)["reason"], "inconsistent_temperature_or_bias_current")

    def test_nonpositive_temperature_rejected(self):
        for temperature in (0, -1):
            self.assertEqual(self.single(self.body(temperature=temperature))["reason"], "nonpositive_temperature")

    def test_fewer_than_three_fields_rejected(self):
        for fields, volts in (((0,), (1,)), ((0, 1), (1, 2))):
            self.assertEqual(self.single(self.body(fields, volts))["reason"], "fewer_than_three_distinct_fields")

    def test_duplicate_numeric_field_forms_rejected(self):
        self.assertEqual(self.single(self.body(("0", "-0.0", "1")))["reason"], "duplicate_magnetic_field")

    def test_reversal_is_rejected_not_sorted(self):
        self.assertEqual(self.single(self.body((0, 1, -1)))["reason"], "nonmonotonic_magnetic_field")

    def test_field_polarity_and_intercept_warnings(self):
        r = self.single(self.body((10, 11, 12), (20.3, 22.3, 24.3)))
        self.assertEqual(r["fit"]["zero_field_extrapolation_ratio"], 5)
        self.assertIn("zero_field_intercept_is_extrapolated", r["warnings"])
        self.assertIn("both_field_polarities_not_sampled", r["warnings"])
        self.assertNotIn("large_zero_field_extrapolation", r["warnings"])
        r = self.single(self.body((-12, -11, -10), (-23.7, -21.7, -19.7)))
        self.assertEqual(r["fit"]["zero_field_extrapolation_ratio"], 5)
        r = self.single(self.body((100, 101, 102), (200.3, 202.3, 204.3)))
        self.assertIn("large_zero_field_extrapolation", r["warnings"])

    def test_intercept_inside_range_without_sample(self):
        r = self.single(self.body((-1, 0.5, 1), (-1.7, 1.3, 2.3)))
        self.assertEqual(r["fit"]["zero_field_extrapolation_ratio"], 0)
        self.assertIn("zero_field_intercept_not_directly_sampled", r["warnings"])
        self.assertNotIn("zero_field_intercept_is_extrapolated", r["warnings"])

    def test_fitted_intercept_not_subtracted_or_replaced_by_zero_measurement(self):
        r = self.single(self.body(volts=(1, 0, 1)), voltage_unit="V")
        self.assertEqual(r["observations"][1]["transverse_voltage_V"], 0)
        self.assertAlmostEqual(r["fit"]["zero_field_intercept_V"], 2 / 3)
        self.assertAlmostEqual(r["observations"][1]["fitted_transverse_voltage_V"], 2 / 3)

    def test_exact_fit_for_fields_below_binary64_separation(self):
        r = self.single(self.body(("1", "1.000000000000000000000000000001", "1.000000000000000000000000000002"), (0, 1, 2)))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["fit"]["slope_V_per_T"], 1e27)
        self.assertIn("binary64_field_rounding_merges_distinct_inputs", r["warnings"])

    def test_voltage_rounding_warning(self):
        r = self.single(self.body(volts=("1", "1.000000000000000000000000000001", "1.000000000000000000000000000002")))
        self.assertEqual(r["status"], "ok")
        self.assertIn("binary64_voltage_rounding_merges_distinct_inputs", r["warnings"])
        self.assertGreater(r["fit"]["slope_V_per_T"], 0)

    def test_large_finite_fit_avoids_intermediate_overflow(self):
        r = self.single(self.body(("-1e150", "0", "1e150"), ("-1e150", "0", "1e150"), current="1e150"), voltage_unit="V", current_unit="A")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["fit"]["slope_V_per_T"], 1)
        self.assertEqual(r["current_normalized_sensitivity_V_per_A_T"], 1e-150)

    def test_tiny_residual_sqrt_does_not_underflow_squared_value(self):
        r = self.single(self.body(volts=("1e-150", "0", "1e-150")), voltage_unit="V")
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["fit"]["rmse_V"] / 1e-150, math.sqrt(2 / 9))

    def test_normalization_overflow_and_underflow_reject_without_partial_fit(self):
        for fields, volts, current in ((('-1e-150', '0', '1e-150'), ('-1e150', '0', '1e150'), '1e-150'),
                                       (('-1e150', '0', '1e150'), ('-1e-150', '0', '1e-150'), '1e150')):
            r = self.single(self.body(fields, volts, current), voltage_unit="V", current_unit="A")
            self.assertEqual(r["reason"], "derived_numeric_range_error")
            self.assertIsNone(r["fit"])
            self.assertIsNone(r["current_normalized_sensitivity_V_per_A_T"])
            self.assertTrue(all("residual_V" not in p for p in r["observations"]))

    def test_finite_float_rejects_nonzero_overflow_and_underflow(self):
        for value in (hall.Fraction(1, 10 ** 400), hall.Fraction(10 ** 400)):
            with self.assertRaises(hall.InputError):
                hall.finite_float(value)

    def test_explicit_boolean_assumption_acceptance(self):
        for value in (False, None, 1, "yes"):
            with self.assertRaises(hall.InputError):
                self.evaluate(assumptions_accepted=value)

    def test_unknown_unit_api_rejected(self):
        for params in ({"field_unit": "A/m"}, {"voltage_unit": "MV"}, {"current_unit": "amp"}):
            with self.assertRaises(hall.InputError):
                self.evaluate(**params)

    def test_bad_numeric_syntax_bounds_and_secrets_not_echoed(self):
        for value in ("NaN", "Infinity", "1_000", "1mA", "", "1e99999", "1e201", "0e-201", "1e151", "1e-151", "1" * 33, "9" * 65, "SECRET_MEASUREMENT"):
            with self.subTest(value=value), self.assertRaises(hall.InputError) as cm:
                self.evaluate(self.body(current=value), current_unit="A")
            self.assertNotIn(value, str(cm.exception)) if value else None

    def test_numeric_boundaries_inclusive_after_si_conversion(self):
        self.assertEqual(hall.number("1e-150", "1"), hall.MIN_NUMBER)
        self.assertEqual(hall.number("-1e150", "1"), -hall.MAX_NUMBER)
        self.assertEqual(hall.number("1e-147", "0.001"), hall.MIN_NUMBER)
        with self.assertRaises(hall.InputError):
            hall.number("1e-150", "0.001")

    def test_identifiers_strict_and_private(self):
        for value in ("", " padded", "padded ", "a\tb", "a\x7fb", "a" * 129):
            with self.assertRaises(hall.InputError):
                self.evaluate(self.body(device=value))

    def test_header_missing_extra_duplicate_and_unknown_rejected(self):
        headers = [HEADER.rstrip().rsplit(",", 1)[0] + "\n", HEADER.rstrip() + ",private_extra\n",
                   HEADER.replace("device", "wafer"), HEADER.replace("device", "private_unknown")]
        for header in headers:
            self.write(header=header)
            with self.assertRaises(hall.InputError):
                hall.evaluate(self.input, "T", "mV", "mA", True)

    def test_header_order_and_utf8_bom_supported(self):
        records = list(csv.reader(io.StringIO(HEADER + self.body())))
        self.input.write_text("\ufeff" + "".join(",".join(reversed(row)) + "\n" for row in records), encoding="utf-8")
        r = hall.evaluate(self.input, "T", "mV", "mA", True)
        self.assertEqual(r["results"][0]["fit"]["slope_V_per_T"], 0.002)

    def test_empty_lines_skip_but_whitespace_rows_do_not(self):
        r = self.evaluate("\n" + self.body() + "\n")
        self.assertEqual(r["results"][0]["source_rows"], [3, 4, 5])
        with self.assertRaises(hall.InputError):
            self.evaluate(" \n" + self.body())

    def test_empty_input_and_row_widths_rejected(self):
        for body in ("", "\n", self.body() + "one,two\n", self.body() + "," * 10 + "\n"):
            with self.assertRaises(hall.InputError):
                self.evaluate(body)

    def test_bad_utf8_and_bad_quotes_fail_safely(self):
        for content in (b"\xff", (HEADER + '"unclosed').encode()):
            self.input.write_bytes(content)
            with self.assertRaises(hall.InputError):
                hall.evaluate(self.input, "T", "mV", "mA", True)

    def test_missing_or_directory_input_safe(self):
        for path in (self.directory / "PRIVATE_MISSING", self.directory):
            with self.assertRaises(hall.InputError) as cm:
                hall.evaluate(path, "T", "mV", "mA", True)
            self.assertNotIn(str(path), str(cm.exception))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO only")
    def test_fifo_rejected_without_waiting(self):
        os.mkfifo(self.input)
        with self.assertRaises(hall.InputError):
            hall.evaluate(self.input, "T", "mV", "mA", True)

    def test_bounded_byte_rows_groups_and_group_rows(self):
        for name, limit, body in (("MAX_BYTES", 1, self.body()), ("MAX_ROWS", 2, self.body()),
                                  ("MAX_GROUP_ROWS", 2, self.body()),
                                  ("MAX_GROUPS", 1, self.body() + self.body(device="other"))):
            with patch.object(hall, name, limit), self.assertRaises(hall.InputError):
                self.evaluate(body)

    def test_input_hash_source_rows_and_no_mutation(self):
        self.write()
        before = self.input.read_bytes()
        report = hall.evaluate(self.input, "T", "mV", "mA", True)
        self.assertEqual(report["provenance"]["input_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(report["results"][0]["source_rows"], [2, 3, 4])
        self.assertEqual(self.input.read_bytes(), before)
        self.assertEqual(report["parameters"]["opposite_field_pairing"], "not_performed")
        self.assertNotIn("physical_estimates", report["results"][0])

    def test_cli_stdout_success_and_warning_not_rejection(self):
        result = self.cli(("--stdout",), self.body(volts=(1, 0, 1)))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["counts"]["extracted_groups"], 1)

    def test_cli_mixed_groups_returns_one_with_report(self):
        result = self.cli(("--stdout",), self.body() + self.body((0, 1, 0), device="other"))
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"]["extracted_groups"], 1)
        self.assertEqual(report["counts"]["rejected_by_reason"], {"duplicate_magnetic_field": 1})

    def test_cli_no_default_output_or_assumptions(self):
        for extra, accepted in (((), True), (("--stdout",), False), (("--stdout", "--output", "other.json"), True)):
            result = self.cli(extra, include_assumptions=accepted)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_cli_bad_arguments_do_not_leak_private_values(self):
        for extra in (("--field-unit", "PRIVATE_UNIT", "--stdout"), ("--PRIVATE_ARGUMENT", "PRIVATE_VALUE", "--stdout")):
            result = self.cli(extra)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("PRIVATE", result.stderr)
            self.assertNotIn(str(self.input), result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_cli_bad_numeric_error_private(self):
        result = self.cli(("--stdout",), self.body(volts=("PRIVATE_VOLTAGE", 0, 1)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("PRIVATE", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_output_exclusive_and_owner_only(self):
        output = self.directory / "report.json"
        result = self.cli(("--output", str(output)))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(output.read_text())["tool"], "hall-response")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        before = output.read_bytes()
        result = self.cli(("--output", str(output)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output.read_bytes(), before)
        self.assertNotIn(str(output), result.stderr)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_output_live_and_dangling_symlinks_never_followed(self):
        for existing in (True, False):
            target = self.directory / ("existing" if existing else "missing")
            if existing:
                target.write_text("unchanged")
            link = self.directory / ("live_link" if existing else "dangling_link")
            link.symlink_to(target)
            result = self.cli(("--output", str(link)))
            self.assertEqual(result.returncode, 2)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(), "unchanged") if existing else self.assertFalse(target.exists())

    def test_output_cannot_overwrite_input(self):
        self.write()
        original = self.input.read_bytes()
        result = self.cli(("--output", str(self.input)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.input.read_bytes(), original)

    def test_version_and_help_do_not_need_input(self):
        for flag in ("--help", "--version"):
            result = subprocess.run([sys.executable, str(MODULE), flag], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")

    def test_fixture_generator_reproduces_bytes_and_expected_values(self):
        fixture = MODULE.parent / "examples" / "synthetic_hall.csv"
        generator = MODULE.parent / "examples" / "generate_synthetic.py"
        generated = subprocess.run([sys.executable, str(generator)], capture_output=True, timeout=10)
        self.assertEqual(generated.returncode, 0)
        self.assertEqual(generated.stdout, fixture.read_bytes())
        report = hall.evaluate(fixture, "T", "mV", "mA", True)
        self.assertEqual(report["counts"]["input_rows"], 20)
        self.assertEqual(report["counts"]["input_groups"], 4)
        self.assertEqual(report["counts"]["distinct_physical_device_ids"], 2)
        self.assertEqual(report["counts"]["rejected_groups"], 0)
        self.assertEqual([r["current_normalized_sensitivity_V_per_A_T"] for r in report["results"]], [2, 2, 2, 1])
        self.assertEqual(report["results"][-1]["fit"]["max_abs_residual_V"], 0.0001)
        json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
