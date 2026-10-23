#!/usr/bin/env python3
"""Print one original invented I-V test fixture, not measurements or a device model."""
import argparse
import csv
import io
import sys

COLUMNS = "lot,wafer,device,condition,cycle,replicate,sample_id,branch_to_next,voltage,current".split(",")
CASES = {'01_triangle': {'device': 'invented_01_triangle',
                 'rows': [['p001', 'up', '0', '0'],
                          ['p002', 'up', '1', '3'],
                          ['p003', 'down', '2', '4'],
                          ['p004', 'down', '1', '1'],
                          ['p005', '', '0', '0']]},
 '02_reverse': {'device': 'invented_02_reverse',
                'rows': [['p001', 'reversed_down', '0', '0'],
                         ['p002', 'reversed_down', '1', '1'],
                         ['p003', 'reversed_up', '2', '4'],
                         ['p004', 'reversed_up', '1', '3'],
                         ['p005', '', '0', '0']]},
 '03_current_offset': {'device': 'invented_03_current_offset',
                       'rows': [['p001', 'up', '0', '7'],
                                ['p002', 'up', '1', '10'],
                                ['p003', 'down', '2', '11'],
                                ['p004', 'down', '1', '8'],
                                ['p005', '', '0', '7']]},
 '04_voltage_only_closure': {'device': 'invented_04_voltage_only_closure',
                             'rows': [['p001', 'up', '0', '1'],
                                      ['p002', 'down', '1', '3'],
                                      ['p003', '', '0', '5']]},
 '05_plateau': {'device': 'invented_05_plateau',
                'rows': [['p001', 'up', '0', '0'],
                         ['p002', 'hold', '1', '2'],
                         ['p003', 'hold', '1', '3'],
                         ['p004', 'down', '1', '4'],
                         ['p005', '', '0', '0']]},
 '06_bowtie': {'device': 'invented_06_bowtie',
               'rows': [['p001', 'up_a', '0', '0'],
                        ['p002', 'down_a', '2', '2'],
                        ['p003', 'up_b', '0', '2'],
                        ['p004', 'down_b', '2', '0'],
                        ['p005', '', '0', '0']]},
 '07_open_voltage': {'device': 'invented_07_open_voltage',
                     'rows': [['p001', 'up', '0', '0'],
                              ['p002', 'down', '1', '2'],
                              ['p003', '', '0.1', '0']]},
 '08_float_alias_endpoints': {'device': 'invented_08_float_alias_endpoints',
                              'rows': [['p001', 'up', '1', '1'],
                                       ['p002', 'down', '2', '3'],
                                       ['p003', '', '1.0000000000000000000000000000001', '1']]},
 '09_reversing_declared_branch': {'device': 'invented_09_reversing_declared_branch',
                                  'rows': [['p001', 'all', '0', '0'],
                                           ['p002', 'all', '1', '3'],
                                           ['p003', 'all', '2', '4'],
                                           ['p004', 'all', '1', '1'],
                                           ['p005', '', '0', '0']]},
 '10_zero_voltage_span': {'device': 'invented_10_zero_voltage_span',
                          'rows': [['p001', 'hold', '1', '0'],
                                   ['p002', 'hold', '1', '2'],
                                   ['p003', '', '1', '3']]},
 '11_reused_branch_label': {'device': 'invented_11_reused_branch_label',
                            'rows': [['p001', 'up', '0', '0'],
                                     ['p002', 'up', '1', '3'],
                                     ['p003', 'down', '2', '4'],
                                     ['p004', 'up', '1', '1'],
                                     ['p005', '', '0', '0']]},
 '12_zero_current': {'device': 'invented_12_zero_current',
                     'rows': [['p001', 'up', '0', '0'], ['p002', 'down', '1', '0'], ['p003', '', '0', '0']]},
 '13_tiny_exact_path': {'device': 'invented_13_tiny_exact_path',
                        'rows': [['p001', 'up', '1e-150', '1e-150'],
                                 ['p002',
                                  'down',
                                  '10000000000000000000000000000001e-181',
                                  '10000000000000000000000000000001e-181'],
                                 ['p003', '', '1e-150', '10000000000000000000000000000002e-181']]},
 '14_total_underflow': {'device': 'invented_14_total_underflow',
                        'rows': [['p001', 'up', '0', '1e-150'],
                                 ['p002', 'down', '1e-150', '1e-150'],
                                 ['p003', '', '0', '10000000000000000000000000000001e-181']]},
 '15_branch_underflow': {'device': 'invented_15_branch_underflow',
                         'rows': [['p001', 'tiny_up', '0', '1e-150'],
                                  ['p002', 'large_up', '1e-150', '-10000000000000000000000000000001e-181'],
                                  ['p003', 'down', '1', '1'],
                                  ['p004', '', '0', '2']]},
 '16_large_exact_cancellation': {'device': 'invented_16_large_exact_cancellation',
                                 'rows': [['p001', 'up', '0', '1e150'],
                                          ['p002', 'down', '1e150', '1e150'],
                                          ['p003', '', '0', '1e150']]},
 '17_negative_current': {'device': 'invented_17_negative_current',
                         'rows': [['p001', 'down', '0', '0'],
                                  ['p002', 'down', '-1', '-3'],
                                  ['p003', 'up', '-2', '-4'],
                                  ['p004', 'up', '-1', '-1'],
                                  ['p005', '', '0', '0']]},
 '18_missing_nonfinal_branch': {'device': 'invented_bad',
                                'rows': [['p001', '', '0', '0'],
                                         ['p002', 'down', '1', '1'],
                                         ['p003', '', '0', '0']]},
 '19_nonempty_final_branch': {'device': 'invented_bad',
                              'rows': [['p001', 'up', '0', '0'],
                                       ['p002', 'down', '1', '1'],
                                       ['p003', 'invented_no_actual_edge', '0', '0']]},
 '20_duplicate_sample_id': {'device': 'invented_bad',
                            'rows': [['p001', 'up', '0', '0'],
                                     ['p002', 'down', '1', '1'],
                                     ['p001', '', '0', '0']]}}


def generate(name):
    case = CASES[name]
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(COLUMNS)
    prefix = ["invented_lot", "invented_wafer", case["device"], "invented_protocol", "001", "acquisition_1"]
    for row in case["rows"]:
        writer.writerow(prefix + row)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("fixture", choices=CASES)
    args = parser.parse_args()
    sys.stdout.write(generate(args.fixture))


if __name__ == "__main__":
    main()
