"""Invented-data tests of software behavior, not laboratory validation."""
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

MODULE = Path(__file__).resolve().parents[1] / "tlm_extract.py"
spec = importlib.util.spec_from_file_location("tlm_extract", MODULE)
tlm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tlm)
HEADER = ",".join(tlm.COLUMNS) + "\n"


class TLMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.input = self.directory / "input.csv"

    def tearDown(self):
        self.temp.cleanup()

    def body(self, x=(2, 5, 10, 20), y=(30, 60, 110, 210), width=10,
             lot="l", wafer="w", structure="s", condition="c", replicate="r"):
        return "".join(f"{lot},{wafer},{structure},{condition},{replicate},p{i},{a},{width},{b}\n"
                       for i, (a, b) in enumerate(zip(x, y)))

    def write(self, body=None, header=HEADER):
        self.input.write_text(header + (self.body() if body is None else body), encoding="utf-8")
        return self.input

    def evaluate(self, body=None, **kwargs):
        self.write(body)
        params = {"length_unit": "um", "resistance_unit": "ohm", "assumptions_accepted": True}
        params.update(kwargs)
        return tlm.evaluate(self.input, **params)

    def single(self, body=None, **kwargs):
        return self.evaluate(body, **kwargs)["results"][0]

    def cli(self, extra=(), body=None, include_assumptions=True):
        self.write(body)
        args = [sys.executable, str(MODULE), str(self.input), "--length-unit", "um", "--resistance-unit", "ohm"]
        if include_assumptions:
            args.append("--accept-linear-tlm-assumptions")
        return subprocess.run(args + list(extra), capture_output=True, text=True, timeout=10)

    def test_known_sheet_and_per_contact_resistance(self):
        r = self.single()
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["fit"]["slope_ohm_per_m"], 1e7)
        self.assertEqual(r["fit"]["intercept_ohm"], 10)
        self.assertEqual(r["physical_estimates"], {"sheet_resistance_ohm_per_square": 100,
                                                 "per_contact_resistance_ohm": 5})
        self.assertEqual(r["fit"]["r_squared"], 1)
        self.assertEqual(r["fit"]["rmse_ohm"], 0)

    def test_all_unit_combinations(self):
        for lu, factor in tlm.LENGTH_UNITS.items():
            for ru, rfactor in tlm.RESISTANCE_UNITS.items():
                with self.subTest(length=lu, resistance=ru):
                    x = [str(tlm.Decimal(a) / tlm.Decimal(factor)) for a in ("0.000002", "0.000005", "0.000010")]
                    y = [str(tlm.Decimal(a) / tlm.Decimal(rfactor)) for a in (30, 60, 110)]
                    width = str(tlm.Decimal("0.000010") / tlm.Decimal(factor))
                    r = self.single(self.body(x, y, width), length_unit=lu, resistance_unit=ru)
                    self.assertEqual(r["physical_estimates"]["sheet_resistance_ohm_per_square"], 100)
                    self.assertEqual(r["physical_estimates"]["per_contact_resistance_ohm"], 5)

    def test_width_not_spacing_applied_to_sheet(self):
        r = self.single(self.body(width=20))
        self.assertEqual(r["physical_estimates"]["sheet_resistance_ohm_per_square"], 200)
        self.assertEqual(r["physical_estimates"]["per_contact_resistance_ohm"], 5)

    def test_noisy_fit_and_residual_definition(self):
        r = self.single(self.body((1, 2, 3), (4, 4, 10), 2), length_unit="m")
        self.assertEqual(r["fit"]["slope_ohm_per_m"], 3)
        self.assertEqual(r["fit"]["intercept_ohm"], 0)
        self.assertAlmostEqual(r["fit"]["rmse_ohm"], math.sqrt(2))
        self.assertEqual(r["fit"]["r_squared"], 0.75)
        self.assertEqual([p["residual_ohm"] for p in r["observations"]], [1, -2, 1])
        self.assertIn("zero_intercept_not_proof_of_zero_contact_resistance", r["warnings"])

    def test_unsorted_rows_keep_provenance(self):
        r = self.single(self.body((20, 2, 10, 5), (210, 30, 110, 60)))
        self.assertEqual(r["physical_estimates"]["sheet_resistance_ohm_per_square"], 100)
        self.assertEqual([p["spacing_m"] for p in r["observations"]], [20e-6, 2e-6, 10e-6, 5e-6])
        self.assertEqual(r["source_rows"], [2, 3, 4, 5])

    def test_all_group_keys_isolate(self):
        body = self.body()
        for name in tlm.GROUP_COLUMNS:
            body += self.body(y=(40, 70, 120, 220), **{name: "different"})
        report = self.evaluate(body)
        self.assertEqual(report["counts"]["extracted_groups"], 6)
        self.assertEqual(report["results"][0]["physical_estimates"]["per_contact_resistance_ohm"], 5)
        for result in report["results"][1:]:
            self.assertEqual(result["physical_estimates"]["per_contact_resistance_ohm"], 10)

    def test_replicates_warn_without_population_aggregation(self):
        report = self.evaluate(self.body() + self.body(replicate="r2"))
        for r in report["results"]:
            self.assertIn("replicate_groups_not_independent_devices", r["warnings"])
        self.assertNotIn("population_mean", report)

    def test_duplicate_pair_even_identical_rejected(self):
        body = self.body() + self.body().splitlines()[0] + "\n"
        self.assertEqual(self.single(body)["reason"], "duplicate_pair_id")

    def test_repeated_spacing_uses_rows_not_spacing_means(self):
        r = self.single(self.body((1, 2, 3, 3), (3, 5, 7, 9)), length_unit="m")
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["fit"]["slope_ohm_per_m"], 28 / 11)
        self.assertAlmostEqual(r["fit"]["intercept_ohm"], 3 / 11)
        self.assertIn("repeated_spacing_equal_row_weighting", r["warnings"])

    def test_fewer_than_three_distinct_spacings_rejected(self):
        for x in ((1,), (1, 2), (1, 2, 2), (1, 1, 1)):
            with self.subTest(x=x):
                r = self.single(self.body(x, [10] * len(x)))
                self.assertEqual(r["reason"], "fewer_than_three_distinct_spacings")

    def test_negative_intercept_retained_not_clipped(self):
        r = self.single(self.body((1, 2, 3), (1, 3, 5)), length_unit="m")
        self.assertEqual(r["status"], "rejected")
        self.assertEqual(r["fit"]["intercept_ohm"], -1)
        self.assertEqual(r["fit_failures"], ["negative_intercept"])
        self.assertIsNone(r["physical_estimates"])

    def test_negative_slope_retained_not_clipped(self):
        r = self.single(self.body((1, 2, 3), (7, 5, 3)), length_unit="m")
        self.assertEqual(r["fit"]["slope_ohm_per_m"], -2)
        self.assertEqual(r["fit_failures"], ["nonpositive_slope"])
        self.assertIsNone(r["physical_estimates"])

    def test_constant_resistance_no_r_squared(self):
        r = self.single(self.body((1, 2, 3), (5, 5, 5)))
        self.assertEqual(r["fit"]["slope_ohm_per_m"], 0)
        self.assertIsNone(r["fit"]["r_squared"])
        self.assertEqual(r["reason"], "nonphysical_fit")
        self.assertEqual(r["fit"]["rmse_ohm"], 0)

    def test_invalid_geometry_rejects_whole_group(self):
        for field, value in (("spacing", "0"), ("spacing", "-1"), ("width", "0"), ("width", "-1")):
            rows = list(csv.reader(io.StringIO(HEADER + self.body())))
            rows[1][list(tlm.COLUMNS).index(field)] = value
            output = io.StringIO()
            csv.writer(output).writerows(rows[1:])
            with self.subTest(field=field, value=value):
                self.assertEqual(self.single(output.getvalue())["reason"], "nonpositive_geometry")

    def test_nonpositive_resistance_not_silently_dropped(self):
        for value in (0, -1):
            r = self.single(self.body((1, 2, 3), (10, value, 20)))
            self.assertEqual(r["reason"], "nonpositive_measured_resistance")
            self.assertEqual(r["sample_count"], 3)

    def test_exact_width_mismatch_not_hidden_by_float_rounding(self):
        body = self.body().replace(",5,10,60", ",5,10.00000000000000000001,60")
        self.assertEqual(self.single(body)["reason"], "inconsistent_width")

    def test_equivalent_decimal_widths_allowed(self):
        body = self.body().replace(",5,10,60", ",5,1e1,60")
        self.assertEqual(self.single(body)["status"], "ok")

    def test_exact_spacing_cancellation_avoided(self):
        x = ("1", "1.00000000000000000001", "1.00000000000000000002")
        y = ("3", "3.00000000000000000002", "3.00000000000000000004")
        r = self.single(self.body(x, y, 1), length_unit="m")
        self.assertEqual(r["distinct_spacing_count"], 3)
        self.assertEqual(r["fit"]["slope_ohm_per_m"], 2)
        self.assertEqual(r["fit"]["intercept_ohm"], 1)
        self.assertIn("large_zero_spacing_extrapolation", r["warnings"])
        self.assertIn("binary64_spacing_rounding_merges_distinct_inputs", r["warnings"])

    def test_large_zero_spacing_extrapolation_warning(self):
        r = self.single(self.body((100, 101, 102), (101, 102, 103)), length_unit="m")
        self.assertEqual(r["fit"]["zero_spacing_extrapolation_ratio"], 50)
        self.assertIn("large_zero_spacing_extrapolation", r["warnings"])

    def test_rmse_does_not_underflow_through_squared_intermediate(self):
        r = self.single(self.body((1, 2, 3), ("1.000000000000000000000000000001e-150", "2e-150", "3e-150")), length_unit="m")
        self.assertNotEqual(r["fit"]["rmse_ohm"], 0)
        self.assertTrue(math.isfinite(r["fit"]["rmse_ohm"]))

    def test_derived_sheet_overflow_rejected_but_fit_retained(self):
        # Stay inside input bounds while sheet resistance exceeds binary64.
        r = self.single(self.body(("1e-150", "2e-150", "3e-150"), ("2e149", "3e149", "4e149"), "1e150"), length_unit="m")
        self.assertEqual(r["reason"], "derived_numeric_range_error")
        self.assertIsNotNone(r["fit"])
        self.assertIsNone(r["physical_estimates"])

    def test_derived_sheet_underflow_rejected(self):
        r = self.single(self.body(("1e149", "2e149", "3e149"), ("2e-150", "3e-150", "4e-150"), "1e-150"), length_unit="m")
        self.assertEqual(r["reason"], "derived_numeric_range_error")

    def test_nonfinite_malformed_and_extreme_numbers_fail(self):
        for value in ("NaN", "inf", "-Infinity", "1_000", "0x10", "1V", "", "1e1000000000", "1e-1000000000", "1e201", "1e-201", "1e151", "1e-151", "1" * 65, "1.123456789012345678901234567890123"):
            with self.subTest(value=value), self.assertRaises(tlm.InputError):
                self.single(self.body((1, 2, 3), (value, 3, 4)), length_unit="m")

    def test_si_limits_after_unit_conversion(self):
        self.assertEqual(tlm.number("1e159", "1e-9"), tlm.MAX_NUMBER)
        self.assertEqual(tlm.number("1e-156", "1e6"), tlm.MIN_NUMBER)
        with self.assertRaises(tlm.InputError):
            tlm.number("1e-150", "1e-9")

    def test_nonzero_finite_float_underflow_and_overflow_rejected(self):
        for value in (tlm.Fraction(1, 10 ** 400), tlm.Fraction(10 ** 400)):
            with self.assertRaises(tlm.InputError):
                tlm.finite_float(value)

    def test_explicit_assumptions_required_even_api(self):
        for value in (False, None, 1, "yes"):
            with self.subTest(value=value), self.assertRaises(tlm.InputError):
                self.evaluate(assumptions_accepted=value)

    def test_unknown_api_units_rejected(self):
        for params in ({"length_unit": "inch"}, {"resistance_unit": "Ohm"}):
            with self.assertRaises(tlm.InputError):
                self.evaluate(**params)

    def test_header_missing_extra_duplicate_and_empty_fail(self):
        for header in ("", "lot,wafer\n", HEADER.replace("width", "spacing"), HEADER.rstrip() + ",temperature\n"):
            with self.subTest(header=header), self.assertRaises(tlm.InputError):
                self.write("", header)
                tlm.evaluate(self.input, "um", "ohm", True)

    def test_header_can_reorder(self):
        rows = list(csv.reader(io.StringIO(HEADER + self.body())))
        output = io.StringIO()
        csv.writer(output).writerows([list(reversed(row)) for row in rows])
        self.input.write_text(output.getvalue(), encoding="utf-8")
        self.assertEqual(tlm.evaluate(self.input, "um", "ohm", True)["counts"]["extracted_groups"], 1)

    def test_bad_row_width_empty_cells_and_quote_fail(self):
        for body in ("l,w,s,c,r,p,1,10\n", "l,w,s,c,r,p,1,10,3,x\n", 'l,w,s,c,r,p,1,10,"3\n', ",,,,,,,,\n", "  \n"):
            with self.subTest(body=body), self.assertRaises(tlm.InputError):
                self.single(body)

    def test_blank_lines_skipped_but_row_numbers_preserved(self):
        r = self.single("\n" + self.body().replace("\n", "\n\n"))
        self.assertEqual(r["source_rows"], [3, 5, 7, 9])

    def test_empty_measurements_fail(self):
        with self.assertRaises(tlm.InputError):
            self.evaluate("")

    def test_identifier_validation(self):
        for value in ("", " l", "l ", "l\t", "l\x00", "l" * 129):
            with self.subTest(value=value), self.assertRaises(tlm.InputError):
                self.single(self.body(lot=value))

    def test_utf8_bom_supported(self):
        self.input.write_text("\ufeff" + HEADER + self.body(lot="晶片"), encoding="utf-8")
        self.assertEqual(tlm.evaluate(self.input, "um", "ohm", True)["results"][0]["lot"], "晶片")

    def test_input_hash_matches_exact_bytes_without_input_path(self):
        report = self.evaluate()
        self.assertEqual(report["provenance"]["input_sha256"], hashlib.sha256(self.input.read_bytes()).hexdigest())
        self.assertNotIn(str(self.input), json.dumps(report))

    def test_bounded_input_bytes_rows_groups_and_group_rows(self):
        for limit, value in (("MAX_BYTES", 5), ("MAX_ROWS", 3), ("MAX_GROUP_ROWS", 3), ("MAX_GROUPS", 0)):
            with self.subTest(limit=limit), patch.object(tlm, limit, value), self.assertRaises(tlm.InputError):
                self.evaluate()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO test")
    def test_fifo_input_rejected_without_blocking(self):
        fifo = self.directory / "input.fifo"
        os.mkfifo(fifo)
        with self.assertRaises(tlm.InputError):
            tlm.evaluate(fifo, "um", "ohm", True)

    def test_invalid_group_does_not_hide_valid_group(self):
        report = self.evaluate(self.body() + self.body((1, 2, 3), (1, 3, 5), structure="bad"))
        self.assertEqual(report["counts"]["extracted_groups"], 1)
        self.assertEqual(report["counts"]["rejected_groups"], 1)
        self.assertEqual(report["counts"]["rejected_by_reason"], {"nonphysical_fit": 1})

    def test_cli_requires_output_and_model_declaration(self):
        for args, accepted in (([], True), (["--stdout"], False)):
            r = self.cli(args, include_assumptions=accepted)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(r.stdout, "")
            self.assertNotIn(str(self.input), r.stderr)

    def test_cli_stdout_success(self):
        r = self.cli(["--stdout"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["counts"]["extracted_groups"], 1)
        self.assertEqual(r.stderr, "")

    def test_cli_rejected_group_exit_one(self):
        r = self.cli(["--stdout"], self.body((1, 2, 3), (1, 3, 5)))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout)["results"][0]["reason"], "nonphysical_fit")

    def test_cli_fatal_input_is_private(self):
        r = self.cli(["--stdout"], self.body(lot="PRIVATE-LOT", y=("SECRET-NOT-NUMERIC", 60, 110, 210)))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertNotIn("PRIVATE-LOT", r.stderr)
        self.assertNotIn("SECRET", r.stderr)
        self.assertNotIn(str(self.input), r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_cli_unknown_args_are_private(self):
        r = self.cli(["--stdout", "--private-secret-argument"])
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("private-secret", r.stderr)

    def test_exclusive_output_mode_and_never_overwrite(self):
        output = self.directory / "report.json"
        r = self.cli(["--output", str(output)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        original = output.read_bytes()
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        r = self.cli(["--output", str(output)])
        self.assertEqual(r.returncode, 2)
        self.assertEqual(output.read_bytes(), original)

    def test_symlink_and_dangling_symlink_not_overwritten(self):
        target = self.directory / "target.json"
        target.write_text("unchanged", encoding="utf-8")
        for name, destination in (("existing", target), ("dangling", self.directory / "absent")):
            link = self.directory / name
            link.symlink_to(destination)
            r = self.cli(["--output", str(link)])
            self.assertEqual(r.returncode, 2)
            self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(), "unchanged")
        self.assertFalse((self.directory / "absent").exists())

    def test_input_cannot_be_overwritten(self):
        r = self.cli(["--output", str(self.input)])
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.input.read_text(), HEADER + self.body())

    def test_documented_synthetic_fixture(self):
        path = MODULE.parent / "examples" / "synthetic_tlm.csv"
        report = tlm.evaluate(path, "um", "ohm", True)
        self.assertEqual(report["counts"]["extracted_groups"], 2)
        self.assertEqual([r["physical_estimates"]["per_contact_resistance_ohm"] for r in report["results"]], [5, 6])


if __name__ == "__main__":
    unittest.main()
