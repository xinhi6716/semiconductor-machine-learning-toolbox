"""Original synthetic invariants, not experimental validation or device statistics."""
from collections import Counter
import csv
from decimal import Decimal, localcontext
import hashlib
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "sweep_segments.py"
spec = importlib.util.spec_from_file_location("sweep_segments", MODULE)
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)
HEADER = ",".join(sweep.COLUMNS) + "\n"
KEY = ("l", "w", "d", "c", "s")


class SweepSegmentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.input = self.directory / "input.csv"

    def tearDown(self):
        self.temp.cleanup()

    def body(self, coordinates=(0, 1, 2, 1, 0), key=KEY, ids=None):
        text = io.StringIO(newline="")
        writer = csv.writer(text, lineterminator="\n")
        for index, coordinate in enumerate(coordinates, 1):
            writer.writerow((*key, ids[index - 1] if ids else f"sample_{index}", coordinate))
        return text.getvalue()

    def write(self, body=None, header=HEADER):
        self.input.write_text(header + (self.body() if body is None else body), encoding="utf-8")
        return self.input

    def evaluate(self, body=None, **kwargs):
        self.write(body)
        params = {"axis": "voltage", "coordinate_unit": "V"}
        params.update(kwargs)
        return sweep.evaluate(self.input, **params)

    def single(self, body=None):
        return self.evaluate(body)["results"][0]

    def cli(self, extra=(), body=None, base=None):
        self.write(body)
        base = ["--axis", "voltage", "--coordinate-unit", "V"] if base is None else base
        return subprocess.run([sys.executable, str(MODULE), str(self.input), *base, *extra],
                              capture_output=True, text=True, timeout=5)

    def assert_partition(self, result, coordinates):
        samples, segments = result["samples"], result["segments"]
        self.assertEqual(len(samples), len(coordinates))
        self.assertEqual([p["coordinate_text"] for p in samples], list(map(str, coordinates)))
        self.assertEqual([p["sample_index"] for p in samples], list(range(1, len(samples) + 1)))
        assigned = Counter()
        for segment in segments:
            start, end = segment["edge_start"], segment["edge_end"]
            assigned.update(range(start, end + 1))
            self.assertEqual(segment["edge_count"], end - start + 1)
            self.assertEqual(segment["sample_indices"], list(range(start, end + 2)))
            for edge in range(start, end + 1):
                left, right = Decimal(str(coordinates[edge - 1])), Decimal(str(coordinates[edge]))
                expected = "flat" if right == left else "increasing" if right > left else "decreasing"
                self.assertEqual(segment["direction"], expected)
        self.assertEqual(assigned, Counter(range(1, len(samples))))
        for left, right in zip(segments, segments[1:]):
            self.assertNotEqual(left["direction"], right["direction"])
            self.assertEqual(left["edge_end"] + 1, right["edge_start"])
        for sample in samples:
            membership = [s["segment_id"] for s in segments if sample["sample_index"] in s["sample_indices"]]
            self.assertEqual(sample["segment_ids"], membership)
            self.assertLessEqual(len(membership), 2)
        self.assertEqual(len(result["shared_endpoints"]), max(0, len(segments) - 1))
        expected_shared = [p["sample_index"] for p in samples if len(p["segment_ids"]) == 2]
        self.assertEqual([p["sample_index"] for p in result["shared_endpoints"]], expected_shared)

    def test_bidirectional_edges_and_shared_turnaround(self):
        result = self.single()
        self.assert_partition(result, (0, 1, 2, 1, 0))
        self.assertEqual([s["direction"] for s in result["segments"]], ["increasing", "decreasing"])
        self.assertEqual([s["sample_indices"] for s in result["segments"]], [[1, 2, 3], [3, 4, 5]])
        self.assertEqual(result["shared_endpoints"], [{"sample_index": 3, "sample_id": "sample_3", "segment_ids": [1, 2]}])
        self.assertEqual(result["coordinate_reversals"], [{"kind": "direct", "from_segment_id": 1,
                         "to_segment_id": 2, "turnaround_sample_indices": [3], "turnaround_sample_ids": ["sample_3"]}])

    def test_plateau_turnaround_retains_every_distinct_observation(self):
        coordinates = (0, 1, "1.0", "1e0", 0)
        result = self.single(self.body(coordinates))
        self.assert_partition(result, coordinates)
        self.assertEqual([s["direction"] for s in result["segments"]], ["increasing", "flat", "decreasing"])
        self.assertEqual(result["coordinate_reversals"][0]["kind"], "across_plateau")
        self.assertEqual(result["coordinate_reversals"][0]["turnaround_sample_indices"], [2, 3, 4])
        self.assertEqual([p["sample_index"] for p in result["shared_endpoints"]], [2, 4])
        self.assertEqual(result["samples"][2]["segment_ids"], [2])

    def test_plateau_without_reversal_remains_three_segments(self):
        coordinates = (0, 1, 1, 2)
        result = self.single(self.body(coordinates))
        self.assert_partition(result, coordinates)
        self.assertEqual([s["direction"] for s in result["segments"]], ["increasing", "flat", "increasing"])
        self.assertEqual(result["coordinate_reversals"], [])

    def test_leading_and_trailing_flats_are_not_reversals(self):
        coordinates = (0, 0, 1, 1)
        result = self.single(self.body(coordinates))
        self.assert_partition(result, coordinates)
        self.assertEqual([s["direction"] for s in result["segments"]], ["flat", "increasing", "flat"])
        self.assertEqual(result["coordinate_reversals"], [])

    def test_all_flat_signed_zero_and_equivalent_decimal_forms(self):
        coordinates = ("+0", "-0.0", "0e10", "0.000")
        result = self.single(self.body(coordinates))
        self.assert_partition(result, coordinates)
        self.assertEqual(len(result["segments"]), 1)
        self.assertEqual(result["segments"][0]["direction"], "flat")
        self.assertEqual(result["coordinate_reversals"], [])
        self.assertEqual(result["shared_endpoints"], [])

    def test_singleton_preserved_with_zero_edges(self):
        report = self.evaluate(self.body(("1.25",)))
        result = report["results"][0]
        self.assert_partition(result, ("1.25",))
        self.assertEqual(result["unsegmented_sample_indices"], [1])
        self.assertEqual(result["samples"][0]["segment_ids"], [])
        for name in ("edges", "segments", "flat_edges", "shared_endpoint_samples", "coordinate_reversals"):
            self.assertEqual(report["counts"][name], 0)
        self.assertEqual(report["counts"]["singleton_sequences"], 1)

    def test_two_sample_all_directions(self):
        for coordinates in ((0, 1), (1, 0), (1, 1)):
            with self.subTest(coordinates=coordinates):
                result = self.single(self.body(coordinates))
                self.assert_partition(result, coordinates)
                self.assertEqual(result["segments"][0]["edge_count"], 1)

    def test_tiny_decimal_reversal_never_collapses_to_binary64_plateau(self):
        coordinates = ("1", "1.0000000000000000000000000000002", "1.0000000000000000000000000000001", "1")
        self.assertEqual(float(coordinates[0]), float(coordinates[1]))
        with localcontext() as context:
            context.prec = 1
            result = self.single(self.body(coordinates))
        self.assert_partition(result, coordinates)
        self.assertEqual([s["direction"] for s in result["segments"]], ["increasing", "decreasing"])
        self.assertEqual(result["coordinate_reversals"][0]["turnaround_sample_ids"], ["sample_2"])
        self.assertEqual(json.loads(json.dumps(result))["samples"][1]["coordinate_text"], coordinates[1])

    def test_extreme_exponents_compared_without_subtraction_or_float_conversion(self):
        coordinates = ("-1e200", "1e-200", "0", "1e200")
        with localcontext() as context:
            context.prec = 1
            context.Emax = 1
            context.Emin = -1
            result = self.single(self.body(coordinates))
        self.assert_partition(result, coordinates)
        self.assertEqual(len(result["coordinate_reversals"]), 2)

    def test_all_small_coordinate_paths_partition_exactly_once(self):
        # Exhaust all 1,093 direction strings of length 0..6. Generated paths
        # cover multiple reversals, repeated values and every flat placement.
        for length in range(7):
            for deltas in itertools.product((-1, 0, 1), repeat=length):
                coordinates = [0]
                for delta in deltas:
                    coordinates.append(coordinates[-1] + delta)
                points = [{"sample_id": f"p{i}", "source_record_index": i,
                           "source_line_start": i + 1, "source_line_end": i + 1,
                           "coordinate_text": str(value), "value": Decimal(value)}
                          for i, value in enumerate(coordinates, 1)]
                result = sweep.segment_sequence(KEY, points)
                self.assert_partition(result, coordinates)
                nonzero = [delta for delta in deltas if delta]
                expected_reversals = sum(a != b for a, b in zip(nonzero, nonzero[1:]))
                self.assertEqual(len(result["coordinate_reversals"]), expected_reversals)

    def test_units_are_explicit_axis_matched_and_not_converted(self):
        for axis, units in sweep.AXIS_UNITS.items():
            for unit in units:
                with self.subTest(axis=axis, unit=unit):
                    report = self.evaluate(axis=axis, coordinate_unit=unit)
                    self.assertEqual(report["axis"], axis)
                    self.assertEqual(report["coordinate_unit"], unit)
                    self.assertEqual([p["coordinate_text"] for p in report["results"][0]["samples"]], ["0", "1", "2", "1", "0"])
                    self.assertEqual(report["counts"]["coordinate_reversals"], 1)
        for axis, unit in (("voltage", "T"), ("magnetic-flux-density", "V"), ("current", "A"), ("voltage", "v")):
            with self.assertRaises(sweep.InputError):
                self.evaluate(axis=axis, coordinate_unit=unit)

    def test_equivalent_physical_coordinates_in_two_units_keep_same_partition(self):
        a = self.evaluate(self.body(("0", "0.001", "0.002", "0.001")), coordinate_unit="V")
        b = self.evaluate(self.body(("0", "1", "2", "1")), coordinate_unit="mV")
        self.assertEqual(a["results"][0]["segments"], b["results"][0]["segments"])
        self.assertNotEqual(a["results"][0]["samples"][1]["coordinate_text"], b["results"][0]["samples"][1]["coordinate_text"])

    def test_every_group_dimension_is_separate_even_reused_sample_ids(self):
        body = self.body((0, 1))
        for index in range(len(KEY)):
            key = list(KEY)
            key[index] = "other"
            body += self.body((10, 9), tuple(key))
        report = self.evaluate(body)
        self.assertEqual(report["counts"]["input_sequences"], 6)
        self.assertEqual(report["counts"]["edges"], 6)
        self.assertEqual([p["source_record_index"] for p in report["results"][-1]["samples"]], [11, 12])
        self.assertEqual([p["sample_index"] for p in report["results"][-1]["samples"]], [1, 2])

    def test_tuple_keys_do_not_collide_on_delimiters(self):
        first = ("a|b", "c", "d", "c", "s")
        second = ("a", "b|c", "d", "c", "s")
        report = self.evaluate(self.body(key=first) + self.body(key=second))
        self.assertEqual(report["counts"]["input_sequences"], 2)

    def test_noncontiguous_sequence_rejected_instead_of_rejoining(self):
        body = self.body((0, 1)) + self.body((4, 5), key=("l", "w", "other", "c", "s"))
        body += self.body((2, 3), ids=("new_1", "new_2"))
        with self.assertRaisesRegex(sweep.InputError, "contiguous"):
            self.evaluate(body)

    def test_duplicate_sample_id_rejected_but_duplicate_coordinate_preserved(self):
        with self.assertRaisesRegex(sweep.InputError, "unique"):
            self.evaluate(self.body((0, 1), ids=("same", "same")))
        self.assertEqual(len(self.single(self.body((1, 1)))["samples"]), 2)

    def test_opaque_sample_ids_are_not_sorted_or_normalized(self):
        ids = ("010", "001", "1", "sample,quoted", "μ")
        result = self.single(self.body(ids=ids))
        self.assertEqual([p["sample_id"] for p in result["samples"]], list(ids))
        self.assertEqual([p["source_record_index"] for p in result["samples"]], [1, 2, 3, 4, 5])

    def test_hash_lineage_and_input_bytes_unchanged(self):
        raw = (HEADER + self.body()).replace("\n", "\r\n").encode()
        self.input.write_bytes(b"\xef\xbb\xbf" + raw)
        before = self.input.read_bytes()
        report = sweep.evaluate(self.input, "voltage", "V")
        self.assertEqual(report["provenance"]["input_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.input.read_bytes(), before)
        self.assertEqual([p["source_line_start"] for p in report["results"][0]["samples"]], [2, 3, 4, 5, 6])
        self.assertEqual([p["source_line_end"] for p in report["results"][0]["samples"]], [2, 3, 4, 5, 6])

    def test_header_reordering_preserves_meaning(self):
        columns = tuple(reversed(sweep.COLUMNS))
        data = list(csv.reader(io.StringIO(HEADER + self.body())))
        self.write("".join(",".join(reversed(row)) + "\n" for row in data[1:]), ",".join(columns) + "\n")
        result = sweep.evaluate(self.input, "voltage", "V")["results"][0]
        self.assertEqual(result["samples"][0]["coordinate_text"], "0")

    def test_empty_missing_duplicate_extra_and_unknown_headers_rejected(self):
        for header in ("", HEADER.replace("sample_id,", ""), HEADER.replace("sample_id", "device"),
                       HEADER.rstrip() + ",extra\n", HEADER.replace("sample_id", "unknown")):
            with self.subTest(header=header):
                self.write(header=header)
                with self.assertRaises(sweep.InputError):
                    sweep.evaluate(self.input, "voltage", "V")
        for body in ("", "\n"):
            with self.assertRaises(sweep.InputError):
                self.evaluate(body)

    def test_blank_rows_width_quotes_and_invalid_encoding_rejected(self):
        for body in (self.body() + "\n", self.body() + "short,row\n",
                     self.body() + "too,many,values,a,b,c,d,e\n", '"unterminated'):
            with self.assertRaises(sweep.InputError):
                self.evaluate(body)
        self.input.write_bytes(b"\xff")
        with self.assertRaises(sweep.InputError):
            sweep.evaluate(self.input, "voltage", "V")

    def test_identifiers_are_not_stripped_or_echoed(self):
        for bad in ("", " padded", "padded ", "x\ny", "x\x00y", "x\x7fy", "x" * 129):
            for name in (*sweep.GROUP_COLUMNS, "sample_id"):
                fields = [*KEY, "sample_1", "0"]
                fields[sweep.COLUMNS.index(name)] = bad
                text = io.StringIO()
                csv.writer(text).writerow(fields)
                with self.subTest(name=name, bad=bad), self.assertRaises(sweep.InputError):
                    self.evaluate(text.getvalue())

    def test_numeric_syntax_and_resource_boundaries(self):
        bad_values = ("", "nan", "NaN", "inf", "-Infinity", "1_000", "1 V", "0x10", " 1", "1 ",
                      "1\n", "1e201", "1e-201", "1.0e-200", "1e9999", "1e10000", "1" * 33, "0" * 65, "１")
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(sweep.InputError):
                self.evaluate(self.body((value,)))
        for value in ("1e200", "-1e-200", "+.5", "1.", "1" * 32):
            self.assertEqual(self.single(self.body((value,)))["samples"][0]["coordinate_text"], value)

    def test_byte_row_and_sequence_limits(self):
        self.write()
        with patch.object(sweep, "MAX_BYTES", self.input.stat().st_size - 1), self.assertRaises(sweep.InputError):
            sweep.evaluate(self.input, "voltage", "V")
        with patch.object(sweep, "MAX_BYTES", self.input.stat().st_size):
            self.assertEqual(sweep.evaluate(self.input, "voltage", "V")["counts"]["input_samples"], 5)
        with patch.object(sweep, "MAX_ROWS", 4), self.assertRaises(sweep.InputError):
            self.evaluate()
        with patch.object(sweep, "MAX_ROWS", 5):
            self.assertEqual(self.evaluate()["counts"]["input_samples"], 5)
        with patch.object(sweep, "MAX_GROUPS", 1), self.assertRaises(sweep.InputError):
            self.evaluate(self.body() + self.body(key=("x", *KEY[1:])))

    def test_both_fixtures_regenerate_byte_for_byte(self):
        for example in ("hall", "gate"):
            generator = MODULE.parent / "examples" / "generate_synthetic.py"
            result = subprocess.run([sys.executable, str(generator), example], capture_output=True, check=True)
            fixture = MODULE.parent / "examples" / f"synthetic_{example}_coordinates.csv"
            self.assertEqual(result.stdout, fixture.read_bytes())

    def test_hall_281_fixture_two_segments_one_shared_observation(self):
        fixture = MODULE.parent / "examples" / "synthetic_hall_coordinates.csv"
        report = sweep.evaluate(fixture, "magnetic-flux-density", "mT")
        self.assertEqual(report["counts"], {"input_samples": 281, "input_sequences": 1, "edges": 280,
                         "segments": 2, "flat_edges": 0, "shared_endpoint_samples": 1,
                         "coordinate_reversals": 1, "singleton_sequences": 0})
        result = report["results"][0]
        self.assertEqual([s["edge_count"] for s in result["segments"]], [140, 140])
        self.assertEqual(result["shared_endpoints"][0]["sample_id"], "sample_141")
        self.assertEqual(sum(len(s["sample_indices"]) for s in result["segments"]), 282)
        self.assertEqual(len(result["samples"]), 281)

    def test_original_three_leg_voltage_path_has_two_shared_turnarounds(self):
        # Independently invented 0 -> +70 -> -70 -> 0 mV coordinates.
        # These are arithmetic test values, not a measurement archive.
        coordinates = list(range(71)) + list(range(69, -71, -1)) + list(range(-69, 1))
        report = self.evaluate(self.body(coordinates), coordinate_unit="mV")
        result = report["results"][0]
        self.assert_partition(result, coordinates)
        self.assertEqual(report["counts"]["input_samples"], 281)
        self.assertEqual(report["counts"]["edges"], 280)
        self.assertEqual([s["edge_count"] for s in result["segments"]], [70, 140, 70])
        self.assertEqual([s["direction"] for s in result["segments"]], ["increasing", "decreasing", "increasing"])
        self.assertEqual([p["sample_index"] for p in result["shared_endpoints"]], [71, 211])
        self.assertEqual(report["counts"]["coordinate_reversals"], 2)
        self.assertEqual(sum(len(s["sample_indices"]) for s in result["segments"]), 283)

    def test_gate_fixture_counts_include_plateau_without_reversal(self):
        fixture = MODULE.parent / "examples" / "synthetic_gate_coordinates.csv"
        report = sweep.evaluate(fixture, "voltage", "V")
        self.assertEqual(report["counts"], {"input_samples": 10, "input_sequences": 1, "edges": 9,
                         "segments": 6, "flat_edges": 3, "shared_endpoint_samples": 5,
                         "coordinate_reversals": 2, "singleton_sequences": 0})
        self.assertEqual([r["kind"] for r in report["results"][0]["coordinate_reversals"]], ["across_plateau", "direct"])

    def test_cli_explicit_destination_axis_and_unit_required(self):
        for extra, base in (((), None), (("--stdout",), []), (("--stdout",), ["--axis", "voltage"]),
                            (("--stdout",), ["--coordinate-unit", "V"]), (("--stdout", "--output", str(self.directory / "both.json")), None)):
            result = self.cli(extra, base=base)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertFalse((self.directory / "both.json").exists())

    def test_cli_json_success_including_plateaus_and_singleton(self):
        for coordinates in ((0, 1, 0), (0, 0), (0,)):
            result = self.cli(("--stdout",), self.body(coordinates))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout)["counts"]["input_samples"], len(coordinates))

    def test_cli_malformed_input_has_private_diagnostic_and_no_report(self):
        output = self.directory / "audit.json"
        body = self.body(("private_bad_coordinate",), key=("private_lot", *KEY[1:]))
        result = self.cli(("--output", str(output)), body)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertFalse(output.exists())
        self.assertNotIn("private", result.stderr)
        self.assertNotIn(str(self.input), result.stderr)
        for args in (["--axis", "private_axis", "--coordinate-unit", "V"],
                     ["--axis", "voltage", "--coordinate-unit", "private_unit"]):
            result = self.cli(("--stdout",), base=args)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("private", result.stderr)
            self.assertEqual(result.stdout, "")

    def test_output_exclusive_and_input_not_overwritten(self):
        output = self.directory / "existing.json"
        output.write_text("untouched")
        result = self.cli(("--output", str(output)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(output.read_text(), "untouched")
        self.write()
        before = self.input.read_bytes()
        result = self.cli(("--output", str(self.input)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.input.read_bytes(), before)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions and symlinks")
    def test_output_owner_permissions_and_live_or_dangling_symlinks(self):
        report = self.evaluate()
        output = self.directory / "audit.json"
        previous_umask = os.umask(0)
        try:
            sweep.write_report(report, output)
        finally:
            os.umask(previous_umask)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(json.loads(output.read_text())["counts"], report["counts"])
        for target in (output, self.directory / "missing.json"):
            link = self.directory / "link.json"
            link.symlink_to(target)
            result = self.cli(("--output", str(link)))
            self.assertEqual(result.returncode, 2)
            self.assertTrue(link.is_symlink())
            link.unlink()
        self.assertFalse((self.directory / "missing.json").exists())

    def test_missing_directory_input_and_output_errors_are_redacted(self):
        for path in (self.directory, self.directory / "private_missing.csv"):
            result = subprocess.run([sys.executable, str(MODULE), str(path), "--axis", "voltage",
                                     "--coordinate-unit", "V", "--stdout"], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(str(path), result.stderr)
            self.assertEqual(result.stdout, "")
        result = self.cli(("--output", str(self.directory / "private_missing_dir" / "file.json")))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("private", result.stderr)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_fifo_input_does_not_block(self):
        fifo = self.directory / "input_fifo"
        os.mkfifo(fifo)
        result = subprocess.run([sys.executable, str(MODULE), str(fifo), "--axis", "voltage",
                                 "--coordinate-unit", "V", "--stdout"], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_help_version_and_deterministic_json(self):
        for option in ("--help", "--version"):
            result = subprocess.run([sys.executable, str(MODULE), option], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
        a = self.cli(("--stdout",))
        b = self.cli(("--stdout",))
        self.assertEqual(a.stdout, b.stdout)
        self.assertNotIn("acquisition_direction", json.loads(a.stdout)["results"][0])


if __name__ == "__main__":
    unittest.main()
