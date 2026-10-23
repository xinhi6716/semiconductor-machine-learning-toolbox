"""Original invented-data software tests, not laboratory/device validation."""
import csv
from decimal import Decimal
from fractions import Fraction
import hashlib
import importlib.util
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

MODULE = Path(__file__).resolve().parents[1] / "vth_constant_current.py"
spec = importlib.util.spec_from_file_location("vth_constant_current", MODULE)
vth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vth)
HEADER = ",".join(vth.COLUMNS) + "\n"


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.input = self.directory / "invented.csv"

    def tearDown(self):
        self.temp.cleanup()

    def body(self, vg=(0, ".4", ".8", "1.2"), current=(0, ".2", "1.8", "4.5"),
             temperature="300", vd=".05", vb="0", **identifiers):
        key = {"lot": "lot", "wafer": "wafer", "device": "device", "condition": "condition",
               "replicate": "r1", "branch": "forward"}
        key.update(identifiers)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        for voltage, drain_current in zip(vg, current):
            writer.writerow([*(key[k] for k in vth.GROUP_COLUMNS), temperature, vd, vb,
                             voltage, drain_current])
        return stream.getvalue()

    def write(self, body=None, header=HEADER):
        self.input.write_text(header + (self.body() if body is None else body), encoding="utf-8")
        return self.input

    def evaluate(self, body=None, **kwargs):
        self.write(body)
        params = {"voltage_unit": "V", "current_unit": "uA", "criterion_current": "1",
                  "criterion_current_unit": "uA", "current_polarity": "positive",
                  "assumptions_accepted": True}
        params.update(kwargs)
        return vth.evaluate(self.input, **params)

    def single(self, body=None, **kwargs):
        return self.evaluate(body, **kwargs)["results"][0]

    def rejected(self, reason, body=None, **kwargs):
        result = self.single(body, **kwargs)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], reason)
        self.assertIsNone(result["extraction"])
        self.assertTrue(result["observations"])

    def cli(self, extra=(), body=None, include_assumptions=True, overrides=()):
        self.write(body)
        args = [sys.executable, str(MODULE), str(self.input), "--voltage-unit", "V",
                "--current-unit", "uA", "--criterion-current", "1", "--criterion-current-unit",
                "uA", "--current-polarity", "positive"]
        if include_assumptions:
            args.append("--accept-transfer-assumptions")
        return subprocess.run(args + list(overrides) + list(extra), capture_output=True, text=True, timeout=10)

    def test_linear_current_not_log_interpolation(self):
        result = self.single()
        self.assertEqual(result["status"], "ok")
        extraction = result["extraction"]
        self.assertEqual(extraction["gate_source_voltage_at_criterion_V"], .6)
        self.assertEqual(extraction["method"], "linear_current_interpolation")
        self.assertEqual(extraction["fraction_from_first_to_second_sample"], .5)
        self.assertEqual(extraction["bracket_span_V"], .4)
        self.assertEqual(extraction["source_rows"], [3, 4])
        self.assertEqual(extraction["bracket_drain_current_A"], [.2e-6, 1.8e-6])
        self.assertNotIn("binary64_rounding_hides_interior_interpolation_fraction", result["warnings"])

    def test_descending_acquisition_bracket_preserved(self):
        result = self.single(self.body(vg=(1.2, .8, .4, 0), current=(4.5, 1.8, .2, 0)))
        self.assertEqual(result["acquisition_direction"], "decreasing_vgs")
        self.assertEqual(result["extraction"]["bracket_gate_source_voltage_V"], [.8, .4])
        self.assertEqual(result["extraction"]["gate_source_voltage_at_criterion_V"], .6)

    def test_negative_current_and_voltage_not_abs(self):
        result = self.single(self.body(vg=(0, "-.4", "-.8"), current=(0, "-.2", "-1.8"), vd="-.05"),
                             current_polarity="negative", criterion_current="-1")
        self.assertEqual(result["extraction"]["gate_source_voltage_at_criterion_V"], -.6)
        self.assertEqual(result["extraction"]["signed_current_criterion_A"], -1e-6)
        self.assertEqual(result["conditions"]["drain_source_voltage_V"], -.05)

    def test_polarity_not_transistor_type_or_voltage_sign(self):
        result = self.single(self.body(vg=(-1, 0, 1), current=(0, .5, 1.5), vd="-.05", vb="1"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["extraction"]["gate_source_voltage_at_criterion_V"], .5)

    def test_exact_zero_voltage_hit_is_valid(self):
        result = self.single(self.body(vg=(-1, 0, 1), current=(0, 1, 2)))
        extraction = result["extraction"]
        self.assertEqual(extraction["gate_source_voltage_at_criterion_V"], 0)
        self.assertEqual(extraction["method"], "measured_exact_criterion")
        self.assertEqual(extraction["source_rows"], [3])
        self.assertEqual(extraction["bracket_span_V"], 0)
        self.assertIsNone(extraction["fraction_from_first_to_second_sample"])
        self.assertNotIn("binary64_rounding_hides_interior_interpolation_fraction", result["warnings"])

    def test_exact_endpoint_hits(self):
        for currents, voltage in (((1, 2, 3), 0), ((0, .5, 1), 2)):
            with self.subTest(currents=currents):
                result = self.single(self.body(vg=(0, 1, 2), current=currents))
                self.assertEqual(result["extraction"]["gate_source_voltage_at_criterion_V"], voltage)

    def test_all_voltage_input_current_and_criterion_units(self):
        for vu, vf in vth.VOLTAGE_UNITS.items():
            for iu, factor in vth.CURRENT_UNITS.items():
                for cu, cf in vth.CURRENT_UNITS.items():
                    with self.subTest(voltage=vu, current=iu, criterion=cu):
                        scale = lambda value, unit: str(Decimal(value) / Decimal(unit))
                        result = self.single(self.body(vg=[scale(v, vf) for v in (".4", ".8")],
                            current=[scale(i, factor) for i in (".0000002", ".0000018")],
                            vd=scale(".05", vf)), voltage_unit=vu, current_unit=iu,
                            criterion_current=scale(".000001", cf), criterion_current_unit=cu)
                        self.assertEqual(result["extraction"]["gate_source_voltage_at_criterion_V"], .6)

    def test_hysteresis_branches_remain_separate(self):
        body = self.body() + self.body(vg=(".9", ".5"), current=("1.8", ".2"), branch="reverse")
        report = self.evaluate(body)
        self.assertEqual(report["counts"]["input_groups"], 2)
        self.assertEqual([r["extraction"]["gate_source_voltage_at_criterion_V"] for r in report["results"]], [.6, .7])
        self.assertNotIn("average", report)

    def test_all_group_columns_isolate(self):
        for field in vth.GROUP_COLUMNS:
            with self.subTest(field=field):
                report = self.evaluate(self.body() + self.body(**{field: "different"}))
                self.assertEqual(report["counts"]["extracted_groups"], 2)

    def test_interleaved_groups_keep_order(self):
        a = self.body().splitlines(True)
        b = self.body(branch="reverse").splitlines(True)
        result = self.evaluate("".join(x + y for x, y in zip(a, b)))
        self.assertEqual(result["results"][0]["extraction"]["source_rows"], [4, 6])
        self.assertEqual(result["results"][1]["extraction"]["source_rows"], [5, 7])

    def test_branch_filter_and_counts(self):
        report = self.evaluate(self.body() + self.body(branch="reverse"), branch="forward")
        self.assertEqual(report["counts"]["selected_groups"], 1)
        self.assertEqual(report["counts"]["excluded_by_branch_filter"], 1)
        self.assertEqual(report["counts"]["input_rows"], 8)
        with self.assertRaises(vth.InputError):
            self.evaluate(branch="not-present")

    def test_single_point_rejected_even_at_criterion(self):
        self.rejected("fewer_than_two_samples", self.body(vg=(.6,), current=(1,)))

    def test_unbracketed_not_extrapolated(self):
        for current in ((0, .1, .2), (2, 3, 4)):
            self.rejected("criterion_not_bracketed", self.body(vg=(0, 1, 2), current=current))

    def test_multiple_interior_crossings_rejected(self):
        self.rejected("nonunique_current_crossing", self.body(vg=(0, 1, 2, 3), current=(0, 2, 0, 2)))

    def test_plateau_at_criterion_rejected(self):
        for current in ((0, 1, 1, 2), (1, 1, 1, 1)):
            self.rejected("nonunique_current_crossing", self.body(current=current))

    def test_exact_plus_interior_crossing_rejected(self):
        self.rejected("nonunique_current_crossing", self.body(current=(0, 1, 2, 0)))

    def test_multiple_exact_hits_rejected(self):
        self.rejected("nonunique_current_crossing", self.body(current=(0, 1, 2, 1)))

    def test_unique_crossing_nonmonotonic_current_rejected(self):
        self.rejected("nonmonotonic_drain_current", self.body(current=(0, .5, .4, 2)))

    def test_flat_current_away_from_criterion_allowed(self):
        self.assertEqual(self.single(self.body(current=(0, 0, .5, 2)))["status"], "ok")

    def test_current_can_decrease_with_increasing_gate(self):
        result = self.single(self.body(vg=(0, 1, 2), current=(2, 1.5, .5)))
        self.assertEqual(result["current_direction"], "decreasing_or_flat")
        self.assertEqual(result["extraction"]["gate_source_voltage_at_criterion_V"], 1.5)

    def test_no_sorting_repairs_gate_reversal(self):
        self.rejected("nonmonotonic_gate_voltage", self.body(vg=(0, 2, 1, 3)))

    def test_duplicate_gate_including_equal_decimal_spellings(self):
        self.rejected("duplicate_gate_voltage", self.body(vg=(0, "0.4", "0.40", 1)))

    def test_current_sign_mismatch_is_not_abs(self):
        self.rejected("current_sign_conflicts_with_declared_polarity", self.body(current=(-.01, .2, 1.8, 4.5)))

    def test_temperature_and_bias_checks(self):
        for value in ("0", "-1"):
            self.rejected("nonpositive_temperature", self.body(temperature=value))
        self.rejected("zero_drain_bias", self.body(vd="0"))
        for field, replacement in ((6, "301"), (7, ".06"), (8, ".1")):
            lines = list(csv.reader(io.StringIO(self.body())))
            lines[-1][field] = replacement
            body = "\n".join(",".join(line) for line in lines) + "\n"
            self.rejected("inconsistent_temperature_or_bias", body)

    def test_no_tolerance_in_fixed_conditions(self):
        body = self.body().replace(",300,", ",300.00000000000000000000000000001,", 1)
        self.rejected("inconsistent_temperature_or_bias", body)

    def test_max_span_inclusive_in_voltage_unit(self):
        self.assertEqual(self.single(max_bracket_span=".4")["status"], "ok")
        self.rejected("bracket_span_exceeds_limit", max_bracket_span=".39999999999999999999999999999")
        result = self.single(self.body(vg=(400, 800), current=(.2, 1.8), vd="50"),
                             voltage_unit="mV", max_bracket_span="400")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["extraction"]["bracket_span_V"], .4)

    def test_exact_hit_not_limited_by_neighbor_distance(self):
        result = self.single(self.body(vg=(0, 1, 100), current=(0, 1, 2)), max_bracket_span=".001")
        self.assertEqual(result["status"], "ok")

    def test_invalid_criteria_polarity_units_and_declarations(self):
        for kwargs in ({"criterion_current": "0"}, {"criterion_current": "-1"},
                       {"current_polarity": "nmos"}, {"assumptions_accepted": False},
                       {"assumptions_accepted": 1}, {"voltage_unit": "kV"},
                       {"current_unit": "A/mm"}, {"criterion_current_unit": "amp"},
                       {"max_bracket_span": "0"}, {"max_bracket_span": "-1"}, {"branch": " padded"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(vth.InputError):
                self.evaluate(**kwargs)

    def test_exact_decimal_hits_not_binary_float_hits(self):
        result = self.single(self.body(vg=(0, 1), current=("1.00000000000000000001", "1.00000000000000000003")),
                             criterion_current="1.00000000000000000002")
        self.assertEqual(result["extraction"]["gate_source_voltage_at_criterion_V"], .5)
        self.assertEqual(result["extraction"]["method"], "linear_current_interpolation")
        self.assertIn("binary64_drain_current_rounding_merges_distinct_inputs", result["warnings"])
        self.assertIn("binary64_rounding_hides_criterion_bracket_separation", result["warnings"])

    def test_near_equal_gate_voltages_not_false_duplicates(self):
        result = self.single(self.body(vg=("1.00000000000000000001", "1.00000000000000000003"), current=(0, 2)))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["extraction"]["bracket_span_V"], 2e-20)
        self.assertIn("binary64_gate_voltage_rounding_merges_distinct_inputs", result["warnings"])

    def test_extreme_values_avoid_overflowing_intermediate(self):
        result = self.single(self.body(vg=("-1e150", "1e150"), current=(0, "1e150")),
                             current_unit="A", criterion_current="5e149", criterion_current_unit="A")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["extraction"]["gate_source_voltage_at_criterion_V"], 0)
        self.assertEqual(result["extraction"]["bracket_span_V"], 2e150)

    def test_derived_nonzero_underflow_rejected(self):
        for gate_sign in ("", "-"):
            for current_sign, polarity in (("", "positive"), ("-", "negative")):
                for criterion in ("2e-150", "1.0000000000000000000000000000001e-150"):
                    for reverse in (False, True):
                        with self.subTest(gate_sign=gate_sign, polarity=polarity,
                                          criterion=criterion, reverse=reverse):
                            vg = ("0", gate_sign + "1e-150")
                            current = (current_sign + "1e-150", current_sign + "1e150")
                            if reverse:
                                vg, current = vg[::-1], current[::-1]
                            self.rejected("derived_numeric_range_error", self.body(vg=vg, current=current),
                                          current_unit="A", criterion_current=current_sign + criterion,
                                          criterion_current_unit="A", current_polarity=polarity)

    def test_fraction_underflow_keeps_representable_threshold_in_both_orders(self):
        expected = float(Fraction(10 ** 119, 10 ** 300 - 1))
        self.assertEqual(expected, 1e-181)
        warning = "binary64_rounding_hides_interior_interpolation_fraction"
        for gate_sign in ("", "-"):
            for current_sign, polarity in (("", "positive"), ("-", "negative")):
                for reverse in (False, True):
                    with self.subTest(gate_sign=gate_sign, polarity=polarity, reverse=reverse):
                        vg = ("0", gate_sign + "1e150")
                        current = (current_sign + "1e-150", current_sign + "1e150")
                        if reverse:
                            vg, current = vg[::-1], current[::-1]
                        result = self.single(self.body(vg=vg, current=current), current_unit="A",
                            criterion_current=current_sign + "1.0000000000000000000000000000001e-150",
                            criterion_current_unit="A", current_polarity=polarity)
                        self.assertEqual(result["status"], "ok")
                        extraction = result["extraction"]
                        self.assertEqual(extraction["gate_source_voltage_at_criterion_V"],
                                         -expected if gate_sign else expected)
                        self.assertEqual(extraction["fraction_from_first_to_second_sample"],
                                         1.0 if reverse else 0.0)
                        self.assertEqual(extraction["method"], "linear_current_interpolation")
                        self.assertEqual(extraction["source_rows"], [2, 3])
                        self.assertEqual(extraction["bracket_gate_source_voltage_V"], list(map(float, vg)))
                        self.assertEqual(extraction["bracket_drain_current_A"], list(map(float, current)))
                        self.assertIn(warning, result["warnings"])
                        json.dumps(result, allow_nan=False)

    def test_representable_subnormal_threshold_in_both_orders(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                vg, current = ("0", "1e-150"), ("1e-150", "1e150")
                if reverse:
                    vg, current = vg[::-1], current[::-1]
                result = self.single(self.body(vg=vg, current=current), current_unit="A",
                                     criterion_current="1e-23", criterion_current_unit="A")
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["extraction"]["gate_source_voltage_at_criterion_V"], 1e-323)

    def test_cli_fraction_underflow_order_invariance(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                vg, current = ("0", "1e150"), ("1e-150", "1e150")
                if reverse:
                    vg, current = vg[::-1], current[::-1]
                result = self.cli(["--stdout"], self.body(vg=vg, current=current), overrides=(
                    "--current-unit", "A", "--criterion-current",
                    "1.0000000000000000000000000000001e-150", "--criterion-current-unit", "A"))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "")
                report = json.loads(result.stdout)
                self.assertEqual(report["counts"]["extracted_groups"], 1)
                self.assertEqual(report["counts"]["rejected_groups"], 0)
                self.assertEqual(report["results"][0]["extraction"]["gate_source_voltage_at_criterion_V"], 1e-181)

    def test_number_boundaries_after_unit_conversion(self):
        self.assertEqual(vth.number("1e-150", "1"), Fraction(1, 10 ** 150))
        self.assertEqual(vth.number("1e153", ".001"), Fraction(10 ** 150))
        self.assertEqual(vth.number("0e-200", "1"), 0)
        for token, factor in (("1e-151", "1"), ("1e151", "1"), ("1e-150", ".001"),
                              ("0e-201", "1"), ("1" * 33, "1"), ("1e99999", "1")):
            with self.subTest(token=token), self.assertRaises(vth.InputError):
                vth.number(token, factor)

    def test_unsupported_numeric_syntax(self):
        for token in ("nan", "NaN", "inf", "Infinity", "0x1", "1_000", "", ".", "1,2", "1e", "1" * 65):
            with self.subTest(token=token), self.assertRaises(vth.InputError):
                self.evaluate(self.body(current=(0, token, 2, 3)))
        self.assertEqual(vth.number(" +1.20e-3 ", "1000"), Fraction(6, 5))

    def test_finite_float_rejects_nonfinite_or_nonzero_zero(self):
        for value in (Fraction(10 ** 400), Fraction(1, 10 ** 400), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(value_type=type(value)), self.assertRaises(vth.InputError):
                vth.finite_float(value)

    def test_hash_and_exact_cli_parameter_provenance(self):
        report = self.evaluate(criterion_current=" 1.00000000000000000000001 ", max_bracket_span=" .5000 ")
        self.assertEqual(report["provenance"]["input_sha256"], hashlib.sha256(self.input.read_bytes()).hexdigest())
        self.assertEqual(report["parameters"]["criterion_current_input"], "1.00000000000000000000001")
        self.assertEqual(report["parameters"]["max_bracket_span_input"], ".5000")
        self.assertNotIn(str(self.input), json.dumps(report))

    def test_bom_blank_lines_header_order_and_quoted_identifier(self):
        rows = list(csv.reader(io.StringIO(HEADER + self.body(device="invented,quoted"))))
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        for row in rows:
            writer.writerow(list(reversed(row)))
        self.input.write_text("\ufeff" + stream.getvalue() + "\n", encoding="utf-8")
        report = vth.evaluate(self.input, "V", "uA", "1", "uA", "positive", True)
        self.assertEqual(report["results"][0]["device"], "invented,quoted")
        self.assertEqual(report["counts"]["input_rows"], 4)

    def test_invalid_headers_width_and_empty_input(self):
        bad = ("", HEADER, HEADER + "\n", HEADER.replace("lot", "device", 1) + self.body(),
               HEADER.rstrip() + ",extra\n" + self.body(), HEADER + ",\n", HEADER + " \n",
               HEADER + '"unterminated\n')
        for text in bad:
            with self.subTest(text_length=len(text)):
                self.input.write_text(text, encoding="utf-8")
                with self.assertRaises(vth.InputError):
                    vth.load_csv(self.input, "V", "uA")

    def test_invalid_identifiers(self):
        for identifier in ("", " padded", "padded ", "a\tb", "a\nb", "a\x7fb", "x" * 129):
            with self.subTest(length=len(identifier)), self.assertRaises(vth.InputError):
                self.evaluate(self.body(device=identifier))

    def test_invalid_utf8_missing_file_directory_and_fifo(self):
        self.input.write_bytes(b"\xff")
        for path in (self.input, self.directory / "missing", self.directory):
            with self.subTest(target_type=str(path.name)), self.assertRaises(vth.InputError):
                vth.load_csv(path, "V", "uA")
        if hasattr(os, "mkfifo"):
            fifo = self.directory / "pipe"
            os.mkfifo(fifo)
            with self.assertRaises(vth.InputError):
                vth.load_csv(fifo, "V", "uA")

    def test_resource_limits(self):
        self.write()
        for name, limit in (("MAX_BYTES", 10), ("MAX_ROWS", 3), ("MAX_GROUP_ROWS", 3), ("MAX_GROUPS", 0)):
            with self.subTest(name=name), patch.object(vth, name, limit), self.assertRaises(vth.InputError):
                vth.load_csv(self.input, "V", "uA")

    def test_cli_explicit_destination_and_assumptions(self):
        for result in (self.cli(), self.cli(["--stdout"], include_assumptions=False),
                       self.cli(["--stdout", "--output", str(self.directory / "both.json")])):
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_cli_stdout_and_exit_rejection_counts(self):
        result = self.cli(["--stdout"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"]["extracted_groups"], 1)
        body = self.body() + self.body(branch="bad", current=(0, 2, 0, 2))
        result = self.cli(["--stdout"], body)
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["counts"]["rejected_by_reason"], {"nonunique_current_crossing": 1})
        self.assertEqual(report["counts"]["extracted_groups"], 1)

    def test_private_errors_and_no_raw_path_value_or_traceback(self):
        secret = "private-marker-918337"
        for result in (self.cli(["--stdout"], self.body(device=secret, current=(0, secret, 2, 3))),
                       self.cli(["--stdout", "--unknown-" + secret]),
                       self.cli(["--output", str(self.directory / secret / "report.json")])):
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(secret, result.stderr)
            self.assertNotIn(str(self.directory), result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(result.stdout, "")

    def test_file_creation_owner_permissions_and_input_unchanged(self):
        output = self.directory / "report.json"
        result = self.cli(["--output", str(output)])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.input.read_text(), HEADER + self.body())
        self.assertEqual(json.loads(output.read_text())["schema_version"], 1)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_output_refuses_existing_files_input_directory_and_symlinks(self):
        existing = self.directory / "keep.json"
        existing.write_text("DO NOT CHANGE")
        dangling = self.directory / "dangling.json"
        dangling.symlink_to(self.directory / "missing-target")
        link = self.directory / "link.json"
        link.symlink_to(existing)
        for output in (existing, self.input, self.directory, dangling, link):
            with self.subTest(output=output.name):
                result = self.cli(["--output", str(output)])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
        self.assertEqual(existing.read_text(), "DO NOT CHANGE")
        self.assertEqual(self.input.read_text(), HEADER + self.body())
        self.assertFalse((self.directory / "missing-target").exists())

    def test_filtered_invalid_rows_still_fatal(self):
        with self.assertRaises(vth.InputError):
            self.evaluate(self.body() + self.body(branch="excluded", current=(0, "bad-number", 2, 3)), branch="forward")

    def test_repository_fixture(self):
        example = MODULE.parent / "examples" / "synthetic_transfer.csv"
        report = vth.evaluate(example, "V", "uA", "1", "uA", "positive", True)
        self.assertEqual([r["extraction"]["gate_source_voltage_at_criterion_V"] for r in report["results"]], [.6, .7])
        self.assertEqual(report["parameters"]["current_normalization"], "none")
        json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
