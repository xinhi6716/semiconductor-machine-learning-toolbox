#!/usr/bin/env python3
"""Native workflow CLI checks against the unchanged Python scientific reference.

Every build under test executes the Rust binary. Python is only this development
test harness and the separately identified reference oracle, never the product
runtime. Runtime/source fingerprints and invocation descriptions intentionally
identify different implementations; all scientific data/status/lineage are exact.
"""
import copy
import csv
from decimal import Decimal
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = ROOT / "reference/python" if (ROOT / "reference/python").is_dir() else ROOT.parent
REFERENCE = Path(os.environ.get("SMLT_PYTHON_REFERENCE", DEFAULT_REFERENCE))
BINARY = Path(os.environ.get("SMLT_BINARY", ROOT / "target/debug/smlt")).resolve()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RRAM = load("rram_workflow_reference", REFERENCE / "examples/measurement-to-features/build_features.py")
FET = load("fet_workflow_reference", REFERENCE / "examples/fet-measurement-to-features/build_features.py")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized_json(name, value):
    value = copy.deepcopy(value)
    if name == "provenance.json":
        # Version 2 explicitly describes native provenance, not a Python run.
        value.pop("schema_version")
        value.pop("python_version", None)
        value.pop("runtime", None)
        value.pop("code_sha256", None)
        value.pop("source_sha256", None)
        # Serialization bytes differ. Each native checksum is independently
        # verified against its exact emitted bytes, never dropped without check.
        value.pop("output_sha256", None)
        if "invocation" in value:
            value["invocation"].pop("vth")
            value["invocation"].pop("log_slope")
    if name == "iv_readpoints.json":
        value.pop("invocation")
    return value


class NativeWorkflowChecks(unittest.TestCase):
    maxDiff = 3000

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="smlt-workflow-parity-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rram = self.root / "rram"
        shutil.copytree(REFERENCE / "examples/measurement-to-features/fixtures", self.rram)
        self.fet = self.root / "transfer.csv"
        shutil.copyfile(REFERENCE / "examples/fet-measurement-to-features/fixtures/transfer.csv", self.fet)
        self.counter = 0

    def read_rows(self, path):
        with path.open(newline="") as stream:
            return list(csv.DictReader(stream))

    def replace(self, path, rows, columns=None):
        if columns is None:
            columns = list(rows[0])
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        path.write_text(stream.getvalue())

    def assert_exact(self, actual, expected, path="report"):
        """Exact semantic comparison, including bool type and signed zero."""
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict, path)
            self.assertEqual(set(actual), set(expected), path)
            for key in expected:
                self.assert_exact(actual[key], expected[key], path + "." + key)
        elif isinstance(expected, list):
            self.assertIsInstance(actual, list, path)
            self.assertEqual(len(actual), len(expected), path)
            for index, (a, e) in enumerate(zip(actual, expected)):
                self.assert_exact(a, e, path + "[" + str(index) + "]")
        elif isinstance(expected, bool):
            self.assertIs(type(actual), bool, path)
            self.assertEqual(actual, expected, path)
        elif isinstance(expected, (int, float)):
            self.assertIsNot(type(actual), bool, path)
            self.assertEqual(actual, expected, path)
            if actual == expected == 0:
                self.assertEqual(math.copysign(1,actual), math.copysign(1,expected), path + " signed zero")
        else:
            self.assertEqual(actual, expected, path)

    def command(self, workflow, destination, options):
        if workflow == "rram":
            return [str(BINARY), "rram-features", "--input-dir", str(self.rram), "--output-dir", str(destination),
                    "--max-bracket-span-V", options.get("max_bracket_span_V", "0.11"),
                    *(["--accept-measurement-assumptions"] if options.get("assumptions_accepted", True) else [])]
        base = dict(voltage_unit="V", current_unit="nA", criterion_current="50", criterion_current_unit="nA",
                    current_polarity="positive", max_bracket_span="0.1", window_min="0.1", window_max="0.4",
                    assumptions_accepted=True)
        base.update(options)
        command = [str(BINARY), "fet-features", str(self.fet), "--output-dir", str(destination)]
        for key, value in base.items():
            if key == "assumptions_accepted":
                if value:
                    command.append("--accept-measurement-assumptions")
            elif value is not None:
                # Equals syntax also accepts a signed negative criterion.
                command.append("--" + key.replace("_", "-") + "=" + value)
        return command

    def reference(self, workflow, options):
        if workflow == "rram":
            return RRAM.build(self.rram, **dict(max_bracket_span_V="0.11", assumptions_accepted=True) | options)
        return FET.build(self.fet, **dict(voltage_unit="V", current_unit="nA", criterion_current="50",
                       criterion_current_unit="nA", current_polarity="positive", max_bracket_span="0.1",
                       window_min="0.1", window_max="0.4", assumptions_accepted=True) | options)

    def check(self, workflow, **options):
        self.counter += 1
        destination = self.root / ("out-" + str(self.counter))
        try:
            expected = self.reference(workflow, options)
        except (RRAM.InputError, FET.InputError):
            expected = None
        result = subprocess.run(self.command(workflow, destination, options), text=True, capture_output=True,
                                timeout=60, env={**os.environ, "PATH": ""}, cwd=self.root)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        self.assertNotIn("PRIVATE", result.stdout + result.stderr)
        self.assertNotIn("invented_device", result.stdout + result.stderr)
        self.assertNotIn("synthetic_device", result.stdout + result.stderr)
        if expected is None:
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertFalse(destination.exists())
            return None
        expected = {name: data.encode() if isinstance(data, str) else data for name, data in expected.items()}
        status = json.loads(expected.get("workflow_status.json", b'{"status":"complete"}'))["status"]
        self.assertEqual(result.returncode, 1 if status == "audit_only" else 0, result.stderr)
        actual = {path.name: path.read_bytes() for path in destination.iterdir()}
        self.assertEqual(set(actual), set(expected))
        provenance = json.loads(actual["provenance.json"])
        self.assertEqual(provenance["schema_version"], 2)
        self.assertEqual(provenance["runtime"]["implementation"], "native Rust")
        for name, digest in provenance.get("output_sha256", {}).items():
            self.assertEqual(sha(actual[name]), digest, name)
        sources = provenance.get("source_sha256", provenance.get("code_sha256"))
        self.assertIn("src/workflows.rs", sources)
        self.assertIn("src/common.rs", sources)
        self.assertIn("Cargo.lock", sources)
        self.assertFalse(any(name.endswith(".py") for name in sources))
        for name, digest in sources.items():
            self.assertEqual(sha((ROOT / name).read_bytes()), digest, name)
        for name in actual:
            if name.endswith(".json"):
                self.assert_exact(normalized_json(name, json.loads(actual[name])),
                                  normalized_json(name, json.loads(expected[name])), name)
            else:
                # Exact CSV bytes, not approximate numerical tolerance.
                self.assertEqual(actual[name], expected[name], name)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in destination.iterdir()))
        return actual

    def rram_mutation(self, name, mutate, **options):
        path = self.rram / name
        rows = self.read_rows(path)
        updated = mutate(rows)
        self.replace(path, rows if updated is None else updated)
        return self.check("rram", **options)

    def fet_mutation(self, mutate, one=False, **options):
        rows = self.read_rows(self.fet)
        if one:
            rows = rows[:7]
        updated = mutate(rows)
        self.replace(self.fet, rows if updated is None else updated)
        return self.check("fet", **options)

    def test_rram_original_fixture_exact_and_deterministic(self):
        actual = self.check("rram")
        self.assertEqual(actual, self.check("rram"))
        self.assertEqual(len(list(csv.DictReader(io.StringIO(actual["features.csv"].decode())))), 6)

    def test_rram_duplicate_manifest(self):
        self.rram_mutation("manifest.csv", lambda rows: rows + [rows[0]])

    def test_rram_duplicate_point(self):
        self.rram_mutation("iv_sweeps.csv", lambda rows: rows + [rows[0]])

    def test_rram_missing_measurement(self):
        self.rram_mutation("iv_sweeps.csv", lambda rows: rows[3:])

    def test_rram_extra_measurement(self):
        self.rram_mutation("iv_sweeps.csv", lambda rows: rows + [dict(rows[0], branch="extra")])

    def test_rram_missing_state(self):
        self.rram_mutation("manifest.csv", lambda rows: rows[1:])

    def test_rram_duplicate_state(self):
        self.rram_mutation("manifest.csv", lambda rows: rows[1].update(state=rows[0]["state"]))

    def test_rram_states_not_inferred(self):
        def mutate(rows):
            for row in rows:
                row["state"] = "LRS" if row["state"] == "HRS" else "HRS"
        self.rram_mutation("manifest.csv", mutate)

    def test_rram_condition_mismatch_between_states(self):
        rows = self.read_rows(self.rram / "conditions.csv")
        self.replace(self.rram / "conditions.csv", rows + [dict(rows[0], condition="other", read_delay_ms="11")])
        self.rram_mutation("manifest.csv", lambda rows: rows[1].update(condition="other"))

    def test_rram_condition_change_across_cycles(self):
        rows = self.read_rows(self.rram / "conditions.csv")
        self.replace(self.rram / "conditions.csv", rows + [dict(rows[0], condition="other", read_voltage_V="0.15")])
        def mutate(rows):
            rows[2]["condition"] = rows[3]["condition"] = "other"
        self.rram_mutation("manifest.csv", mutate)

    def test_rram_unused_condition(self):
        self.rram_mutation("conditions.csv", lambda rows: rows + [dict(rows[0], condition="unused")])

    def test_rram_duplicate_condition(self):
        self.rram_mutation("conditions.csv", lambda rows: rows + rows)

    def test_rram_unknown_condition(self):
        self.rram_mutation("conditions.csv", lambda rows: [dict(rows[0], condition="unreferenced")])

    def test_rram_device_cannot_move_batch(self):
        self.rram_mutation("manifest.csv", lambda rows: rows[2].update(batch="other_batch"))

    def test_rram_replicate_reused(self):
        def mutate(rows):
            rows[2]["replicate"] = rows[3]["replicate"] = rows[0]["replicate"]
        self.rram_mutation("manifest.csv", mutate)

    def test_rram_replicate_mismatch(self):
        self.rram_mutation("manifest.csv", lambda rows: rows[1].update(replicate="other_repeat"))

    def test_rram_zero_and_opposite_sign_current(self):
        original = (self.rram / "iv_sweeps.csv").read_bytes()
        for value in ("0", "-1e-6"):
            with self.subTest(value=value):
                (self.rram / "iv_sweeps.csv").write_bytes(original)
                def mutate(rows):
                    for row in rows[:3]:
                        row["current"] = value
                self.rram_mutation("iv_sweeps.csv", mutate)

    def test_rram_signed_negative_read(self):
        rows = self.read_rows(self.rram / "conditions.csv")
        rows[0]["read_voltage_V"] = "-0.1"
        self.replace(self.rram / "conditions.csv", rows)
        def mutate(rows):
            for row in rows:
                row["voltage"] = "-" + row["voltage"]
                row["current"] = "-" + row["current"]
        self.rram_mutation("iv_sweeps.csv", mutate)

    def test_rram_signed_zero_source_voltage_is_retained(self):
        def mutate(rows):
            for index in range(0,len(rows),3):
                rows[index]["voltage"] = "-0"
                rows[index]["current"] = "0"
        outputs = self.rram_mutation("iv_sweeps.csv",mutate,max_bracket_span_V="0.16")
        report = json.loads(outputs["iv_readpoints.json"])
        self.assertEqual(math.copysign(1,report["results"][0]["voltage_range_V"][0]),-1)

    def test_rram_bracket_and_extrapolation(self):
        self.check("rram", max_bracket_span_V="0.01")
        self.rram_mutation("conditions.csv", lambda rows: rows[0].update(read_voltage_V="0.3"))

    def test_rram_manifest_reordering(self):
        original = self.check("rram")
        reordered = self.rram_mutation("manifest.csv", lambda rows: rows[::-1])
        self.assertEqual(original["features.csv"], reordered["features.csv"])

    def test_rram_multicondition_device_specific_targets(self):
        rows = self.read_rows(self.rram / "conditions.csv")
        self.replace(self.rram / "conditions.csv", rows + [dict(rows[0], condition="other", read_voltage_V="0.2")])
        def mutate(rows):
            for row in rows:
                if row["device"].endswith("_3"):
                    row["condition"] = "other"
        self.rram_mutation("manifest.csv", mutate)

    def test_rram_formula_ids_rejected(self):
        self.rram_mutation("manifest.csv", lambda rows: rows[0].update(device="=PRIVATE_FORMULA"))

    def test_rram_numeric_and_header_rejection(self):
        path = self.rram / "iv_sweeps.csv"
        original = path.read_bytes()
        for bad in (original.replace(b"voltage,current", b"voltage,current,extra", 1),
                    original.replace(b"100000e-12", b"NaN", 1),
                    original.replace(b"100000e-12", b"1e-0201", 1)):
            with self.subTest():
                path.write_bytes(bad)
                self.check("rram")

    def test_rram_invalid_assumptions_and_bounds(self):
        for options in (dict(assumptions_accepted=False), dict(max_bracket_span_V="0"),
                        dict(max_bracket_span_V="NaN"), dict(max_bracket_span_V="1e-9999")):
            with self.subTest(options=options):
                self.check("rram", **options)

    def test_rram_condition_numeric_rejection(self):
        original = (self.rram / "conditions.csv").read_bytes()
        for change in (dict(read_voltage_V="0"), dict(temperature_K="0"), dict(read_delay_ms="-1"), dict(protocol="@PRIVATE")):
            with self.subTest(change=change):
                (self.rram / "conditions.csv").write_bytes(original)
                self.rram_mutation("conditions.csv", lambda rows: rows[0].update(change))

    def test_rram_blank_lines_bom_and_reordered_columns(self):
        for name in RRAM.INPUT_FILES:
            path = self.rram / name
            rows = self.read_rows(path)
            self.replace(path, rows, list(reversed(list(rows[0]))))
            path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n",b"\n\n",1))
        self.check("rram")

    def test_fet_original_fixture_exact_and_deterministic(self):
        actual = self.check("fet")
        self.assertEqual(actual, self.check("fet"))
        provenance = json.loads(actual["provenance.json"])
        self.assertEqual(provenance["counts"]["distinct_physical_device_ids"], 4)
        self.assertEqual(provenance["counts"]["feature_rows"], 16)

    def test_fet_interleaved_groups(self):
        def mutate(rows):
            groups = [rows[index:index + 7] for index in range(0,len(rows),7)]
            return [group[index] for index in range(7) for group in groups]
        self.fet_mutation(mutate)

    def test_fet_negative_current(self):
        def mutate(rows):
            for row in rows:
                row["drain_current"] = "-" + row["drain_current"]
        self.fet_mutation(mutate, criterion_current="-50",current_polarity="negative")

    def test_fet_negative_log_slope(self):
        def mutate(rows):
            currents = [row["drain_current"] for row in rows][::-1]
            for row, current in zip(rows,currents):
                row["drain_current"] = current
        self.fet_mutation(mutate,one=True)

    def test_fet_zero_log_slope(self):
        def mutate(rows):
            for row in rows[1:5]:
                row["drain_current"] = "10"
        self.fet_mutation(mutate,one=True)

    def test_fet_exact_criterion(self):
        self.fet_mutation(lambda rows: None,one=True,criterion_current="100")

    def test_fet_explicit_units(self):
        def mutate(rows):
            for row in rows:
                for key in ("gate_voltage","drain_voltage","body_voltage"):
                    row[key] = str(Decimal(row[key]) * 1000)
                row["drain_current"] = str(Decimal(row["drain_current"]) / 1000)
        self.fet_mutation(mutate,voltage_unit="mV",current_unit="uA",criterion_current="0.05",criterion_current_unit="uA",max_bracket_span="100",window_min="100",window_max="400")

    def test_fet_exact_decimal_window(self):
        self.fet_mutation(lambda rows: None,one=True,window_min="0.10000000000000000000000000001")

    def test_fet_vth_rejects_log_passes(self):
        self.fet_mutation(lambda rows: rows[0].update(drain_current="-1"))

    def test_fet_vth_passes_log_rejects(self):
        def mutate(rows):
            rows[0]["drain_current"] = rows[1]["drain_current"] = "0"
        self.fet_mutation(mutate,one=True)

    def test_fet_nonmonotonic_current(self):
        self.fet_mutation(lambda rows: rows[5].update(drain_current="9000"),one=True)

    def test_fet_absent_crossing(self):
        self.check("fet",criterion_current="1e10")

    def test_fet_wide_bracket(self):
        self.check("fet",max_bracket_span="0.099")

    def test_fet_short_window(self):
        self.check("fet",window_min="0.2",window_max="0.3")

    def test_fet_duplicate_gate_voltage(self):
        self.fet_mutation(lambda rows: rows[2].update(gate_voltage=rows[1]["gate_voltage"]),one=True)

    def test_fet_changed_bias_outside_window(self):
        self.fet_mutation(lambda rows: rows[0].update(drain_voltage="0.06"),one=True)

    def test_fet_formula_prefix_export_rejection(self):
        original = self.fet.read_bytes()
        for prefix in "=+-@":
            with self.subTest(prefix=prefix):
                self.fet.write_bytes(original)
                def mutate(rows):
                    for row in rows:
                        row["device"] = prefix + "PRIVATE_" + row["device"]
                self.fet_mutation(mutate)

    def test_fet_quoted_tuple_ids(self):
        def mutate(rows):
            for row in rows:
                row["lot"] += ",quoted|part"
                row["condition"] = 'protocol"quoted'
        self.fet_mutation(mutate)

    def test_fet_bom_blank_and_reordered_header(self):
        rows = self.read_rows(self.fet)
        self.replace(self.fet,rows,list(reversed(list(rows[0]))))
        self.fet.write_bytes(b"\xef\xbb\xbf" + self.fet.read_bytes().replace(b"\n",b"\n\n",1))
        self.check("fet")

    def test_fet_invalid_arguments(self):
        for options in (dict(assumptions_accepted=False),dict(max_bracket_span=None),dict(max_bracket_span="0"),
                        dict(window_min="0.4",window_max="0.1"),dict(criterion_current="0"),
                        dict(criterion_current="-50"),dict(current_unit="unknown"),dict(window_min="NaN")):
            with self.subTest(options=options):
                self.check("fet",**options)

    def test_fet_malformed_header_and_numbers(self):
        original = self.fet.read_bytes()
        for data in (b"PRIVATE invalid header\n",original.replace(b"300,",b"NaN,",1),
                     original.replace(b"drain_current\n",b"drain_current,target\n",1)):
            with self.subTest():
                self.fet.write_bytes(data)
                self.check("fet")

    def test_workflow_oversized_and_nonregular_inputs(self):
        original = self.fet.read_bytes()
        self.fet.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
        self.check("fet")
        self.fet.unlink()
        self.fet.mkdir()
        self.check("fet")
        self.fet.rmdir()
        self.fet.write_bytes(original)
        manifest = self.rram / "manifest.csv"
        manifest.unlink()
        manifest.mkdir()
        self.check("rram")

    @unittest.skipUnless(hasattr(os,"mkfifo"), "POSIX FIFO only")
    def test_workflow_fifo_never_blocks(self):
        self.fet.unlink()
        os.mkfifo(self.fet)
        self.check("fet")

    def test_workflow_no_overwrite_files_directories_and_symlinks(self):
        for workflow in ("rram","fet"):
            for kind in ("directory","file","live_symlink","dangling_symlink"):
                with self.subTest(workflow=workflow,kind=kind):
                    destination = self.root / (workflow + "-" + kind)
                    if kind == "directory":
                        destination.mkdir()
                        (destination / "keep").write_bytes(b"keep")
                    elif kind == "file":
                        destination.write_bytes(b"keep")
                    else:
                        destination.symlink_to(self.fet if kind == "live_symlink" else self.root / "missing")
                    before = destination.lstat()
                    result = subprocess.run(self.command(workflow,destination,{}),capture_output=True,timeout=60)
                    self.assertEqual(result.returncode,2,result.stderr)
                    self.assertEqual(destination.lstat().st_ino,before.st_ino)
                    if kind == "directory":
                        self.assertEqual((destination / "keep").read_bytes(),b"keep")
                    elif kind == "file":
                        self.assertEqual(destination.read_bytes(),b"keep")
                    else:
                        self.assertTrue(destination.is_symlink())

    def test_help_and_unknown_options_are_private(self):
        for workflow in ("rram-features","fet-features"):
            result = subprocess.run([str(BINARY),workflow,"--help"],capture_output=True,text=True)
            self.assertEqual(result.returncode,0)
            self.assertIn("Provenance schema 2",result.stdout)
            self.assertIn("--accept-measurement-assumptions",result.stdout)
            result = subprocess.run([str(BINARY),workflow,"PRIVATE_PATH","--unknown=PRIVATE_VALUE"],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertNotIn("PRIVATE",result.stdout+result.stderr)

    def test_long_option_prefixes_match_original_parser(self):
        for workflow in ("rram","fet"):
            with self.subTest(workflow=workflow):
                original = self.command(workflow,self.root/(workflow+"-abbreviated"),{})
                replacements = {"--input-dir":"--input-d", "--output-dir":"--output-d",
                                "--max-bracket-span-V":"--max-bracket", "--accept-measurement-assumptions":"--accept-m",
                                "--voltage-unit":"--voltage-u", "--current-unit":"--current-u",
                                "--current-polarity":"--current-p", "--criterion-current-unit":"--criterion-current-u",
                                "--max-bracket-span":"--max-bracket", "--window-min":"--window-mi", "--window-max":"--window-ma"}
                command = []
                for token in original:
                    name, separator, value = token.partition("=")
                    command.append(replacements.get(name,name) + separator + value)
                result = subprocess.run(command,capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stderr)
                reference = [sys.executable, str(RRAM.__file__ if workflow=="rram" else FET.__file__),*command[2:]]
                reference[reference.index("--output-d")+1] = str(self.root/(workflow+"-python-prefix"))
                result = subprocess.run(reference,capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((self.root/(workflow+"-abbreviated")/"features.csv").read_bytes(),
                                 (self.root/(workflow+"-python-prefix")/"features.csv").read_bytes())
        for token in ("--current", "--window-m", "--PRIVATE-unknown"):
            with self.subTest(token=token):
                native = subprocess.run([str(BINARY),"fet-features",token,"PRIVATE_VALUE"],capture_output=True,text=True)
                reference = subprocess.run([sys.executable,str(FET.__file__),token,"PRIVATE_VALUE"],capture_output=True,text=True)
                self.assertEqual(native.returncode,reference.returncode)
                self.assertEqual(native.returncode,2)
                self.assertNotIn("PRIVATE",native.stdout+native.stderr)
        for workflow in ("rram-features","fet-features"):
            result = subprocess.run([str(BINARY),workflow,"--he"],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_signed_zero_comparator_distinguishes_sign(self):
        self.assert_exact({"zero":-0.0},{"zero":-0.0})
        with self.assertRaises(AssertionError):
            self.assert_exact({"zero":0.0},{"zero":-0.0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
