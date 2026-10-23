"""Original offline software tests; no experimental, predictive or causal validation."""
import contextlib
import csv
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import importlib.util
import io
import itertools
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "iv_loop_integral.py"
spec = importlib.util.spec_from_file_location("iv_loop_integral", MODULE)
loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loop)
genspec = importlib.util.spec_from_file_location("iv_loop_synthetic", ROOT / "examples/generate_synthetic.py")
gen = importlib.util.module_from_spec(genspec)
genspec.loader.exec_module(gen)
CASES = json.loads((ROOT / "examples/expected.json").read_text())["cases"]
F = Fraction


class IVLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.input = self.directory / "private_input.csv"

    def tearDown(self):
        self.temp.cleanup()

    def rows(self, points=((0, 0), (1, 3), (2, 4), (1, 1), (0, 0)), labels=None, key=None):
        key = list(key or ("lot", "wafer", "device", "condition", "001", "replicate"))
        labels = labels if labels is not None else ["up", "up", "down", "down"]
        return [key + ["p" + str(i), labels[i - 1] if i < len(points) else "", str(v), str(current)]
                for i, (v, current) in enumerate(points, 1)]

    def write(self, rows=None, header=None, newline="\n", bom=False):
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator=newline)
        writer.writerow(header or loop.COLUMNS)
        writer.writerows(self.rows() if rows is None else rows)
        raw = (b"\xef\xbb\xbf" if bom else b"") + buffer.getvalue().encode()
        self.input.write_bytes(raw)
        return self.input

    def evaluate(self, rows=None, **kwargs):
        self.write(rows)
        params = {"voltage_unit": "V", "current_unit": "A", "accept_supplied_path_assumptions": True}
        params.update(kwargs)
        return loop.evaluate(self.input, **params)

    def one(self, rows=None, **kwargs):
        return self.evaluate(rows, **kwargs)["results"][0]

    def cli(self, path=None, extras=("--stdout",), assumptions=True):
        args = [sys.executable, str(MODULE), str(path or self.input), "--voltage-unit", "V", "--current-unit", "A"]
        if assumptions:
            args.append("--accept-supplied-path-assumptions")
        return subprocess.run(args + list(extras), capture_output=True, text=True, timeout=15)

    def test_all_twenty_exact_fixture_dispositions(self):
        for c in CASES:
            with self.subTest(fixture=c["file"]):
                path = ROOT / c["file"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), c["sha256"])
                if c["expected_exit"] == 2:
                    with self.assertRaises(loop.InputError):
                        loop.evaluate(path, voltage_unit=c["voltage_unit"], current_unit=c["current_unit"], accept_supplied_path_assumptions=True)
                    continue
                report = loop.evaluate(path, voltage_unit=c["voltage_unit"], current_unit=c["current_unit"], accept_supplied_path_assumptions=True)
                result = report["results"][0]
                self.assertEqual(result["status"], c["status"])
                self.assertEqual(result["rejection_reasons"], c["rejection_reasons"])
                self.assertEqual([e["trapezoid_A_V_exact"] for e in result["edges"]], c["edge_contributions_A_V_exact"])
                self.assertEqual([b["signed_path_integral_A_V_exact"] for b in result["branch_audits"]],
                                 [b["signed_path_integral_A_V_exact"] for b in c["branch_block_audit"]])
                metric = None if result["metrics"] is None else result["metrics"]["signed_path_integral_A_V_exact"]
                self.assertEqual(metric, c["expected_primary_metric_A_V_exact"])
                self.assertEqual(result["sample_count"], c["sample_count"])
                self.assertEqual(result["edge_count"], c["edge_count"])
                self.assertEqual(result["closure"]["voltage_closed_exact"], c["voltage_closed_exact"])
                self.assertEqual(result["closure"]["current_endpoints_equal_exact"], c["current_endpoints_equal_exact"])
                self.assertEqual(report["provenance"]["input_sha256"], c["sha256"])

    def test_all_twenty_fixture_cli_exits_and_no_fatal_report(self):
        for c in CASES:
            with self.subTest(fixture=c["file"]):
                cmd = [sys.executable, str(MODULE), str(ROOT / c["file"]), "--voltage-unit", c["voltage_unit"], "--current-unit", c["current_unit"], "--accept-supplied-path-assumptions", "--stdout"]
                run = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                self.assertEqual(run.returncode, c["expected_exit"], run.stderr)
                if run.returncode == 2:
                    self.assertEqual(run.stdout, "")
                    self.assertNotIn("invented_", run.stderr)
                else:
                    self.assertEqual(json.loads(run.stdout)["results"][0]["status"], c["status"])
                    self.assertEqual(run.stderr, "")

    def test_generator_regenerates_all_original_bytes(self):
        for case in CASES:
            p = ROOT / case["file"]
            self.assertEqual(gen.generate(p.stem).encode(), p.read_bytes())
        run = subprocess.run([sys.executable, str(ROOT / "examples/generate_synthetic.py"), "01_triangle"], capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, (ROOT / "examples/01_triangle.csv").read_bytes())

    def test_all_twenty_unit_pairs_preserve_exact_si(self):
        points = [(0, 0), (1, 3), (2, 4), (1, 1), (0, 0)]
        for vu, vf in loop.VOLTAGE_UNITS.items():
            for cu, cf in loop.CURRENT_UNITS.items():
                with self.subTest(vu=vu, cu=cu):
                    scaled = [(loop.exact_decimal(F(v)/vf), loop.exact_decimal(F(i)/cf)) for v, i in points]
                    result = self.one(self.rows(scaled), voltage_unit=vu, current_unit=cu)
                    self.assertEqual(result["metrics"]["signed_path_integral_A_V_exact"], "2e+0")

    def test_reversal_and_terminal_sign_symmetries(self):
        base = [(0, 0), (1, 3), (2, 4), (1, 1), (0, 0)]
        for points, want in [(list(reversed(base)), -2), ([(v, -i) for v, i in base], -2),
                             ([(-v, i) for v, i in base], -2), ([(-v, -i) for v, i in base], 2)]:
            r = self.one(self.rows(points))
            self.assertEqual(F(r["metrics"]["signed_path_integral_A_V_exact"]), want)
            self.assertEqual([(F(p["voltage_V_exact"]), F(p["current_A_exact"])) for p in r["observations"]], points)

    def test_constant_current_offset_not_branch_invariance(self):
        base = [(0, 7), (1, 10), (2, 11), (1, 8), (0, 7)]
        r = self.one(self.rows(base))
        self.assertEqual(r["metrics"]["signed_path_integral_A_V_exact"], "2e+0")
        self.assertEqual([b["signed_path_integral_A_V"] for b in r["branch_audits"]], [19, -17])
        opened = self.one(self.rows([(0, 7), (1, 9), ("0.1", 7)], ["up", "down"]))
        self.assertIsNone(opened["metrics"])
        self.assertEqual(sum(F(e["trapezoid_A_V_exact"]) for e in opened["edges"]), F("0.8"))

    def test_large_current_offset_survives_float_aliases(self):
        offset = 10**30
        r = self.one(self.rows([(0, offset), (1, offset+3), (2, offset+4), (1, offset+1), (0, offset)]))
        self.assertEqual(float(offset), float(offset+4))
        self.assertEqual(r["metrics"]["signed_path_integral_A_V_exact"], "2e+0")

    def test_voltage_translation_and_collinear_supplied_refinement(self):
        translated = [(8, 0), (9, 3), (10, 4), (9, 1), (8, 0)]
        self.assertEqual(self.one(self.rows(translated))["metrics"]["signed_path_integral_A_V"], 2)
        refined = [(0, 0), ("0.5", "1.5"), (1, 3), (2, 4), (1, 1), (0, 0)]
        r = self.one(self.rows(refined, ["up", "up", "up", "down", "down"]))
        self.assertEqual(r["sample_count"], 6)
        self.assertEqual(r["metrics"]["signed_path_integral_A_V"], 2)

    def test_closed_polygon_exact_shoelace_oracle(self):
        # 81 independent four-edge paths, including crossings/retracing/degeneracies.
        grid = list(itertools.product((-1, 0, 1), repeat=2))
        for a, b in itertools.product(grid, repeat=2):
            points = [(0, 0), a, (1, 0), b, (0, 0)]
            expected = -sum(F(v)*j-F(w)*i for (v, i), (w, j) in zip(points, points[1:]))/2
            r = self.one(self.rows(points, ["a", "b", "c", "d"]))
            self.assertEqual(F(r["metrics"]["signed_path_integral_A_V_exact"]), expected)

    def test_voltage_only_closure_keeps_current_difference_and_no_added_edge(self):
        points = [(0, 1), (1, 3), (0, 5)]
        for seq, diff in [(points, 4), (list(reversed(points)), -4)]:
            r = self.one(self.rows(seq, ["up", "down"]))
            self.assertEqual(F(r["closure"]["current_endpoint_difference_A_exact"]), diff)
            self.assertTrue(r["closure"]["voltage_closed_exact"])
            self.assertFalse(r["closure"]["iv_endpoint_closed_exact"])
            self.assertFalse(r["closure"]["added_closure_edge"])
            self.assertEqual((len(r["observations"]), len(r["edges"])), (3, 2))
            self.assertIn("voltage_closed_current_endpoints_differ", r["warnings"])

    def test_open_voltage_difference_is_last_minus_first(self):
        r = self.one(self.rows([(0, 0), (1, 2), ("0.1", 0)], ["up", "down"]))
        self.assertEqual(r["closure"]["voltage_endpoint_difference_V_exact"], "1e-1")
        self.assertIsNone(r["metrics"])
        self.assertTrue(all(b["signed_path_integral_A_V"] is None for b in r["branch_audits"]))
        self.assertNotIn("signed_path_integral_A_V_exact", r)

    def test_exact_closed_endpoint_spellings_and_float_alias_rejection(self):
        r = self.one(self.rows([("1.0", 0), (2, 1), ("1e0", 0)], ["up", "down"]))
        self.assertEqual(r["status"], "usable")
        points = [(1, 0), (2, 1), ("1.0000000000000000000000000000001", 0)]
        self.assertEqual(float(points[0][0]), float(points[-1][0]))
        r = self.one(self.rows(points, ["up", "down"]))
        self.assertEqual(r["primary_rejection_reason"], "voltage_endpoints_not_equal")

    def test_plateau_shared_identity_partition_and_raw_text(self):
        r = self.one(self.rows([(0, 0), (1, 2), ("1.0", 3), ("1e0", 4), (0, 0)], ["up", "hold", "hold", "down"]))
        self.assertEqual([b["sample_indices"] for b in r["branch_audits"]], [[1, 2], [2, 3, 4], [4, 5]])
        self.assertEqual([p["sample_index"] for p in r["shared_endpoints"]], [2, 4])
        self.assertEqual([p["voltage_text"] for p in r["observations"]], ["0", "1", "1.0", "1e0", "0"])
        self.assertEqual([e["trapezoid_A_V_exact"] for e in r["edges"]], ["1e+0", "0", "0", "-2e+0"])
        self.assertEqual(sum(b["edge_count"] for b in r["branch_audits"]), 4)
        self.assertEqual(len(r["observations"]), 5)
        self.assertEqual(r["metrics"]["signed_path_integral_A_V"], -1)

    def test_flat_edges_can_be_explicitly_owned_by_directional_branches(self):
        r = self.one(self.rows([(0, 0), (1, 2), (1, 3), (1, 4), (0, 0)], ["up", "up", "down", "down"]))
        self.assertEqual([b["direction"] for b in r["branch_audits"]], ["increasing", "decreasing"])
        self.assertEqual([b["flat_edge_count"] for b in r["branch_audits"]], [1, 1])
        self.assertEqual(r["metrics"]["signed_path_integral_A_V"], -1)

    def test_declared_same_direction_labels_are_not_merged(self):
        r = self.one(self.rows(labels=["first_up", "second_up", "down", "down"]))
        self.assertEqual(len(r["branch_audits"]), 3)
        self.assertEqual(r["metrics"]["signed_path_integral_A_V"], 2)

    def test_singletons_all_flat_and_ordered_multiple_reasons(self):
        one = self.one(self.rows([(0, 0)], []))
        self.assertEqual(one["rejection_reasons"], ["insufficient_samples", "zero_voltage_span"])
        self.assertEqual(one["edges"], [])
        self.assertEqual(one["branch_audits"], [])
        self.assertEqual(one["observations"][0]["branch_block_indices"], [])
        two = self.one(self.rows([(0, 0), (1, 1)], ["up"]))
        self.assertEqual(two["rejection_reasons"], ["insufficient_samples", "voltage_endpoints_not_equal"])
        flat = self.one(self.rows([(1, 0), (1, 1), (1, 2)], ["hold", "hold"]))
        self.assertEqual(flat["rejection_reasons"], ["zero_voltage_span"])
        self.assertEqual(flat["branch_audits"][0]["direction"], "flat")

    def test_zero_current_and_signed_cancellation_have_distinct_warnings(self):
        zero = self.one(self.rows([(0, 0), (1, 0), (0, 0)], ["up", "down"]))
        cancel = self.one(self.rows([(0, 1), (1, 1), (0, 1)], ["up", "down"]))
        for r in [zero, cancel]:
            self.assertEqual(r["metrics"]["signed_path_integral_A_V_exact"], "0")
            self.assertIn("zero_signed_integral_not_absence_of_hysteresis", r["warnings"])
        self.assertNotIn("nonzero_edge_contributions_cancel_exactly", zero["warnings"])
        self.assertIn("nonzero_edge_contributions_cancel_exactly", cancel["warnings"])

    def test_prediction_availability_is_last_sample_and_never_verified(self):
        for n in (1, 3, 5):
            rows = self.rows([(i, 0) for i in range(n)], ["up"]*(n-1))
            r = self.one(rows)
            timing = r["feature_availability"]
            self.assertEqual(timing["earliest_required_sample_index"], n)
            self.assertEqual(timing["earliest_required_sample_id"], "p"+str(n))
            self.assertFalse(timing["time_verified"])
            self.assertEqual(timing["prediction_eligibility"], "not_established")

    def test_all_group_dimensions_isolate_and_preserve_order(self):
        for index in range(6):
            key = ["lot", "wafer", "device", "condition", "001", "replicate"]
            changed = key[:]; changed[index] = "other"
            rows = self.rows(key=key) + self.rows(key=changed)
            result = self.evaluate(rows)
            self.assertEqual(result["counts"]["input_loops"], 2)
            self.assertEqual([r["metrics"]["signed_path_integral_A_V"] for r in result["results"]], [2, 2])
            self.assertEqual([p["source_record_index"] for r in result["results"] for p in r["observations"]], list(range(1, 11)))

    def test_opaque_cycle_ids_and_unicode_comma_ids(self):
        a = ["l,一", "wafer", "device", "condition", "001", "r"]
        b = a[:]; b[4] = "1"
        report = self.evaluate(self.rows(key=a)+self.rows(key=b))
        self.assertEqual([r["key"]["cycle"] for r in report["results"]], ["001", "1"])
        self.assertEqual(report["results"][0]["key"]["lot"], "l,一")

    def test_noncontiguous_full_group_is_fatal(self):
        second = ["l", "w", "other", "c", "1", "r"]
        with self.assertRaises(loop.InputError):
            self.evaluate(self.rows()+self.rows(key=second)+self.rows())

    def test_duplicate_ids_and_branch_sentinel_errors_are_fatal(self):
        mutations = [(4, 6, "p1"), (0, 7, ""), (4, 7, "extra")]
        for i, col, value in mutations:
            rows = self.rows(); rows[i][col] = value
            with self.assertRaises(loop.InputError):self.evaluate(rows)

    def test_identifier_bounds_controls_and_boundary_unicode_whitespace(self):
        for good in ["x", "x"*128, "001", "a\u2028b", "裝置,1"]:
            self.assertEqual(loop.identifier(good), good)
        for bad in ["", "x"*129, " x", "x ", "x\t", "x\n", "x\x00y", "x\x7f", "\u2028x", "x\u2028"]:
            with self.assertRaises(loop.InputError):loop.identifier(bad)

    def test_numeric_syntax_precision_exponent_and_token_bounds(self):
        for token in ["1", "+1", "1.", ".1", "-0", "1e+0001", "0"*63+"1", "1"+"0"*31, "0e200", "0e-200"]:
            self.assertEqual(loop.number(token, F(1)), F(token))
        for token in ["", " 1", "1 ", "1\n", "NaN", "Infinity", "inf", "0x1", "1_0", "1,000", "1V", "1e00001", "0"*64+"1", "1"+"0"*32, "0e201", "0e-201", "0.0e-200"]:
            with self.subTest(token=token), self.assertRaises(loop.InputError):loop.number(token, F(1))

    def test_exact_si_range_all_units_and_signs(self):
        for factor in list(loop.VOLTAGE_UNITS.values())+list(loop.CURRENT_UNITS.values()):
            for target in [F(0), F("1e-150"), F("-1e-150"), F("1e150"), F("-1e150")]:
                self.assertEqual(loop.number(loop.exact_decimal(target/factor), factor), target)
            for target in [F("1e-151"), F("-1e-151"), F("1e151"), F("-1e151")]:
                with self.assertRaises(loop.InputError):loop.number(loop.exact_decimal(target/factor), factor)

    def test_decimal_context_does_not_change_arithmetic_or_canonicalization(self):
        with localcontext() as context:
            context.prec = 2
            r = self.one(self.rows([(0, 10**30), (1, 10**30+3), (2, 10**30+4), (1, 10**30+1), (0, 10**30)]))
            self.assertEqual(r["metrics"]["signed_path_integral_A_V_exact"], "2e+0")
            for q in [F(0), F("1.25"), F("1e-362"), F("-1e150"), F("1.0000000000000000000000000000001")]:
                self.assertEqual(F(loop.exact_decimal(q)), q)

    def test_canonical_exact_strings_schema_negative_cases(self):
        schema = json.loads((ROOT / "report_schema.json").read_text())
        pattern = re.compile(schema["$defs"]["exactDecimal"]["pattern"])
        for value in ["0", "1e+0", "-1e+0", "2.5e-3"]:self.assertIsNotNone(pattern.search(value))
        for value in ["1e-0", "-1e-0", "0\n", "1e+0\n", "0\r\n", "1.0e+0", "01e+0", "1e+00"]:
            self.assertIsNone(pattern.search(value))
        identifier = re.compile(schema["$defs"]["identifier"]["pattern"])
        for control in ("\n", "\r", "\x00", "\x7f"):
            for value in [control+"x", "x"+control+"y", "x"+control]:self.assertIsNone(identifier.search(value))
        digest = re.compile(schema["properties"]["provenance"]["properties"]["input_sha256"]["pattern"])
        self.assertIsNone(digest.search("a"*64+"\n"))

    def test_finite_float_subnormal_overflow_and_underflow(self):
        self.assertNotEqual(loop.finite_float(F("1e-323")), 0)
        self.assertEqual(loop.finite_float(F(0)), 0.0)
        self.assertEqual(math.copysign(1, loop.finite_float(F(0))), 1)
        for q in [F("1e-400"), F("-1e-400"), F("1e400"), F("-1e400")]:
            with self.assertRaises(loop.NumericRangeError):loop.finite_float(q)

    def test_total_and_branch_underflow_reject_all_numeric_summaries(self):
        for name in ["13_tiny_exact_path", "14_total_underflow", "15_branch_underflow"]:
            report = loop.evaluate(ROOT/("examples/"+name+".csv"), voltage_unit="V", current_unit="A", accept_supplied_path_assumptions=True)
            r = report["results"][0]
            self.assertEqual(r["rejection_reasons"], ["derived_numeric_range_error"])
            self.assertIsNone(r["metrics"])
            self.assertTrue(all(b["signed_path_integral_A_V"] is None for b in r["branch_audits"]))
            self.assertTrue(any(F(e["trapezoid_A_V_exact"]) for e in r["edges"]))

    def test_numeric_budget_and_nonterminating_defensive_failures(self):
        with patch.object(loop, "MAX_RATIONAL_BITS", 1), self.assertRaises(loop.InputError):loop.bounded(F(3))
        with patch.object(loop, "MAX_EXACT_CHARACTERS", 2), self.assertRaises(loop.InputError):loop.exact_decimal(F(1))
        with self.assertRaises(loop.InputError):loop.exact_decimal(F(1, 3))

    def test_fatal_numeric_anywhere_no_partial_cohort(self):
        rows = self.rows()+self.rows(key=("l", "w", "other", "c", "1", "r"));rows[-1][-1] = "NaN"
        with self.assertRaises(loop.InputError):self.evaluate(rows)

    def test_header_permutation_and_all_invalid_header_shapes(self):
        header = list(reversed(loop.COLUMNS));rows = [list(reversed(r)) for r in self.rows()]
        self.write(rows, header)
        r = loop.evaluate(self.input, voltage_unit="V", current_unit="A", accept_supplied_path_assumptions=True)
        self.assertEqual(r["results"][0]["metrics"]["signed_path_integral_A_V"], 2)
        for bad in [list(loop.COLUMNS[:-1]), list(loop.COLUMNS)+["extra"], list(loop.COLUMNS[:-1])+[loop.COLUMNS[0]], ["Lot"]+list(loop.COLUMNS[1:])]:
            self.write(header=bad)
            with self.assertRaises(loop.InputError):loop.evaluate(self.input, voltage_unit="V", current_unit="A", accept_supplied_path_assumptions=True)

    def test_empty_blank_width_malformed_and_invalid_utf8_fatal(self):
        header = ",".join(loop.COLUMNS).encode()+b"\n"
        for raw in [b"", header, header+b"\n", header+b"short,row\n", header+b'"unclosed', header+b"\xff"]:
            self.input.write_bytes(raw)
            with self.assertRaises(loop.InputError):loop.evaluate(self.input, voltage_unit="V", current_unit="A", accept_supplied_path_assumptions=True)
        self.write();self.input.write_bytes(self.input.read_bytes()+b"\n")
        with self.assertRaises(loop.InputError):loop.evaluate(self.input, voltage_unit="V", current_unit="A", accept_supplied_path_assumptions=True)

    def test_bom_crlf_source_lines_records_and_hash(self):
        self.write(newline="\r\n", bom=True)
        raw = self.input.read_bytes()
        r = loop.evaluate(self.input, voltage_unit="V", current_unit="A", accept_supplied_path_assumptions=True)
        self.assertEqual(r["provenance"]["input_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(r["provenance"]["input_byte_count"], len(raw))
        self.assertEqual([p["source_line_start"] for p in r["results"][0]["observations"]], [2, 3, 4, 5, 6])
        self.assertEqual([p["source_line_end"] for p in r["results"][0]["observations"]], [2, 3, 4, 5, 6])
        self.assertEqual(self.input.read_bytes(), raw)

    def test_actual_row_group_and_per_group_caps(self):
        block = self.rows([(0, 0)]*1000, ["hold"]*999)
        rows = []
        for i in range(10):
            current = [r[:] for r in block]
            for row in current:row[2] = "device"+str(i)
            rows.extend(current)
        self.write(rows)
        groups, count, _, _ = loop.load_csv(self.input, F(1), F(1))
        self.assertEqual((len(groups), count), (10, 10000))
        self.write(rows+self.rows([(0, 0)], [], key=("l", "w", "extra", "c", "1", "r")))
        with self.assertRaises(loop.InputError):loop.load_csv(self.input, F(1), F(1))
        self.write(self.rows([(0, 0)]*1001, ["hold"]*1000))
        with self.assertRaises(loop.InputError):loop.load_csv(self.input, F(1), F(1))
        singleton_rows = [self.rows([(0, 0)], [], key=("l", "w", str(i), "c", "1", "r"))[0] for i in range(1000)]
        self.write(singleton_rows);self.assertEqual(len(loop.load_csv(self.input, F(1), F(1))[0]), 1000)
        self.write(singleton_rows+self.rows([(0, 0)], [], key=("l", "w", "1000", "c", "1", "r")))
        with self.assertRaises(loop.InputError):loop.load_csv(self.input, F(1), F(1))

    def test_byte_cap_boundary_and_oversize(self):
        self.write();size = self.input.stat().st_size
        with patch.object(loop, "MAX_BYTES", size):loop.load_csv(self.input, F(1), F(1))
        with patch.object(loop, "MAX_BYTES", size-1), self.assertRaises(loop.InputError):loop.load_csv(self.input, F(1), F(1))
        self.assertEqual(loop.MAX_BYTES, 8*1024*1024)
        self.input.write_bytes(b"x"*(loop.MAX_BYTES+1))
        with self.assertRaisesRegex(loop.InputError, "byte limit"):loop.load_csv(self.input, F(1), F(1))

    def test_report_size_boundary_before_output_creation(self):
        report = self.evaluate();payload = b"".join(loop.report_chunks(report));new = self.directory/"too_big.json"
        with patch.object(loop, "MAX_REPORT_BYTES", len(payload)):
            self.assertEqual(b"".join(loop.report_chunks(report)), payload)
        with patch.object(loop, "MAX_REPORT_BYTES", len(payload)-1), self.assertRaises(loop.InputError):
            loop.write_report(report, new)
        self.assertFalse(new.exists())
        with patch.object(loop, "MAX_REPORT_BYTES", 1), self.assertRaises(loop.InputError):self.evaluate()

    def test_regular_file_only_and_input_symlink(self):
        self.write();link = self.directory/"input_link.csv";link.symlink_to(self.input)
        report = loop.evaluate(link, voltage_unit="V", current_unit="A", accept_supplied_path_assumptions=True)
        self.assertEqual(report["results"][0]["status"], "usable")
        for p in [self.directory, Path("/dev/null")]:
            with self.assertRaises(loop.InputError):loop.load_csv(p, F(1), F(1))
        if hasattr(os, "mkfifo"):
            fifo = self.directory/"input_fifo";os.mkfifo(fifo)
            run = self.cli(fifo)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, "")

    def test_private_exclusive_output_all_destination_types(self):
        report = self.evaluate();raw = self.input.read_bytes()
        new = self.directory/"new.json";loop.write_report(report, new)
        self.assertEqual(stat.S_IMODE(new.stat().st_mode), 0o600)
        self.assertEqual(json.loads(new.read_text()), report)
        live = self.directory/"live_link";live.symlink_to(new)
        dangling = self.directory/"dangling_link";dangling.symlink_to(self.directory/"absent")
        for path in [new, self.input, self.directory, live, dangling]:
            with self.assertRaises(OSError):loop.write_report(report, path)
        self.assertEqual(self.input.read_bytes(), raw)
        self.assertTrue(dangling.is_symlink())
        self.assertEqual(json.loads(new.read_text()), report)

    def test_cli_required_options_no_abbreviations_and_redacted_io(self):
        self.write()
        for extra, assumptions in [((), True), (("--stdout", "--output", str(self.directory/"x")), True), (("--stdout",), False), (("--stdo",), True), (("--stdout", "--unknown"), True)]:
            run = self.cli(extras=extra, assumptions=assumptions)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, "")
            self.assertNotIn(str(self.input), run.stderr)
        run = self.cli(extras=("--output", str(self.directory/"private_missing_parent"/"private_out.json")))
        self.assertEqual(run.returncode, 2)
        self.assertNotIn("private_", run.stderr)
        self.assertEqual(run.stdout, "")
        help_run = subprocess.run([sys.executable, str(MODULE), "--help"], capture_output=True, text=True, timeout=15)
        self.assertEqual(help_run.returncode, 0)

    def test_callable_bad_units_and_acknowledgment(self):
        self.write()
        for vu, cu, flag in [("V", "A", False), ("V", "A", 1), ("v", "A", True), ("V", "uAMP", True), ([], "A", True)]:
            with self.assertRaises(loop.InputError):loop.evaluate(self.input, voltage_unit=vu, current_unit=cu, accept_supplied_path_assumptions=flag)

    def test_fatal_main_creates_no_output_or_raw_diagnostic(self):
        rows = self.rows();rows[0][9] = "private_invalid_value";self.write(rows)
        dest = self.directory/"audit.json";run = self.cli(extras=("--output", str(dest)))
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, "")
        self.assertFalse(dest.exists())
        self.assertNotIn("private_invalid_value", run.stderr)
        self.assertNotIn("private_input", run.stderr)

    def test_mixed_cohort_exit_one_retains_every_group(self):
        other = self.rows([(0, 0), (1, 1)], ["up"], key=("l", "w", "other", "c", "1", "r"))
        self.write(self.rows()+other);run = self.cli()
        self.assertEqual(run.returncode, 1)
        report = json.loads(run.stdout)
        self.assertEqual(report["counts"]["input_samples"], 7)
        self.assertEqual([r["status"] for r in report["results"]], ["usable", "rejected"])
        self.assertEqual(sum(len(r["observations"]) for r in report["results"]), 7)

    def test_callable_embedded_nul_path_is_redacted_input_error(self):
        with self.assertRaises(loop.InputError) as caught:
            loop.evaluate("private_path\x00secret", voltage_unit="V", current_unit="A", accept_supplied_path_assumptions=True)
        self.assertNotIn("private_path", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("embedded", str(caught.exception))

    def test_partial_output_failure_is_fatal_and_not_claimed_complete(self):
        self.write(); before = self.input.read_bytes(); destination = self.directory/"partial.json"
        real_fdopen = os.fdopen
        class BrokenWriter:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): self.handle.close()
            def write(self, payload):
                self.handle.write(b"{"); self.handle.flush()
                raise OSError("private_write_failure")
        def wrapped(fd, mode, *args, **kwargs):
            handle = real_fdopen(fd, mode, *args, **kwargs)
            return BrokenWriter(handle) if mode == "wb" else handle
        out, err = io.StringIO(), io.StringIO()
        with patch.object(loop.os, "fdopen", side_effect=wrapped), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = loop.main([str(self.input), "--voltage-unit", "V", "--current-unit", "A", "--accept-supplied-path-assumptions", "--output", str(destination)])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertNotIn("private_write_failure", err.getvalue())
        self.assertEqual(destination.read_bytes(), b"{")
        self.assertEqual(self.input.read_bytes(), before)

    def test_serialization_never_emits_nonfinite_json(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(ValueError):list(loop.report_chunks({"bad": value}))


if __name__ == "__main__":
    unittest.main()
