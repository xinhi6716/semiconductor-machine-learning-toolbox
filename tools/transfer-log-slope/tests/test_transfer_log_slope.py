"""Original synthetic tests establish software behavior, not physical validity."""
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

MODULE = Path(__file__).resolve().parents[1] / "transfer_log_slope.py"
spec = importlib.util.spec_from_file_location("transfer_log_slope", MODULE)
log = importlib.util.module_from_spec(spec)
spec.loader.exec_module(log)
HEADER = ",".join(log.COLUMNS) + "\n"


class TransferLogSlopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.input = self.directory / "input.csv"

    def tearDown(self):
        self.temp.cleanup()

    def body(self, gates=(0, 0.1, 0.2), currents=(1, 10, 100), temperature=300,
             drain=0.1, body=0, lot="l", wafer="w", device="d", condition="c", replicate="r", branch="up"):
        return "".join(f"{lot},{wafer},{device},{condition},{replicate},{branch},{temperature},{drain},{body},{x},{y}\n"
                       for x, y in zip(gates, currents))

    def write(self, body=None, header=HEADER):
        self.input.write_text(header + (self.body() if body is None else body), encoding="utf-8")
        return self.input

    def evaluate(self, body=None, **kwargs):
        self.write(body)
        params = {"voltage_unit": "V", "current_unit": "nA", "window_min": "0", "window_max": "0.2",
                  "current_polarity": "positive", "assumptions_accepted": True}
        params.update(kwargs)
        return log.evaluate(self.input, **params)

    def single(self, body=None, **kwargs):
        return self.evaluate(body, **kwargs)["results"][0]

    def cli(self, extra=(), body=None, assumptions=True):
        self.write(body)
        args = [sys.executable, str(MODULE), str(self.input), "--voltage-unit", "V", "--current-unit", "nA",
                "--window-min", "0", "--window-max", "0.2", "--current-polarity", "positive"]
        if assumptions:
            args.append("--accept-log-transfer-assumptions")
        return subprocess.run(args + list(extra), capture_output=True, text=True, timeout=10)

    def test_base_ten_slope_reciprocal_and_residuals(self):
        r = self.single()
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["fit"]["slope_decades_per_V"], 10)
        self.assertEqual(r["fit"]["zero_gate_intercept_log10_current_over_A"], -9)
        self.assertEqual(r["reciprocal_slope"]["signed_mV_per_decade"], 100)
        self.assertEqual(r["reciprocal_slope"]["signed_V_per_decade"], 0.1)
        self.assertEqual(r["fit"]["r_squared"], 1)
        self.assertEqual(r["fit"]["rmse_decades"], 0)
        self.assertEqual([p["residual_decades"] for p in r["observations"]], [0, 0, 0])
        self.assertIn("user_window_not_certified_subthreshold", r["warnings"])

    def test_all_fifteen_unit_combinations(self):
        for vu, vf in log.VOLTAGE_UNITS.items():
            for cu, cf in log.CURRENT_UNITS.items():
                with self.subTest(voltage=vu, current=cu):
                    gates = [str(log.Decimal(v) / log.Decimal(vf)) for v in ("0", "0.1", "0.2")]
                    currents = [str(log.Decimal(v) / log.Decimal(cf)) for v in ("1e-9", "1e-8", "1e-7")]
                    drain = str(log.Decimal("0.1") / log.Decimal(vf))
                    r = self.single(self.body(gates, currents, drain=drain), voltage_unit=vu, current_unit=cu,
                                    window_max=str(log.Decimal("0.2") / log.Decimal(vf)))
                    self.assertEqual(r["fit"]["slope_decades_per_V"], 10)
                    self.assertEqual(r["fit"]["zero_gate_intercept_log10_current_over_A"], -9)

    def test_negative_declared_current_not_implicit_abs(self):
        r = self.single(self.body(currents=(-1, -10, -100)), current_polarity="negative")
        self.assertEqual(r["fit"]["slope_decades_per_V"], 10)
        self.assertEqual(r["observations"][0]["drain_current_A"], -1e-9)
        r = self.single(self.body(currents=(-1, -10, -100)))
        self.assertEqual(r["reason"], "current_sign_conflicts_with_declared_polarity_in_user_window")

    def test_negative_slope_keeps_signed_and_magnitude_distinct(self):
        r = self.single(self.body(currents=(100, 10, 1)))
        self.assertEqual(r["fit"]["slope_decades_per_V"], -10)
        self.assertEqual(r["reciprocal_slope"]["signed_mV_per_decade"], -100)
        self.assertEqual(r["reciprocal_slope"]["magnitude_mV_per_decade"], 100)
        self.assertIn("negative_log_slope_sign_preserved", r["warnings"])

    def test_reverse_acquisition_does_not_reverse_slope(self):
        r = self.single(self.body((0.2, 0.1, 0), (100, 10, 1)))
        self.assertEqual(r["fit"]["slope_decades_per_V"], 10)
        self.assertEqual(r["acquisition_direction"], "decreasing_vgs")
        self.assertEqual([p["gate_source_voltage_V"] for p in r["observations"]], [0.2, 0.1, 0])

    def test_constant_current_has_no_reciprocal_or_r_squared(self):
        r = self.single(self.body(currents=(3, 3, 3)))
        self.assertEqual(r["fit"]["slope_decades_per_V"], 0)
        self.assertIsNone(r["reciprocal_slope"])
        self.assertIsNone(r["fit"]["r_squared"])
        self.assertEqual(r["fit"]["rmse_decades"], 0)

    def test_curvature_residuals_equal_row_weights(self):
        r = self.single(self.body(currents=(1, 100, 100)))
        self.assertEqual(r["fit"]["slope_decades_per_V"], 10)
        self.assertAlmostEqual(r["fit"]["r_squared"], 0.75)
        self.assertAlmostEqual(r["fit"]["rmse_decades"], math.sqrt(2 / 9))
        for p, expected in zip(r["observations"], (-1/3, 2/3, -1/3)):
            self.assertAlmostEqual(p["residual_decades"], expected)

    def test_nonmonotonic_current_retained_not_cherry_picked(self):
        r = self.single(self.body(currents=(1, 100, 10)))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["fit"]["slope_decades_per_V"], 5)
        self.assertEqual(r["selected_source_rows"], [2, 3, 4])
        self.assertTrue(all(p["used_in_fit"] for p in r["observations"]))
        self.assertIn("nonmonotonic_selected_current_retained_not_trimmed", r["warnings"])

    def test_inclusive_window_and_every_source_row_audited(self):
        r = self.single(self.body((-.1, 0, .1, .2, .3), (0, 1, 10, 100, -1)))
        self.assertEqual(r["selected_source_rows"], [3, 4, 5])
        self.assertEqual(r["outside_window_source_rows"], [2, 6])
        self.assertEqual([p["window_selection"] for p in r["observations"]],
                         ["below_user_window"] + ["inside_user_window"] * 3 + ["above_user_window"])
        self.assertEqual([p["used_in_fit"] for p in r["observations"]], [False, True, True, True, False])
        self.assertNotIn("residual_decades", r["observations"][0])
        self.assertEqual(r["observations"][-1]["current_log_domain"], "polarity_conflict")
        self.assertEqual(r["status"], "ok")

    def test_window_does_not_interpolate_or_extend_samples(self):
        r = self.single(window_min="0.001", window_max="0.199")
        self.assertEqual(r["reason"], "fewer_than_three_samples_in_user_window")
        self.assertEqual(r["selected_source_rows"], [3])
        r = self.single(window_min="-1", window_max="1")
        self.assertEqual(r["fit"]["selected_gate_voltage_range_V"], [0, .2])
        self.assertIn("requested_window_extends_beyond_measured_branch_no_extrapolation", r["warnings"])

    def test_empty_window_is_rejected_with_audit(self):
        r = self.single(window_min="1", window_max="2")
        self.assertEqual(r["selected_sample_count"], 0)
        self.assertEqual(r["outside_window_source_rows"], [2, 3, 4])
        self.assertEqual(r["reason"], "fewer_than_three_samples_in_user_window")

    def test_window_exact_decimal_not_binary64_selection(self):
        gates = ("1", "1.00000000000000000001", "1.00000000000000000002", "1.00000000000000000003")
        r = self.single(self.body(gates, (1, 10, 100, 1000)), window_min=gates[1], window_max=gates[-1])
        self.assertEqual(r["selected_source_rows"], [3, 4, 5])
        self.assertAlmostEqual(r["fit"]["slope_decades_per_V"], 1e20)
        self.assertIn("binary64_gate_voltage_rounding_merges_distinct_inputs", r["warnings"])
        self.assertEqual(r["observations"][1]["input_numeric_text"]["gate_voltage"], gates[1])

    def test_all_identity_dimensions_isolated_and_audited(self):
        bodies = [self.body()]
        for name in log.GROUP_COLUMNS:
            bodies.append(self.body(**{name: "other"}, currents=(10, 100, 1000)))
        report = self.evaluate("".join(bodies))
        self.assertEqual(report["counts"]["input_groups"], 7)
        self.assertEqual(report["counts"]["distinct_physical_device_ids"], 4)
        self.assertEqual(len(report["results"]), 7)
        self.assertEqual(report["counts"]["fitted_rows"], 21)

    def test_interleaved_groups_preserve_each_supplied_order(self):
        a = self.body().splitlines(True)
        b = self.body(device="other").splitlines(True)
        report = self.evaluate("".join(i for pair in zip(a, b) for i in pair))
        self.assertEqual(report["results"][0]["source_rows"], [2, 4, 6])
        self.assertEqual(report["results"][1]["source_rows"], [3, 5, 7])

    def test_rejection_never_silently_discards_invalid_selected_current(self):
        for currents, reason in (((1, 0, 100), "zero_current_in_user_window"),
                                 ((1, -10, 100), "current_sign_conflicts_with_declared_polarity_in_user_window")):
            r = self.single(self.body(currents=currents))
            self.assertEqual(r["reason"], reason)
            self.assertIsNone(r["fit"])
            self.assertIsNone(r["reciprocal_slope"])
            self.assertFalse(any(p["used_in_fit"] for p in r["observations"]))
            self.assertEqual(r["selected_sample_count"], 3)

    def test_duplicate_and_reversal_outside_window_still_reject(self):
        for gates, reason in (((0, .1, .2, .3, .3), "duplicate_gate_voltage"),
                              ((0, .1, .2, .4, .3), "nonmonotonic_gate_voltage")):
            r = self.single(self.body(gates, (1, 10, 100, 1000, 10000)))
            self.assertEqual(r["reason"], reason)

    def test_too_few_branch_points(self):
        self.assertEqual(self.single(self.body((0, .1), (1, 10)))["reason"], "fewer_than_three_branch_samples")

    def test_fixed_conditions_entire_branch_not_just_window(self):
        for field in ("temperature", "drain", "body"):
            changed = self.body((.3,), (1000,), **{field: 20})
            r = self.single(self.body() + changed)
            self.assertEqual(r["reason"], "inconsistent_temperature_or_bias")

    def test_positive_temperature_and_nonzero_vds(self):
        for kwargs, reason in (({"temperature": 0}, "nonpositive_temperature"),
                               ({"temperature": -1}, "nonpositive_temperature"), ({"drain": 0}, "zero_drain_bias")):
            self.assertEqual(self.single(self.body(**kwargs))["reason"], reason)
        self.assertEqual(self.single(self.body(drain=-.1))["status"], "ok")

    def test_invalid_bounds_units_polarity_and_assumptions(self):
        for params in ({"window_min": ".2"}, {"window_min": "1"}, {"window_max": "NaN"},
                       {"voltage_unit": "MV"}, {"current_unit": "amp"}, {"current_polarity": "auto"},
                       {"assumptions_accepted": False}, {"assumptions_accepted": 1}, {"assumptions_accepted": "yes"}):
            with self.subTest(params=params), self.assertRaises(log.InputError):
                self.evaluate(**params)

    def test_logarithms_keep_near_equal_currents_distinct(self):
        r = self.single(self.body(currents=("1", "1.0000000000000000000000000000001", "1.0000000000000000000000000000002")), current_unit="A")
        self.assertGreater(r["fit"]["slope_decades_per_V"], 0)
        self.assertIn("binary64_drain_current_rounding_merges_distinct_inputs", r["warnings"])
        self.assertGreater(r["reciprocal_slope"]["signed_mV_per_decade"], 1e32)

    def test_log_precision_collision_explicit_rejection(self):
        with patch.object(log, "LOG_PRECISION", 2):
            r = self.single(self.body(currents=("1", "1.0001", "1.0002")), current_unit="A")
        self.assertEqual(r["reason"], "log_precision_merges_distinct_currents")

    def test_huge_offset_small_span_rank_not_lost(self):
        gates = ("100000000000000000000", "100000000000000000000.1", "100000000000000000000.2")
        r = self.single(self.body(gates), window_min=gates[0], window_max=gates[-1])
        self.assertEqual(r["fit"]["slope_decades_per_V"], 10)
        self.assertEqual(r["fit"]["rmse_decades"], 0)
        self.assertIn("zero_gate_intercept_is_extrapolated_diagnostic_only", r["warnings"])

    def test_extreme_input_range_is_loggable_and_finite(self):
        r = self.single(self.body(("-1e150", "0", "1e150"), ("1e-150", "1", "1e150")),
                        current_unit="A", window_min="-1e150", window_max="1e150")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["fit"]["slope_decades_per_V"], 1.5e-148)
        self.assertEqual(r["fit"]["observed_log10_current_span_decades"], 300)
        json.dumps(r, allow_nan=False)

    def test_derived_numeric_error_clears_partial_fit(self):
        with patch.object(log, "root_fraction", side_effect=log.InputError("range error")):
            r = self.single()
        self.assertEqual(r["reason"], "derived_numeric_range_error")
        self.assertIsNone(r["fit"])
        self.assertIsNone(r["reciprocal_slope"])
        self.assertFalse(any(p["used_in_fit"] for p in r["observations"]))

    def test_finite_float_rejects_underflow_overflow(self):
        for value in (log.Fraction(1, 10**400), log.Fraction(10**400)):
            with self.assertRaises(log.InputError):
                log.finite_float(value)

    def test_tiny_residual_root_not_lost_by_squaring_binary64(self):
        self.assertEqual(log.root_fraction(log.Fraction(1, 10**600)), 1e-300)

    def test_bad_numeric_syntax_precision_range_safe(self):
        for value in ("NaN", "Infinity", "1_000", "1nA", "", "1e99999", "1e201", "0e-201", "1e151", "1e-151", "1"*33, "9"*65, "SECRET_CURRENT"):
            with self.subTest(value=value), self.assertRaises(log.InputError) as cm:
                self.evaluate(self.body(currents=(1, value, 100)), current_unit="A")
            if value:
                self.assertNotIn(value, str(cm.exception))

    def test_bad_number_outside_window_still_fatal(self):
        with self.assertRaises(log.InputError):
            self.evaluate(self.body() + self.body((.3,), ("NaN",)))

    def test_numeric_si_bounds_inclusive(self):
        self.assertEqual(log.number("1e-150", "1"), log.MIN_NUMBER)
        self.assertEqual(log.number("-1e150", "1"), -log.MAX_NUMBER)
        self.assertEqual(log.number("1e-147", ".001"), log.MIN_NUMBER)
        with self.assertRaises(log.InputError):
            log.number("1e-150", ".001")

    def test_identifiers_strict(self):
        for value in ("", " padded", "padded ", "a\tb", "a\x7fb", "a"*129):
            with self.assertRaises(log.InputError):
                self.evaluate(self.body(device=value))

    def test_headers_missing_extra_duplicate_unknown_rejected(self):
        for header in (HEADER.rstrip().rsplit(",", 1)[0]+"\n", HEADER.rstrip()+",extra\n",
                       HEADER.replace("device", "wafer"), HEADER.replace("device", "unknown")):
            self.write(header=header)
            with self.assertRaises(log.InputError):
                log.evaluate(self.input, "V", "nA", "0", ".2", "positive", True)

    def test_reordered_header_and_bom(self):
        records = list(csv.reader(io.StringIO(HEADER + self.body())))
        self.input.write_text("\ufeff" + "".join(",".join(reversed(r))+"\n" for r in records), encoding="utf-8")
        report = log.evaluate(self.input, "V", "nA", "0", ".2", "positive", True)
        self.assertEqual(report["results"][0]["fit"]["slope_decades_per_V"], 10)

    def test_empty_lines_skip_but_whitespace_rejected(self):
        self.assertEqual(self.single("\n" + self.body())["source_rows"], [3, 4, 5])
        with self.assertRaises(log.InputError):
            self.evaluate(" \n" + self.body())

    def test_empty_input_and_bad_width(self):
        for body in ("", "\n", self.body()+"one,two\n", self.body()+","*11+"\n"):
            with self.assertRaises(log.InputError):
                self.evaluate(body)

    def test_invalid_utf8_and_quotes(self):
        for content in (b"\xff", (HEADER+'"unclosed').encode()):
            self.input.write_bytes(content)
            with self.assertRaises(log.InputError):
                log.evaluate(self.input, "V", "nA", "0", ".2", "positive", True)

    def test_missing_or_directory_safe(self):
        for path in (self.directory/"SECRET_PATH", self.directory):
            with self.assertRaises(log.InputError) as cm:
                log.evaluate(path, "V", "nA", "0", ".2", "positive", True)
            self.assertNotIn(str(path), str(cm.exception))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO only")
    def test_fifo_input_not_blocked(self):
        os.mkfifo(self.input)
        with self.assertRaises(log.InputError):
            log.evaluate(self.input, "V", "nA", "0", ".2", "positive", True)

    def test_resource_limits(self):
        for name, limit, body in (("MAX_BYTES", 1, self.body()), ("MAX_ROWS", 2, self.body()),
                                  ("MAX_GROUP_ROWS", 2, self.body()), ("MAX_GROUPS", 1, self.body()+self.body(device="other"))):
            with patch.object(log, name, limit), self.assertRaises(log.InputError):
                self.evaluate(body)

    def test_input_hash_raw_text_source_rows_no_mutation(self):
        self.write()
        before = self.input.read_bytes()
        report = log.evaluate(self.input, "V", "nA", "0", ".2", "positive", True)
        self.assertEqual(report["provenance"]["input_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(report["results"][0]["source_rows"], [2, 3, 4])
        self.assertEqual(self.input.read_bytes(), before)
        self.assertEqual(report["parameters"]["log_base"], 10)
        self.assertEqual(report["parameters"]["window_search"], "not_performed")

    def test_multiline_numeric_record_end_line_convention(self):
        body = self.body().replace(",0,1\n", ',"\n0",1\n', 1)
        r = self.single(body)
        self.assertEqual(r["source_rows"], [3, 4, 5])
        self.assertEqual(r["observations"][0]["input_numeric_text"]["gate_voltage"], "\n0")

    def test_cli_stdout_warning_not_rejection(self):
        result = self.cli(("--stdout",), self.body(currents=(1, 100, 10)))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["counts"]["extracted_groups"], 1)

    def test_cli_mixed_groups_returns_one_and_full_audit(self):
        result = self.cli(("--stdout",), self.body()+self.body(currents=(0, 10, 100), device="other"))
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"]["fitted_rows"], 3)
        self.assertEqual(report["counts"]["inside_window_rows"], 6)
        self.assertEqual(report["counts"]["rejected_by_reason"], {"zero_current_in_user_window": 1})

    def test_cli_requires_output_assumptions_and_all_declarations(self):
        for extra, accepted in (((), True), (("--stdout",), False), (("--stdout", "--output", "other.json"), True)):
            result = self.cli(extra, assumptions=accepted)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
        for option in ("--window-min", "--window-max", "--current-polarity", "--current-unit", "--voltage-unit"):
            args = [str(self.input), "--window-min", "0", "--window-max", ".2", "--current-polarity", "positive",
                    "--current-unit", "nA", "--voltage-unit", "V", "--stdout", "--accept-log-transfer-assumptions"]
            index = args.index(option)
            del args[index:index+2]
            result = subprocess.run([sys.executable, str(MODULE)]+args, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_cli_private_argument_and_numeric_errors(self):
        for extra in (("--current-unit", "SECRET", "--stdout"), ("--SECRET_ARG", "SECRET_VAL", "--stdout"),
                      ("--window-mi", "0", "--stdout")):
            result = self.cli(extra)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("SECRET", result.stderr)
            self.assertNotIn(str(self.input), result.stderr)
        result = self.cli(("--stdout",), self.body(currents=(1, "SECRET_VALUE", 100)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("SECRET", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_exclusive_owner_only_output(self):
        output = self.directory/"report.json"
        result = self.cli(("--output", str(output)))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(output.read_text())["tool"], "transfer-log-slope")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        before = output.read_bytes()
        result = self.cli(("--output", str(output)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output.read_bytes(), before)
        self.assertNotIn(str(output), result.stderr)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks required")
    def test_live_dangling_output_symlinks_never_followed(self):
        for existing in (True, False):
            target = self.directory/("existing" if existing else "missing")
            if existing:
                target.write_text("unchanged")
            link = self.directory/("live_link" if existing else "dangling_link")
            link.symlink_to(target)
            result = self.cli(("--output", str(link)))
            self.assertEqual(result.returncode, 2)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(), "unchanged") if existing else self.assertFalse(target.exists())

    def test_output_cannot_overwrite_input(self):
        self.write()
        before = self.input.read_bytes()
        result = self.cli(("--output", str(self.input)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.input.read_bytes(), before)

    def test_help_version_without_input(self):
        for flag in ("--help", "--version"):
            result = subprocess.run([sys.executable, str(MODULE), flag], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")

    def test_fixture_generator_and_expected_results(self):
        generated = subprocess.run([sys.executable, str(MODULE.parent/"examples/generate_synthetic.py")], capture_output=True, timeout=10)
        fixture = MODULE.parent/"examples/synthetic_transfer.csv"
        self.assertEqual(generated.returncode, 0)
        self.assertEqual(generated.stdout, fixture.read_bytes())
        report = log.evaluate(fixture, "V", "nA", ".1", ".4", "positive", True)
        self.assertEqual(report["counts"]["input_rows"], 24)
        self.assertEqual(report["counts"]["input_groups"], 4)
        self.assertEqual(report["counts"]["distinct_physical_device_ids"], 3)
        self.assertEqual(report["counts"]["fitted_rows"], 16)
        self.assertEqual(report["counts"]["outside_window_rows"], 8)
        self.assertEqual(report["counts"]["rejected_groups"], 0)
        self.assertEqual([r["fit"]["slope_decades_per_V"] for r in report["results"][:2]], [10, 10])
        self.assertEqual(report["results"][-1]["reciprocal_slope"]["signed_mV_per_decade"], -100)
        self.assertGreater(report["results"][2]["fit"]["rmse_decades"], 0)
        json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
