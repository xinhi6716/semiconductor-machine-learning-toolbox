import contextlib
import copy
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

ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


audit = module(ROOT / "bosch_identity_audit.py", "bosch_audit")
synthetic = module(ROOT / "examples/generate_synthetic.py", "bosch_synthetic")


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bosch_tool_test_")
        self.directory = Path(self.temp.name)
        self.fixtures = synthetic.fixture_bytes()
        self.csv = self.fixtures["synthetic_inventory.csv"]
        self.meta = self.fixtures["synthetic_channels.json"]
        self.input = self.directory / "private_input.csv"
        self.sidecar = self.directory / "private_sidecar.json"
        self.input.write_bytes(self.csv)
        self.sidecar.write_bytes(self.meta)

    def tearDown(self):
        self.temp.cleanup()

    def mutate_metadata(self, operation):
        metadata = json.loads(self.meta)
        operation(metadata)
        return json.dumps(metadata).encode()

    def one_row(self, identities=(b"2099-01-02_01", b"9", b"1", b"F10"), opaque=None):
        if opaque is None:
            opaque = [b"opaque"] * 7
        return (",".join(audit.HEADER).encode() + b"\n" + b",".join(list(identities) + opaque) + b"\n")

    def cli(self, *extra):
        return subprocess.run([sys.executable, str(ROOT / "bosch_identity_audit.py"), str(self.input),
                               "--channel-metadata", str(self.sidecar), *extra], capture_output=True,
                              text=True, timeout=10)

    def test_fixture_inventory_preserves_order_nulls_and_duplicates(self):
        report = audit.audit(self.csv, self.meta)
        inv = report["identity_inventory"]
        self.assertEqual(inv["row_count"], 21)
        self.assertEqual([row["logical_data_row_ordinal"] for row in inv["rows"]], list(range(1, 22)))
        self.assertEqual(inv["null_counts"], dict(experiment_key=2, lot_number=1, wafer_number=1, loc_id=1))
        self.assertEqual(inv["incomplete_wafer_key_row_ordinals"], [19, 20])
        self.assertEqual(inv["exact_duplicate_four_field_tuples"][0]["source_row_ordinals"], [1, 21])
        self.assertEqual(inv["rows_dropped_or_filled"], 0)
        self.assertEqual(inv["complete_identity_keys"][0]["duplicate_nonnull_location_labels"], ["F10"])
        self.assertEqual(inv["complete_identity_key_count"], 2)

    def test_exact_channel_alignment_not_positions_or_intersection_selection(self):
        report = audit.audit(self.csv, self.meta)["channel_alignment"]
        first, second, third = report["groups"]
        self.assertEqual([group["feature_count"] for group in report["groups"]], [44, 31, 31])
        self.assertEqual((report["common_label_count"], report["union_label_count"]), (30, 45))
        self.assertEqual(first["canonical_name_column_map"][1]["zero_based_column_index"], 2)
        self.assertEqual(second["canonical_name_column_map"][0]["zero_based_column_index"], 30)
        self.assertIsNone(third["canonical_name_column_map"][-1]["zero_based_column_index"])
        self.assertEqual(third["missing_canonical_labels"], ["synthetic_channel_30"])
        self.assertEqual(len(first["noncanonical_labels_in_source_order"]), 13)
        self.assertFalse(report["automatic_feature_selection"])

    def test_name_candidates_not_physical_or_finite_eligibility(self):
        inv = audit.audit(self.csv, self.meta)["identity_inventory"]
        self.assertTrue(all(row["name_join_status"] == "EXACT_NAME_CANDIDATE" for row in inv["complete_identity_keys"]))
        self.assertEqual(inv["process_groups_without_complete_identity_name_candidate"], ["/Day_2099_01_02_Wafer_03"])
        self.assertFalse(inv["physical_wafer_identity_or_independence_verified"])
        self.assertTrue(all(row["finite_target_eligibility_verified"] is False for row in inv["complete_identity_keys"]))

    def test_no_scientific_fields_emitted_or_utf8_decoded(self):
        raw = self.one_row(opaque=[b'"SECRET\xff\x00,\nquoted ""text"""'] * 7)
        report = audit.audit(raw, self.meta)
        dumped = json.dumps(report)
        for token in ("SECRET", "preox_thickness", "postox_thickness", "stepheight", "si_etch", "oxide_etch"):
            self.assertNotIn(token, dumped)
        self.assertEqual(report["identity_inventory"]["row_count"], 1)

    def test_all_null_full_width_record_retained(self):
        result = audit.identity_records(self.one_row((b"", b"", b"", b"")))
        self.assertEqual(result, [{"logical_data_row_ordinal": 1, **dict.fromkeys(audit.IDENTITY_FIELDS)}])

    def test_empty_is_not_whitespace_and_unicode_not_normalized(self):
        raw = self.one_row((b" ", b"001", "裝置".encode(), b" F10 "))
        row = audit.identity_records(raw)[0]
        self.assertEqual([row[name] for name in audit.IDENTITY_FIELDS], [" ", "001", "裝置", " F10 "])

    def test_controls_and_bad_identity_encoding_rejected_without_values(self):
        for token in (b"\xff", b"x\x00secret", b'"x\nsecret"', b"x\x7fsecret", b"x\tsecret", "x\u0085secret".encode(), "x\u009fsecret".encode()):
            with self.subTest(token=token), self.assertRaises(audit.InputError) as caught:
                audit.identity_records(self.one_row((token, b"9", b"1", b"F10")))
            self.assertNotIn("secret", str(caught.exception))

    def test_quoted_identity_commas_and_escaped_quotes(self):
        row = audit.identity_records(self.one_row((b'"one,two"', b'"a""b"', b"1", b"F10")))[0]
        self.assertEqual(row["experiment_key"], "one,two")
        self.assertEqual(row["lot_number"], 'a"b')

    def test_multiline_opaque_field_uses_logical_row_ordinals(self):
        first = self.one_row(opaque=[b'"line1\nline2"'] * 7)
        second = b",".join([b"", b"", b"", b"F2"] + [b"opaque"] * 7) + b"\n"
        rows = audit.identity_records(first + second)
        self.assertEqual([row["logical_data_row_ordinal"] for row in rows], [1, 2])

    def test_bom_crlf_and_no_final_newline(self):
        for raw in (b"\xef\xbb\xbf" + self.csv, self.csv.replace(b"\n", b"\r\n"), self.csv.rstrip(b"\n")):
            self.assertEqual(len(audit.identity_records(raw)), 21)

    def test_bad_csv_shapes_and_framing_fail_closed(self):
        header = ",".join(audit.HEADER).encode() + b"\n"
        bad = [b"", header, header + b"\n", self.csv + b"\n", self.one_row() + b"short,row\n",
               self.one_row(opaque=[b"x"] * 8), self.one_row(opaque=[b"x"] * 6),
               self.one_row(opaque=[b'"unclosed'] + [b"x"] * 6),
               self.one_row(opaque=[b'"closed"junk'] + [b"x"] * 6),
               self.one_row(opaque=[b'bad"quote'] + [b"x"] * 6), self.one_row().replace(b"\n", b"\r")]
        for raw in bad:
            with self.subTest(raw=raw[:30]), self.assertRaises(audit.InputError):
                audit.identity_records(raw)

    def test_header_exact_order_and_no_extra_columns(self):
        for change in (lambda value: value.replace(b"experiment_key", b"Experiment_key", 1),
                       lambda value: value.replace(b"lot_number,wafer_number", b"wafer_number,lot_number", 1),
                       lambda value: value.replace(b"si_etch\n", b"si_etch,extra\n", 1)):
            with self.assertRaises(audit.InputError):
                audit.identity_records(change(self.csv))

    def test_identity_and_csv_resource_bounds(self):
        self.assertEqual(audit.identity_text(b"a" * 256), "a" * 256)
        with self.assertRaises(audit.InputError):
            audit.identity_text(b"a" * 257)
        with patch.object(audit, "MAX_ROWS", 20), self.assertRaises(audit.InputError):
            audit.identity_records(self.csv)
        with patch.object(audit, "MAX_CSV_BYTES", len(self.csv) - 1), self.assertRaises(audit.InputError):
            audit.identity_records(self.csv)
        with patch.object(audit, "MAX_RECORD_BYTES", 100), self.assertRaises(audit.InputError):
            audit.identity_records(self.one_row(opaque=[b"x" * 101] * 7))

    def test_actual_2000_rows_boundary(self):
        header, row = self.one_row().split(b"\n", 1)
        self.assertEqual(len(audit.identity_records(header + b"\n" + row * 2000)), 2000)
        with self.assertRaises(audit.InputError):
            audit.identity_records(header + b"\n" + row * 2001)

    def test_metadata_duplicate_keys_top_schema_and_constants(self):
        for raw in (b'{"a":1,"a":2}', b'NaN', b'[]', b'{"schema_version":NaN}', b'\xff'):
            with self.assertRaises(audit.InputError):
                audit.channel_metadata(raw)
        raw = self.meta.replace(b'"schema_version":', b'"schema_version":"bad","schema_version":', 1)
        with self.assertRaises(audit.InputError):
            audit.channel_metadata(raw)

    def test_metadata_no_extra_fields_or_numeric_arrays(self):
        for operation in (lambda m: m.update(data=[1.0]),
                          lambda m: m["groups"][0].update(times=[0.0]),
                          lambda m: m.update(schema_version="wrong"),
                          lambda m: m.update(process_file_sha256="A" * 64)):
            with self.assertRaises(audit.InputError):
                audit.channel_metadata(self.mutate_metadata(operation))

    def test_group_and_label_duplicates_rejected(self):
        for operation in (lambda m: m["groups"].append(copy.deepcopy(m["groups"][0])),
                          lambda m: m["groups"][0]["feature_labels"].__setitem__(1, m["groups"][0]["feature_labels"][0]),
                          lambda m: m["canonical_labels"].__setitem__(1, m["canonical_labels"][0])):
            with self.assertRaises(audit.InputError):
                audit.channel_metadata(self.mutate_metadata(operation))

    def test_label_invalid_encoding_control_and_empty(self):
        for value in ("", "x\n", "x\u0085", "x\u009f", "\ud800", "a" * 257, 7, None):
            for section in ("canonical", "group"):
                def operation(m):
                    target = m["canonical_labels"] if section == "canonical" else m["groups"][0]["feature_labels"]
                    target[0] = value
                with self.subTest(value=value, section=section), self.assertRaises(audit.InputError):
                    audit.channel_metadata(self.mutate_metadata(operation))

    def test_exact_case_whitespace_labels_not_silently_equated(self):
        raw = self.mutate_metadata(lambda m: m["groups"][1]["feature_labels"].__setitem__(30, "Synthetic_channel_00 "))
        report = audit.audit(self.csv, raw)["channel_alignment"]["groups"][1]
        self.assertIn("synthetic_channel_00", report["missing_canonical_labels"])
        self.assertIn("Synthetic_channel_00 ", report["noncanonical_labels_in_source_order"])

    def test_group_names_and_calendar_fail_closed(self):
        for name in ("/Day_2099_02_30_Wafer_01", "/Day_2099_01_02_Wafer_00", "/Day_2099_01_02_Wafer_1", "../secret", None):
            with self.assertRaises(audit.InputError):
                audit.channel_metadata(self.mutate_metadata(lambda m: m["groups"][0].update(group_path=name)))

    def test_feature_width_group_count_and_metadata_byte_limits(self):
        for operation in (lambda m: m["groups"][0]["feature_labels"].pop(),
                          lambda m: m["canonical_labels"].pop(), lambda m: m.update(groups=[])):
            with self.assertRaises(audit.InputError):
                audit.channel_metadata(self.mutate_metadata(operation))
        with patch.object(audit, "MAX_GROUPS", 2), self.assertRaises(audit.InputError):
            audit.channel_metadata(self.meta)
        with patch.object(audit, "MAX_METADATA_BYTES", 10), self.assertRaises(audit.InputError):
            audit.channel_metadata(self.meta)

    def test_inconsistent_and_unsupported_keys_not_repaired(self):
        for key, wafer, expected in ((b"2099-01-02_01", b"01", "WAFER_NUMBER_TEXT_MISMATCH"),
                                     (b"2099-02-30_01", b"1", "UNSUPPORTED_EXPERIMENT_KEY"),
                                     (b"other", b"1", "UNSUPPORTED_EXPERIMENT_KEY"),
                                     (b"2099-01-02_09", b"9", "NO_EXACT_PROCESS_GROUP")):
            inv = audit.audit(self.one_row((key, b"9", wafer, b"F10")), self.meta)["identity_inventory"]
            self.assertEqual(inv["complete_identity_keys"][0]["name_join_status"], expected)
            self.assertEqual(inv["row_count"], 1)

    def test_two_lots_claiming_same_process_group_are_ambiguous(self):
        raw = self.one_row()
        raw += b"2099-01-02_01,other_lot,1,F10," + b",".join([b"x"] * 7) + b"\n"
        keys = audit.audit(raw, self.meta)["identity_inventory"]["complete_identity_keys"]
        self.assertEqual([key["name_join_status"] for key in keys], ["AMBIGUOUS_MULTIPLE_IDENTITY_KEYS"] * 2)

    def test_hashes_exact_and_process_claim_not_verified(self):
        report = audit.audit(self.csv, self.meta)
        self.assertEqual(report["provenance"]["csv_sha256"], hashlib.sha256(self.csv).hexdigest())
        self.assertEqual(report["provenance"]["channel_metadata_sha256"], hashlib.sha256(self.meta).hexdigest())
        self.assertFalse(report["provenance"]["declared_process_hash_verified_against_file"])

    def test_empty_intersection_retains_all_groups(self):
        def operation(m):
            m["groups"][1]["feature_labels"] = ["unrelated_" + str(i) for i in range(31)]
        report = audit.audit(self.csv, self.mutate_metadata(operation))["channel_alignment"]
        self.assertEqual(report["common_labels"], [])
        self.assertEqual(len(report["groups"]), 3)
        self.assertEqual(len(report["groups"][1]["missing_canonical_labels"]), 31)

    def test_report_cap_enforced_before_output(self):
        with patch.object(audit, "MAX_REPORT_BYTES", 1), self.assertRaises(audit.InputError):
            audit.audit(self.csv, self.meta)
        with self.assertRaises(ValueError):
            audit.report_bytes({"invalid": float("nan")})

    def test_cli_stdout_and_new_private_output(self):
        run = self.cli("--stdout")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["identity_inventory"]["row_count"], 21)
        destination = self.directory / "new.json"
        run = self.cli("--output", str(destination))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, "")
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(self.input.read_bytes(), self.csv)
        self.assertEqual(self.sidecar.read_bytes(), self.meta)

    def test_output_no_overwrite_input_directory_and_symlinks(self):
        existing = self.directory / "existing.json"
        existing.write_bytes(b"untouched")
        live = self.directory / "live"; live.symlink_to(existing)
        dangling = self.directory / "dangling"; dangling.symlink_to(self.directory / "absent")
        for destination in (existing, self.input, self.directory, live, dangling):
            run = self.cli("--output", str(destination))
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, "")
        self.assertEqual(existing.read_bytes(), b"untouched")
        self.assertEqual(self.input.read_bytes(), self.csv)
        self.assertTrue(dangling.is_symlink())

    def test_cli_required_options_and_redacted_errors(self):
        for extra in ((), ("--stdo",), ("--stdout", "--output", "private_secret"), ("--stdout", "--unknown", "private_secret")):
            run = self.cli(*extra)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, "")
            self.assertNotIn("private_", run.stderr)
        self.input.write_bytes(self.one_row((b"\xffprivate_secret", b"1", b"1", b"F10")))
        run = self.cli("--stdout")
        self.assertEqual(run.returncode, 2)
        self.assertNotIn("private_", run.stderr)
        self.assertEqual(run.stdout, "")

    def test_regular_only_nonblocking_input_and_nul_path_redacted(self):
        for path in (self.directory, Path("/dev/null"), "private\x00secret"):
            with self.assertRaises(audit.InputError) as caught:
                audit.read_regular(path, 100)
            self.assertNotIn("secret", str(caught.exception))
        if hasattr(os, "mkfifo"):
            fifo = self.directory / "pipe"; os.mkfifo(fifo)
            with self.assertRaises(audit.InputError):
                audit.read_regular(fifo, 100)

    def test_generator_matches_checked_in_original_fixtures(self):
        for name, blob in self.fixtures.items():
            self.assertEqual((ROOT / "examples" / name).read_bytes(), blob)

    def test_partial_output_failure_is_fatal_not_complete(self):
        destination = self.directory / "partial.json"
        real_fdopen = os.fdopen
        class BrokenWriter:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): self.handle.close()
            def write(self, value):
                self.handle.write(b"{")
                raise OSError("private_secret")
        def wrapped(fd, mode, *args, **kwargs):
            handle = real_fdopen(fd, mode, *args, **kwargs)
            return BrokenWriter(handle) if mode == "wb" else handle
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(audit.os, "fdopen", side_effect=wrapped), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = audit.main([str(self.input), "--channel-metadata", str(self.sidecar), "--output", str(destination)])
        self.assertEqual(result, 2)
        self.assertEqual(destination.read_bytes(), b"{")
        self.assertNotIn("private_secret", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_stdout_flush_failure_is_fatal_and_stream_closed(self):
        class BrokenBufferedStdout:
            closed = False
            def write(self, value): return len(value)
            def flush(self): raise OSError("private_secret")
            def close(self):
                self.closed = True
                raise OSError("private_secret")
        stdout, stderr = BrokenBufferedStdout(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = audit.main([str(self.input), "--channel-metadata", str(self.sidecar), "--stdout"])
        self.assertEqual(result, 2)
        self.assertTrue(stdout.closed)
        self.assertIn("No complete report claimed", stderr.getvalue())
        self.assertNotIn("private_secret", stderr.getvalue())

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/dev/full").exists(),
                         "requires Linux /dev/full for a real deferred write failure")
    def test_buffered_stdout_error_keeps_documented_exit_status(self):
        # Make the write fit in the Python buffer so failure occurs only on flush.
        script = """
import importlib.util, io, sys
spec = importlib.util.spec_from_file_location("bosch_audit", sys.argv[1])
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
sys.stdout = io.TextIOWrapper(io.BufferedWriter(io.FileIO("/dev/full", "wb"),
                                              buffer_size=1024 * 1024))
raise SystemExit(audit.main(sys.argv[2:]))
"""
        run = subprocess.run([sys.executable, "-c", script, str(ROOT / "bosch_identity_audit.py"),
                              str(self.input), "--channel-metadata", str(self.sidecar), "--stdout"],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(run.stdout, "")
        self.assertEqual(run.stderr,
                         "Audit failed: input/output/serialization error. No complete report claimed.\n")

    def test_normal_error_diagnostic_stays_open_and_exact(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = audit.main([])
        self.assertEqual(result, 2)
        self.assertFalse(stderr.closed)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(),
                         "Audit failed: Invalid command-line options; use --help. No complete report claimed.\n")

    def test_failed_stderr_does_not_mask_input_or_stdout_error(self):
        for phase in ("write", "flush"):
            for error_type in (OSError, ValueError):
                for original_error in ("input", "stdout"):
                    with self.subTest(phase=phase, error=error_type, original=original_error):
                        class BrokenDiagnostic:
                            closed = False
                            def write(self, value):
                                if phase == "write": raise error_type("private_secret")
                                return len(value)
                            def flush(self): raise error_type("private_secret")
                            def close(self):
                                self.closed = True
                                raise error_type("private_secret")
                        class BrokenOutput(io.StringIO):
                            def write(self, value): raise OSError("private_secret")
                        stdout = BrokenOutput() if original_error == "stdout" else io.StringIO()
                        stderr = BrokenDiagnostic()
                        args = ([str(self.input), "--channel-metadata", str(self.sidecar), "--stdout"]
                                if original_error == "stdout" else [])
                        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            result = audit.main(args)
                        self.assertEqual(result, 2)
                        self.assertTrue(stderr.closed)
                        if original_error == "input":
                            self.assertEqual(stdout.getvalue(), "")

    def test_closed_or_missing_stderr_does_not_use_stdout(self):
        closed = io.StringIO()
        closed.close()
        for stderr in (closed, None):
            with self.subTest(stderr=stderr):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = audit.main([])
                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), "")

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/dev/full").exists(),
                         "requires Linux /dev/full for a real diagnostic failure")
    def test_cli_stderr_device_failure_keeps_exit_status(self):
        with open("/dev/full", "wb") as stderr:
            run = subprocess.run([sys.executable, str(ROOT / "bosch_identity_audit.py"),
                                  str(self.input), "--channel-metadata", str(self.sidecar)],
                                 stdout=subprocess.PIPE, stderr=stderr, timeout=10)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, b"")

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/dev/full").exists(),
                         "requires Linux /dev/full for real simultaneous output failures")
    def test_cli_stdout_and_stderr_device_failures_keep_exit_status(self):
        with open("/dev/full", "wb") as sink:
            run = subprocess.run([sys.executable, str(ROOT / "bosch_identity_audit.py"),
                                  str(self.input), "--channel-metadata", str(self.sidecar), "--stdout"],
                                 stdout=sink, stderr=sink, timeout=10)
        self.assertEqual(run.returncode, 2)

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/dev/full").exists(),
                         "requires Linux /dev/full for a deferred diagnostic failure")
    def test_buffered_stderr_failure_keeps_exit_status(self):
        script = """
import importlib.util, io, sys
spec = importlib.util.spec_from_file_location("bosch_audit", sys.argv[1])
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
sys.stderr = io.TextIOWrapper(io.BufferedWriter(io.FileIO("/dev/full", "wb"),
                                              buffer_size=1024 * 1024))
raise SystemExit(audit.main([]))
"""
        run = subprocess.run([sys.executable, "-c", script, str(ROOT / "bosch_identity_audit.py")],
                             capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(run.stdout, b"")
        self.assertEqual(run.stderr, b"")

    def test_unexpected_diagnostic_exception_is_not_silenced(self):
        class UnexpectedBug:
            def write(self, value): raise RuntimeError("unexpected stream implementation bug")
        with contextlib.redirect_stderr(UnexpectedBug()), self.assertRaises(RuntimeError):
            audit.main([])


if __name__ == "__main__":
    unittest.main()
