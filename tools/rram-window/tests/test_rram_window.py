"""Synthetic contract, arithmetic, conditioning, and CLI regression tests."""

import importlib.util
import io
import json
import math
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "rram_window.py"
SPEC = importlib.util.spec_from_file_location("rram_window", SCRIPT)
rw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rw
SPEC.loader.exec_module(rw)
HEADER = "device,cycle,state,read_condition,read_voltage,resistance\n"
DEFAULTS = dict(voltage_unit="V", resistance_unit="ohm", hrs_label="H", lrs_label="L")


def evaluate(rows, **options):
    return rw.evaluate_stream(io.StringIO(HEADER + rows), **(DEFAULTS | options))


def pair(h="1000", l="10", hv="0.1", lv="0.1", hc="p", lc="p"):
    return f"d,1,H,{hc},{hv},{h}\nd,1,L,{lc},{lv},{l}\n"


class EvaluationTests(unittest.TestCase):
    def test_ratio_and_unit_metadata(self):
        result = evaluate(pair())
        self.assertEqual(result["pairs"][0]["resistance_ratio"], 100)
        self.assertEqual(result["counts"]["valid_pairs"], 1)
        self.assertEqual(result["method"]["normalized_units"]["resistance_ratio"], "dimensionless")

    def test_explicit_units_normalize_to_si(self):
        result = evaluate(pair("100", "1", "100", "1e2"), voltage_unit="mV", resistance_unit="kohm")
        observed = result["pairs"][0]
        self.assertEqual(observed["common_read_voltage_V"], 0.1)
        self.assertEqual(observed["hrs"]["resistance_ohm"], 100000)
        self.assertEqual(observed["resistance_ratio"], 100)

    def test_microvolt_and_megohm_units(self):
        result = evaluate(pair("1", ".1", "100000", "100000"), voltage_unit="uV", resistance_unit="Mohm")
        self.assertEqual(result["pairs"][0]["common_read_voltage_V"], .1)
        self.assertEqual(result["pairs"][0]["resistance_ratio"], 10)

    def test_custom_labels_are_not_inferred(self):
        rows = pair().replace(",H,", ",program_A,").replace(",L,", ",program_B,")
        result = evaluate(rows, hrs_label="program_A", lrs_label="program_B")
        self.assertEqual(result["counts"]["valid_pairs"], 1)
        with self.assertRaises(rw.InputError):
            evaluate(rows)

    def test_below_one_ratio_preserved_not_relabelled(self):
        result = evaluate(pair("1", "10"))
        self.assertEqual(result["pairs"][0]["resistance_ratio"], .1)
        self.assertTrue(result["pairs"][0]["ratio_below_one"])
        self.assertEqual(result["counts"]["ratios_below_one_pairs"], 1)

    def test_duplicate_observation_rejected_even_if_invalid(self):
        with self.assertRaisesRegex(rw.InputError, "duplicate"):
            evaluate(pair() + "d,1,H,p,0.1,NaN\n")

    def test_missing_lrs_and_missing_hrs_count_groups(self):
        result = evaluate("d,1,H,p,.1,1\nd,2,L,p,.1,1\n")
        self.assertEqual(result["counts"]["missing_pair_groups"], 2)
        self.assertEqual(result["counts"]["missing_hrs_groups"], 1)
        self.assertEqual(result["counts"]["missing_lrs_groups"], 1)
        self.assertEqual(result["counts"]["complete_pairs"], 0)
        self.assertEqual(result["per_device"][0]["resistance_ratio_summary"]["count"], 0)

    def test_nonfinite_zero_negative_and_missing_resistances(self):
        for raw in ("NaN", "Infinity", "-inf", "0", "-0", "-1", "", "bad", "1e9999", "1e-9999"):
            with self.subTest(raw=raw):
                result = evaluate(pair(h=raw))
                self.assertEqual(result["counts"]["invalid_resistance_rows"], 1)
                self.assertIsNone(result["pairs"][0]["resistance_ratio"])
                self.assertEqual(result["counts"]["valid_pairs"], 0)
                json.dumps(result, allow_nan=False)

    def test_invalid_resistance_counts_rows_not_pairs(self):
        result = evaluate(pair("0", "-1"))
        self.assertEqual(result["counts"]["invalid_resistance_rows"], 2)
        self.assertEqual(result["counts"]["excluded_groups"], 1)

    def test_invalid_voltage_excludes_pair(self):
        for raw in ("", "NaN", "inf", "bad", "1e9999", "1e-9999"):
            with self.subTest(raw=raw):
                result = evaluate(pair(hv=raw))
                self.assertEqual(result["counts"]["invalid_read_voltage_rows"], 1)
                self.assertIsNone(result["pairs"][0]["resistance_ratio"])

    def test_zero_voltage_excluded(self):
        result = evaluate(pair(hv="0", lv="-0"))
        self.assertEqual(result["counts"]["zero_read_voltage_pairs"], 1)
        self.assertEqual(result["counts"]["invalid_read_voltage_rows"], 0)
        self.assertIsNone(result["pairs"][0]["common_read_voltage_V"])

    def test_signed_voltage_mismatch_not_magnitude_match(self):
        result = evaluate(pair(hv="0.1", lv="-0.1"))
        self.assertEqual(result["counts"]["voltage_mismatch_pairs"], 1)
        self.assertIsNone(result["pairs"][0]["resistance_ratio"])

    def test_equal_negative_voltage_allowed(self):
        result = evaluate(pair(hv="-.1", lv="-0.10"))
        self.assertEqual(result["counts"]["valid_pairs"], 1)
        self.assertEqual(result["pairs"][0]["common_read_voltage_V"], -.1)

    def test_decimal_voltage_equality_does_not_round_into_match(self):
        result = evaluate(pair(hv="0.10000000000000000000000001", lv="0.10000000000000000000000002"))
        self.assertEqual(result["counts"]["voltage_mismatch_pairs"], 1)
        self.assertEqual(result["counts"]["valid_pairs"], 0)

    def test_condition_mismatch_excludes_equal_voltage_pair(self):
        result = evaluate(pair(hc="ambient", lc="heated"))
        self.assertEqual(result["counts"]["read_condition_mismatch_pairs"], 1)
        self.assertIn("read_condition_mismatch", result["pairs"][0]["reasons"])
        self.assertIsNone(result["pairs"][0]["resistance_ratio"])

    def test_multiple_exclusion_reasons_count_independently(self):
        result = evaluate(pair(h="0", hv="0", lv=".1", hc="p", lc="q"))
        self.assertEqual(result["counts"]["invalid_resistance_rows"], 1)
        self.assertEqual(result["counts"]["zero_read_voltage_pairs"], 1)
        self.assertEqual(result["counts"]["voltage_mismatch_pairs"], 1)
        self.assertEqual(result["counts"]["read_condition_mismatch_pairs"], 1)
        self.assertEqual(result["counts"]["excluded_groups"], 1)

    def test_per_device_repeated_cycle_summary(self):
        rows = pair("100", "10") + pair("300", "10").replace("d,1,", "d,2,") + pair("500", "10").replace("d,1,", "e,1,")
        result = evaluate(rows)
        self.assertEqual(result["counts"]["devices"], 2)
        self.assertEqual(result["counts"]["valid_pairs"], 3)
        summary = result["per_device"][0]["resistance_ratio_summary"]
        self.assertEqual(summary, dict(count=2, minimum=10.0, median=20.0, maximum=30.0, arithmetic_mean=20.0))
        self.assertNotIn("p_value", result)
        self.assertNotIn("confidence_interval", summary)

    def test_conditions_are_separate_descriptive_strata(self):
        rows = pair() + pair(hc="other", lc="other").replace("d,1,", "d,2,") + pair(hv=".2", lv=".2").replace("d,1,", "d,3,")
        result = evaluate(rows)
        device = result["per_device"][0]
        self.assertTrue(device["mixed_read_conditions_or_voltages"])
        self.assertIsNone(device["resistance_ratio_summary"])
        self.assertEqual(len(device["by_read_condition_and_voltage"]), 3)

    def test_same_cycle_id_across_devices_not_duplicate(self):
        result = evaluate(pair() + pair().replace("d,1,", "e,1,"))
        self.assertEqual(result["counts"]["devices"], 2)

    def test_cycle_is_opaque_not_ordinal(self):
        result = evaluate(pair() + pair().replace("d,1,", "d,01,"))
        self.assertEqual(result["counts"]["device_cycle_groups"], 2)
        self.assertEqual({p["cycle"] for p in result["pairs"]}, {"1", "01"})

    def test_ratio_overflow_and_underflow_excluded(self):
        for h, l in (("1e308", "1e-308"), ("1e-308", "1e308")):
            with self.subTest(h=h, l=l):
                result = evaluate(pair(h, l))
                self.assertEqual(result["counts"]["unrepresentable_ratio_pairs"], 1)
                self.assertEqual(result["counts"]["invalid_resistance_rows"], 0)
                self.assertIsNone(result["pairs"][0]["resistance_ratio"])

    def test_normalization_overflow_is_invalid_resistance(self):
        result = evaluate(pair("1e308", "1"), resistance_unit="Mohm")
        self.assertEqual(result["counts"]["invalid_resistance_rows"], 1)

    def test_si_representable_voltage_with_large_input_is_accepted(self):
        result = evaluate(pair(hv="1e310", lv="1e310"), voltage_unit="uV")
        self.assertEqual(result["counts"]["valid_pairs"], 1)
        self.assertEqual(result["pairs"][0]["common_read_voltage_V"], 1e304)

    def test_summary_does_not_overflow_finite_values(self):
        rows = pair("1e308", "1") + pair("1e308", "1").replace("d,1,", "d,2,")
        summary = evaluate(rows)["per_device"][0]["resistance_ratio_summary"]
        self.assertEqual(summary["median"], 1e308)
        self.assertEqual(summary["arithmetic_mean"], 1e308)
        self.assertTrue(all(math.isfinite(v) for v in summary.values()))

    def test_empty_input_and_header_only_rejected(self):
        for content in ("", HEADER):
            with self.subTest(content=content):
                with self.assertRaises(rw.InputError):
                    rw.evaluate_stream(io.StringIO(content), **DEFAULTS)

    def test_strict_header_rejects_extra_missing_duplicate_columns(self):
        for header in (HEADER.rstrip() + ",secret\n", HEADER.replace("read_condition,", ""), HEADER.replace("state,", "device,")):
            with self.subTest(header=header):
                with self.assertRaises(rw.InputError):
                    rw.evaluate_stream(io.StringIO(header + pair()), **DEFAULTS)

    def test_header_order_can_change(self):
        content = "resistance,state,device,read_voltage,cycle,read_condition\n100,H,d,.1,1,p\n1,L,d,.1,1,p\n"
        result = rw.evaluate_stream(io.StringIO(content), **DEFAULTS)
        self.assertEqual(result["counts"]["valid_pairs"], 1)

    def test_short_long_blank_and_malformed_rows_rejected(self):
        for row in ("d,1,H,p,.1\n", "d,1,H,p,.1,10,extra\n", "\n", 'd,1,H,p,.1,"unclosed\n', 'd,1,H,p,.1,"10"junk\n'):
            with self.subTest(row=row):
                with self.assertRaises(rw.InputError):
                    evaluate(row)

    def test_blank_identifiers_and_conditions_rejected(self):
        for row in (",1,H,p,.1,10\n", "d,,H,p,.1,10\n", "d,1,,p,.1,10\n", "d,1,H,,.1,10\n"):
            with self.subTest(row=row):
                with self.assertRaises(rw.InputError):
                    evaluate(row)

    def test_nul_and_embedded_control_identifiers_rejected(self):
        for row in ('"d\nx",1,H,p,.1,10\n', "d\x00,1,H,p,.1,10\n"):
            with self.subTest(row=row):
                with self.assertRaises(rw.InputError):
                    evaluate(row)

    def test_numeric_whitespace_and_quoted_identifiers(self):
        result = evaluate('"device,one",1,H,p, .1 , 100\n"device,one",1,L,p,.1,10\n')
        self.assertEqual(result["pairs"][0]["device"], "device,one")
        self.assertEqual(result["counts"]["valid_pairs"], 1)

    def test_surrounding_identifier_whitespace_is_rejected(self):
        for column, raw in ((0, " d"), (1, "1 "), (2, " H"), (3, "p ")):
            with self.subTest(column=column):
                cells = ["d", "1", "H", "p", ".1", "10"]
                cells[column] = raw
                with self.assertRaisesRegex(rw.InputError, "surrounding whitespace"):
                    evaluate(",".join(cells) + "\n")

    def test_surrounding_label_whitespace_rejected(self):
        with self.assertRaisesRegex(rw.InputError, "surrounding whitespace"):
            evaluate(pair(), hrs_label=" H")

    def test_exact_condition_ids_never_merge_after_whitespace(self):
        with self.assertRaises(rw.InputError):
            evaluate(pair(hc="p", lc="p "))

    def test_unsupported_units_and_equal_empty_labels_rejected(self):
        for options in ({"voltage_unit": "kV"}, {"resistance_unit": "OHM"}, {"hrs_label": "L"}, {"hrs_label": " "}):
            with self.subTest(options=options):
                with self.assertRaises(rw.InputError):
                    evaluate(pair(), **options)

    def test_non_decimal_notation_is_not_silently_accepted(self):
        for raw in ("1_000", "0x10", "١٠", "1,000"):
            with self.subTest(raw=raw):
                quoted = '"' + raw + '"'
                result = evaluate(pair(h=quoted))
                self.assertEqual(result["counts"]["invalid_resistance_rows"], 1)

    def test_synthetic_fixture_expected_counts(self):
        result = rw.evaluate_csv(ROOT / "fixtures" / "synthetic.csv", voltage_unit="V", resistance_unit="ohm", hrs_label="HRS", lrs_label="LRS")
        expected = dict(rows=13, devices=2, device_cycle_groups=7, complete_pairs=6, missing_pair_groups=1,
                        missing_hrs_groups=0, missing_lrs_groups=1, invalid_resistance_rows=1,
                        invalid_read_voltage_rows=0, zero_read_voltage_pairs=0, voltage_mismatch_pairs=1,
                        read_condition_mismatch_pairs=0, unrepresentable_ratio_pairs=0, valid_pairs=4,
                        excluded_groups=3, ratios_below_one_pairs=1)
        self.assertEqual(result["counts"], expected)


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.input = Path(self.temp.name) / "private-device-name.csv"
        self.input.write_text(HEADER + pair(), encoding="utf-8")
        self.base = [sys.executable, str(SCRIPT), str(self.input), "--voltage-unit", "V", "--resistance-unit", "ohm", "--hrs-label", "H", "--lrs-label", "L"]

    def run_cli(self, *args):
        return subprocess.run(self.base + list(args), text=True, capture_output=True, check=False)

    def test_explicit_stdout_json(self):
        result = self.run_cli("--stdout")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["counts"]["valid_pairs"], 1)
        self.assertEqual(result.stderr, "")

    def test_no_default_output_leak(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn(str(self.input), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_output_and_stdout_mutually_exclusive(self):
        result = self.run_cli("--stdout", "--output", str(Path(self.temp.name) / "report.json"))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_exclusive_new_file_output(self):
        output = Path(self.temp.name) / "report.json"
        result = self.run_cli("--output", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(output.read_text())["counts"]["valid_pairs"], 1)

    def test_existing_output_not_overwritten(self):
        output = Path(self.temp.name) / "private-output.json"
        output.write_text("KEEP", encoding="utf-8")
        result = self.run_cli("--output", str(output))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output.read_text(), "KEEP")
        self.assertNotIn(str(output), result.stderr)

    def test_input_cannot_be_overwritten_by_output(self):
        original = self.input.read_bytes()
        result = self.run_cli("--output", str(self.input))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.input.read_bytes(), original)

    def test_unknown_state_error_does_not_echo_raw_value(self):
        self.input.write_text(HEADER + pair().replace(",H,", ",PRIVATE_STATE_TOKEN,"), encoding="utf-8")
        result = self.run_cli("--stdout")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("PRIVATE_STATE_TOKEN", result.stderr)
        self.assertNotIn(str(self.input), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_malformed_csv_does_not_create_output(self):
        self.input.write_text(HEADER + '"unterminated', encoding="utf-8")
        output = Path(self.temp.name) / "new.json"
        result = self.run_cli("--output", str(output))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())

    def test_missing_input_error_redacts_path(self):
        self.input.unlink()
        result = self.run_cli("--stdout")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("private-device-name", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_encoding_error_is_clean(self):
        self.input.write_bytes(b"\xff\xfe\x00")
        result = self.run_cli("--stdout")
        self.assertEqual(result.returncode, 2)
        self.assertIn("UTF-8", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_utf8_bom_is_supported(self):
        self.input.write_text(HEADER + pair(), encoding="utf-8-sig")
        result = self.run_cli("--stdout")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_units_and_labels_are_mandatory(self):
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.input), "--stdout"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_help(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--help"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--voltage-unit", result.stdout)
        self.assertIn("read_condition", result.stdout)
        self.assertIn("--stdout", result.stdout)

    def test_excluded_groups_produce_json_and_exit_one(self):
        self.input.write_text(HEADER + pair(h="0"), encoding="utf-8")
        result = self.run_cli("--stdout")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["counts"]["excluded_groups"], 1)
        self.assertEqual(result.stderr, "")

    @unittest.skipUnless(os.name == "posix", "POSIX file permission semantics")
    def test_output_permissions_owner_only_despite_permissive_umask(self):
        output = Path(self.temp.name) / "private.json"
        old_umask = os.umask(0)
        try:
            result = self.run_cli("--output", str(output))
        finally:
            os.umask(old_umask)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink semantics")
    def test_output_symlink_is_not_followed(self):
        target = Path(self.temp.name) / "uncreated-target.json"
        link = Path(self.temp.name) / "link.json"
        link.symlink_to(target)
        result = self.run_cli("--output", str(link))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(target.exists())
        self.assertTrue(link.is_symlink())


if __name__ == "__main__":
    unittest.main()
