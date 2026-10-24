import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("iv_grid_audit", ROOT / "iv_grid_audit.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
HEADER = list(M.REQUIRED_COLUMNS) + ["current", "target"]


def row(voltage="0", *, sweep="s1", sample="p1", condition="c1", branch="forward",
        device="D1", lot="L1", wafer="W1", current="opaque", target="not a number"):
    return [lot, wafer, device, condition, sweep, branch, sample, voltage, current, target]


def data(rows, header=HEADER, ending="\n"):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator=ending)
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def report(rows):
    return M.audit(data(rows), "V")


class GridTests(unittest.TestCase):
    def test_equal_grids_ignore_order_not_observations(self):
        r = report([row("0", sample="a"), row("1", sample="b"),
                    row("1.0", sweep="s2", sample="a"), row("-0", sweep="s2", sample="b")])
        self.assertEqual(r["status"], "NO_GRID_FINDINGS")
        self.assertEqual(r["counts"]["input_rows"], 4)
        self.assertEqual(r["counts"]["declared_sweep_groups"], 2)
        self.assertEqual(len(r["comparisons"][0]["grid_variants"]), 1)
        self.assertEqual(r["groups"][1]["samples"][0]["voltage_text"], "1.0")
        self.assertEqual(r["groups"][1]["samples"][1]["coordinate_index"], 1)

    def test_duplicate_coordinates_preserve_every_observation(self):
        r = report([row("1", sample="a"), row("1.0", sample="b"), row("1e0", sample="c")])
        self.assertEqual(r["status"], "GRID_FINDINGS")
        self.assertEqual(r["groups"][0]["duplicate_coordinates"],
                         [{"coordinate_index": 1, "source_row_ordinals": [1, 2, 3]}])
        self.assertEqual(len(r["groups"][0]["samples"]), 3)
        self.assertEqual(r["counts"]["groups_with_duplicate_coordinates"], 1)

    def test_unequal_grid_is_relative_to_union_not_first_sweep(self):
        r = report([row("0", sample="a"), row("2", sample="b"),
                    row("1", sweep="s2", sample="a"), row("2", sweep="s2", sample="b")])
        self.assertEqual(r["groups"][0]["missing_from_observed_union_indices"], [2])
        self.assertEqual(r["groups"][1]["missing_from_observed_union_indices"], [1])
        self.assertEqual(r["counts"]["comparison_sets_with_unequal_grids"], 1)
        self.assertEqual(r["comparisons"][0]["observed_union"],
                         [{"coordinate_index": 1, "first_observed_voltage_text": "0"},
                          {"coordinate_index": 2, "first_observed_voltage_text": "1"},
                          {"coordinate_index": 3, "first_observed_voltage_text": "2"}])

    def test_equal_count_shifted_grid_is_not_equal(self):
        r = report([row("0", sample="a"), row("1", sample="b"),
                    row("1", sweep="s2", sample="a"), row("2", sweep="s2", sample="b")])
        self.assertTrue(r["comparisons"][0]["unequal_grids"])

    def test_condition_and_branch_isolation(self):
        r = report([row("0"), row("1", condition="c2"), row("2", branch="reverse")])
        self.assertEqual(r["counts"]["comparison_sets"], 3)
        self.assertEqual(r["status"], "NO_GRID_FINDINGS")

    def test_device_namespace_is_preserved_without_independence_inference(self):
        r = report([row("0"), row("1", lot="L2"), row("2", wafer="W2"), row("3", device="D2")])
        self.assertEqual(r["counts"]["declared_sweep_groups"], 4)
        self.assertEqual(len(r["comparisons"]), 1)
        self.assertEqual([g["identity"]["lot"] for g in r["groups"]], ["L1", "L2", "L1", "L1"])
        self.assertNotIn("independent_device_count", r["counts"])

    def test_noncontiguous_groups_retain_global_row_lineage(self):
        r = report([row("0", sample="a"), row("0", sweep="s2", sample="a"),
                    row("1", sample="b"), row("1", sweep="s2", sample="b")])
        self.assertEqual([s["source_row_ordinal"] for s in r["groups"][0]["samples"]], [1, 3])
        self.assertEqual(r["status"], "NO_GRID_FINDINGS")

    def test_first_union_spelling_follows_source_order_across_interleaved_groups(self):
        r = report([row("0", sample="p1", sweep="A"),
                    row("1.00", sample="p1", sweep="B"),
                    row("1e0", sample="p2", sweep="A")])
        self.assertEqual(r["comparisons"][0]["observed_union"][1],
                         {"coordinate_index": 2, "first_observed_voltage_text": "1.00"})
        self.assertEqual(r["groups"][0]["samples"][1]["voltage_text"], "1e0")
        self.assertEqual(r["groups"][1]["samples"][0]["source_row_ordinal"], 2)

    def test_duplicate_sample_id_is_fatal_even_when_noncontiguous(self):
        with self.assertRaises(M.InputError):
            report([row("0"), row("1", sweep="s2"), row("2")])

    def test_opaque_identifier_tuple_not_delimiter_join(self):
        r = report([row(lot="a,b", wafer="c"), row(lot="a", wafer="b,c")])
        self.assertEqual(r["counts"]["declared_sweep_groups"], 2)

    def test_exact_decimal_does_not_alias_binary64(self):
        r = report([row("9007199254740992", sample="a"), row("9007199254740993", sample="b")])
        self.assertEqual(len(r["groups"][0]["unique_coordinate_indices"]), 2)
        self.assertEqual(r["status"], "NO_GRID_FINDINGS")

    def test_tiny_large_signed_zero_values_and_low_decimal_context(self):
        import decimal
        with decimal.localcontext() as ctx:
            ctx.prec = 2
            r = report([row("1e-200", sample="a"), row("1e200", sample="b"),
                        row("-0", sample="c"), row("0.0", sample="d")])
        self.assertEqual(len(r["groups"][0]["unique_coordinate_indices"]), 3)
        self.assertEqual(r["groups"][0]["duplicate_coordinates"][0]["source_row_ordinals"], [3, 4])

    def test_source_hash_and_schema(self):
        blob = data([row()])
        r = M.audit(blob, "mV")
        self.assertEqual(r["provenance"]["csv_sha256"], hashlib.sha256(blob).hexdigest())
        self.assertEqual(r["provenance"]["csv_bytes"], len(blob))
        self.assertEqual(r["schema_version"], "iv-grid-audit-v1")
        self.assertEqual(r["parameters"]["voltage_unit_declared"], "mV")
        self.assertFalse(r["selection"]["response_values_inspected"])

    def test_random_permuted_equal_grids(self):
        rng = random.Random(44)
        rows = []
        for sweep in range(12):
            values = list(range(-8, 9))
            rng.shuffle(values)
            rows.extend(row(str(v), sweep=f"s{sweep}", sample=f"p{n}") for n, v in enumerate(values))
        rng.shuffle(rows)
        r = report(rows)
        self.assertEqual(r["status"], "NO_GRID_FINDINGS")
        self.assertEqual(r["counts"]["input_rows"], 204)


class ParsingTests(unittest.TestCase):
    def test_response_noninterference_including_invalid_utf8(self):
        blobs = [data([row(current=value, target=value)]) for value in
                 ("NaN", "Infinity", "=HYPERLINK(secret)", "3.14", 'quoted,\n"text"', "")]
        blobs.append(data([row(current="PLACEHOLDER", target="PLACEHOLDER")]).replace(b"PLACEHOLDER", b"\xff\x00"))
        reports = [M.audit(blob, "V") for blob in blobs]
        for r in reports:
            r.pop("provenance")
        self.assertTrue(all(r == reports[0] for r in reports))
        self.assertNotIn("PLACEHOLDER", json.dumps(reports[0]))

    def test_required_only_and_reordered_headers(self):
        required = data([row()[:8]], list(M.REQUIRED_COLUMNS))
        self.assertEqual(M.audit(required, "V")["selection"]["opaque_columns"], [])
        order = list(reversed(range(len(HEADER))))
        r = M.audit(data([[row()[i] for i in order]], [HEADER[i] for i in order]), "V")
        self.assertEqual(r["groups"][0]["identity"]["device"], "D1")

    def test_bom_crlf_and_no_final_newline(self):
        for blob in (b"\xef\xbb\xbf" + data([row()], ending="\r\n"), data([row()]).rstrip(b"\n")):
            with self.subTest(blob=blob[:3]):
                self.assertEqual(M.audit(blob, "V")["counts"]["input_rows"], 1)

    def test_invalid_selected_utf8_is_fatal(self):
        for name in ("device", "voltage"):
            values = row()
            values[HEADER.index(name)] = "PLACEHOLDER"
            with self.assertRaises(M.InputError):
                M.audit(data([values]).replace(b"PLACEHOLDER", b"\xff"), "V")

    def test_finite_numeric_syntax_and_bounds(self):
        bad = ["NaN", "Infinity", "-inf", " 1", "1 ", "1_000", "0x10", "", "1V", "1,000", "１",
               "1e201", "1.0e-200", "1" * 33, "0" * 65, "1e99999"]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(M.InputError):
                report([row(value)])
        for value in ("+1.", ".1", "-1e+200", "1e-200", "1" * 32):
            with self.subTest(value=value):
                self.assertEqual(report([row(value)])["status"], "NO_GRID_FINDINGS")

    def test_identifier_limits_and_literal_identity(self):
        for value in ("", " padded", "padded ", "x\x00y", "x\x85y", "x" * 129):
            with self.subTest(value=value), self.assertRaises(M.InputError):
                report([row(device=value)])
        r = report([row(device="001"), row(device="1"), row(device="裝置")])
        self.assertEqual(r["counts"]["declared_sweep_groups"], 3)

    def test_bad_headers(self):
        for header in (HEADER[:-3], HEADER + ["voltage"], HEADER + [""], HEADER + ["with space"],
                       HEADER + ["x" * 65], ["Voltage" if h == "voltage" else h for h in HEADER]):
            with self.subTest(header=header), self.assertRaises(M.InputError):
                M.audit(data([row()], header), "V")

    def test_bad_csv_framing_even_in_opaque_values(self):
        header = data([])
        bad = [b"", header, header + b"\n", data([row()]) + b"\n", data([row()[:-1]]),
               data([row() + ["extra"]]), header + b'"unterminated',
               data([row(current="PLACEHOLDER")]).replace(b"PLACEHOLDER", b'a"b'),
               data([row(current="PLACEHOLDER")]).replace(b"PLACEHOLDER", b'"a"b'),
               data([row()]).replace(b"\n", b"\r")]
        for blob in bad:
            with self.subTest(blob=blob[-20:]), self.assertRaises(M.InputError):
                M.audit(blob, "V")

    def test_byte_row_column_record_group_and_report_limits(self):
        cases = [("MAX_BYTES", 10, data([row()])),
                 ("MAX_ROWS", 1, data([row(), row(sweep="s2")])),
                 ("MAX_COLUMNS", 9, data([row()])),
                 ("MAX_RECORD_BYTES", 20, data([row()])),
                 ("MAX_GROUPS", 1, data([row(), row(sweep="s2")])),
                 ("MAX_GRID_CELLS", 3, data([row(), row("1", sweep="s2")])),
                 ("MAX_REPORT_BYTES", 10, data([row()]))]
        for name, value, blob in cases:
            with self.subTest(name=name), patch.object(M, name, value), self.assertRaises(M.InputError):
                M.audit(blob, "V")

    def test_wrong_input_type_or_unit(self):
        for blob in ("text", None, bytearray(data([row()]))):
            with self.assertRaises(M.InputError):
                M.audit(blob, "V")
        for unit in (None, "v", "ampere", ""):
            with self.assertRaises(M.InputError):
                M.audit(data([row()]), unit)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.csv"
        self.source.write_bytes(data([row()]))

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "iv_grid_audit.py"), str(self.source), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)

    def test_stdout_and_file_are_same_and_source_unchanged(self):
        before = self.source.read_bytes()
        result = self.cli("--voltage-unit", "V", "--stdout")
        output = self.root / "report.json"
        saved = self.cli("--voltage-unit", "V", "--output", str(output))
        self.assertEqual((result.returncode, saved.returncode), (0, 0))
        self.assertEqual(output.read_bytes(), result.stdout)
        self.assertEqual(saved.stdout, b"")
        self.assertEqual(saved.stderr, b"")
        self.assertEqual(before, self.source.read_bytes())
        if os.name == "posix":
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_findings_exit_one_with_complete_json(self):
        self.source.write_bytes(data([row("0", sample="a"), row("0", sample="b")]))
        result = self.cli("--voltage-unit", "V", "--stdout")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "GRID_FINDINGS")
        self.assertEqual(result.stderr, b"")

    def test_fatal_selected_input_does_not_create_report(self):
        self.source.write_bytes(data([row("SECRET_BAD_NUMBER")]))
        output = self.root / "report.json"
        result = self.cli("--voltage-unit", "V", "--output", str(output))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"SECRET", result.stderr)
        self.assertNotIn(str(self.source).encode(), result.stderr)

    def test_existing_file_input_path_and_symlink_cannot_be_overwritten(self):
        output = self.root / "existing.json"
        output.write_bytes(b"keep")
        paths = [output, self.source]
        if hasattr(os, "symlink"):
            link = self.root / "link.json"
            link.symlink_to(output)
            paths.append(link)
        before = self.source.read_bytes()
        for path in paths:
            result = self.cli("--voltage-unit", "V", "--output", str(path))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
        self.assertEqual(output.read_bytes(), b"keep")
        self.assertEqual(self.source.read_bytes(), before)

    def test_explicit_destination_units_and_full_option_names_required(self):
        for args in ((), ("--stdout",), ("--voltage-unit", "V"),
                     ("--voltage-u", "V", "--stdout"), ("--voltage-unit", "SECRET", "--stdout"),
                     ("--voltage-unit", "V", "--stdout", "--output", "SECRET")):
            result = self.cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertNotIn(b"SECRET", result.stderr)

    def test_missing_directory_and_fifo_inputs_fail_without_hanging(self):
        for path in (self.root / "absent", self.root):
            with self.assertRaises(M.InputError):
                M.read_regular(path)
        if hasattr(os, "mkfifo"):
            path = self.root / "pipe"
            os.mkfifo(path)
            with self.assertRaises(M.InputError):
                M.read_regular(path)

    def test_stdout_missing_closed_and_flush_failure_preserve_exit_two(self):
        class Broken(io.StringIO):
            def flush(self):
                raise OSError("SECRET output failure")
        closed = io.StringIO()
        closed.close()
        for stream in (None, closed, Broken()):
            with patch.object(sys, "stdout", stream), patch.object(sys, "stderr", io.StringIO()) as error:
                code = M.main([str(self.source), "--voltage-unit", "V", "--stdout"])
            self.assertEqual(code, 2)
            self.assertNotIn("SECRET", error.getvalue())

    def test_stderr_absent_closed_and_failed_do_not_leak_or_override_fatal(self):
        class Broken(io.StringIO):
            def write(self, value):
                raise OSError("SECRET diagnostic failure")
        closed = io.StringIO()
        closed.close()
        for stream in (None, closed, Broken()):
            with patch.object(sys, "stderr", stream), patch.object(sys, "stdout", io.StringIO()) as output:
                code = M.main(["SECRET_missing_file", "--voltage-unit", "V", "--stdout"])
            self.assertEqual(code, 2)
            self.assertEqual(output.getvalue(), "")

    def test_help_and_documented_synthetic_fixtures(self):
        result = self.cli("--help")
        self.assertEqual(result.returncode, 0)
        for name, expected in (("synthetic_equal.csv", 0), ("synthetic_findings.csv", 1)):
            self.source = ROOT / "examples" / name
            result = self.cli("--voltage-unit", "V", "--stdout")
            self.assertEqual(result.returncode, expected)
            self.assertEqual(json.loads(result.stdout)["counts"]["input_rows"], 6)


if __name__ == "__main__":
    unittest.main()
