use crate::common::*;
use std::collections::{BTreeMap, HashSet};
const GROUP: &[&str] = &["lot", "wafer", "device", "condition", "sweep", "branch"];
const REQUIRED: &[&str] = &[
    "lot",
    "wafer",
    "device",
    "condition",
    "sweep",
    "branch",
    "sample_id",
    "voltage",
];
#[derive(Clone)]
struct Sample {
    row: usize,
    id: String,
    text: String,
    value: Rat,
}
struct Group {
    key: Vec<String>,
    samples: Vec<Sample>,
    ids: HashSet<String>,
    coordinates: IndexMap<Rat, Vec<usize>>,
}
struct Comparison {
    key: Vec<String>,
    union: BTreeMap<Rat, Sample>,
    groups: Vec<usize>,
}
pub fn run(a: &Args) -> Res<Value> {
    let unit = a.choice("voltage-unit", &["V", "mV", "uV", "nV"])?;
    let raw = read_regular(&a.input, MAX_BYTES)?;
    let records = csv_records_limited(&raw, true, Some(65536), 64, 10001, false, Some(REQUIRED))?;
    let first = records.first().ok_or("CSV header missing")?;
    let pattern = regex::Regex::new(r"^[A-Za-z][A-Za-z0-9_]{0,63}$").unwrap();
    let header: Vec<String> = first
        .fields
        .iter()
        .map(|b| {
            let s = std::str::from_utf8(b).map_err(|_| "invalid CSV header")?;
            if !pattern.is_match(s) {
                return err("invalid CSV header name");
            };
            Ok(s.to_string())
        })
        .collect::<Res<_>>()?;
    if header.len() > 64
        || header.iter().collect::<HashSet<_>>().len() != header.len()
        || !REQUIRED.iter().all(|x| header.iter().any(|h| h == x))
    {
        return err("missing or duplicate required CSV header");
    };
    let selected: Vec<usize> = REQUIRED
        .iter()
        .map(|n| header.iter().position(|h| h == n).unwrap())
        .collect();
    let mut groups: IndexMap<Vec<String>, Group> = IndexMap::new();
    let rows = records.len() - 1;
    if rows == 0 || rows > 10000 {
        return err("CSV requires bounded nonempty records");
    }
    for (ordinal, row) in records.iter().skip(1).enumerate() {
        if row.fields.len() != header.len() {
            return err("CSV row width mismatch; blank records not permitted");
        };
        let mut vals = vec![];
        for &i in &selected {
            vals.push(
                std::str::from_utf8(&row.fields[i]).map_err(|_| "invalid selected-column UTF-8")?,
            );
        }
        let key: Vec<String> = vals[..6]
            .iter()
            .map(|v| identifier(v, 128, true))
            .collect::<Res<_>>()?;
        let id = identifier(vals[6], 128, true)?;
        if !vals[7].is_ascii() {
            return err("voltage must be an ASCII finite decimal");
        }
        let value = coordinate(vals[7])?;
        let group = groups.entry(key.clone()).or_insert_with(|| Group {
            key,
            samples: vec![],
            ids: HashSet::new(),
            coordinates: IndexMap::new(),
        });
        if !group.ids.insert(id.clone()) {
            return err("duplicate sample ID within declared sweep group");
        };
        group
            .coordinates
            .entry(value.clone())
            .or_default()
            .push(ordinal + 1);
        group.samples.push(Sample {
            row: ordinal + 1,
            id,
            text: vals[7].to_string(),
            value,
        });
        if groups.len() > 1000 {
            return err("declared sweep-group limit exceeded");
        }
    }
    let mut comparisons: IndexMap<Vec<String>, Comparison> = IndexMap::new();
    for (i, g) in groups.values().enumerate() {
        let k = vec![g.key[3].clone(), g.key[5].clone()];
        let c = comparisons.entry(k.clone()).or_insert_with(|| Comparison {
            key: k,
            union: BTreeMap::new(),
            groups: vec![],
        });
        c.groups.push(i);
        for s in &g.samples {
            let first = c.union.entry(s.value.clone()).or_insert_with(|| s.clone());
            if s.row < first.row {
                *first = s.clone();
            }
        }
    }
    if comparisons
        .values()
        .map(|c| c.union.len() * c.groups.len())
        .sum::<usize>()
        > 100000
    {
        return err("observed-union grid-cell limit exceeded");
    }
    let mut group_reports = vec![Value::Null; groups.len()];
    let mut comparison_reports = vec![];
    let mut unequal_count = 0;
    let mut duplicate_groups = 0;
    for (ci, c) in comparisons.values().enumerate() {
        let index: BTreeMap<Rat, usize> = c
            .union
            .keys()
            .enumerate()
            .map(|(i, v)| (v.clone(), i + 1))
            .collect();
        let mut variants: IndexMap<Vec<usize>, Vec<usize>> = IndexMap::new();
        for &gi in &c.groups {
            let g = &groups[gi];
            let mut grid: Vec<usize> = g.coordinates.keys().map(|v| index[v]).collect();
            grid.sort();
            variants.entry(grid.clone()).or_default().push(gi + 1);
            let duplicates: Vec<Value> = g
                .coordinates
                .iter()
                .filter(|(_, rows)| rows.len() > 1)
                .map(|(v, rows)| json!({"coordinate_index":index[v],"source_row_ordinals":rows}))
                .collect();
            if !duplicates.is_empty() {
                duplicate_groups += 1
            }
            group_reports[gi] = json!({"group_index":gi+1,"identity":identity(GROUP,&g.key),"comparison_index":ci+1,"samples":g.samples.iter().map(|s|json!({"source_row_ordinal":s.row,"sample_id":s.id,"voltage_text":s.text,"coordinate_index":index[&s.value]})).collect::<Vec<_>>(),"unique_coordinate_indices":grid,"duplicate_coordinates":duplicates,"missing_from_observed_union_indices":c.union.keys().filter(|v|!g.coordinates.contains_key(*v)).map(|v|index[v]).collect::<Vec<_>>()});
        }
        let unequal = variants.len() > 1;
        if unequal {
            unequal_count += 1
        }
        comparison_reports.push(json!({"comparison_index":ci+1,"condition":c.key[0],"branch":c.key[1],"group_indices":c.groups.iter().map(|i|i+1).collect::<Vec<_>>(),"observed_union":c.union.iter().map(|(v,s)|json!({"coordinate_index":index[v],"first_observed_voltage_text":s.text})).collect::<Vec<_>>(),"grid_variants":variants.iter().map(|(grid,groups)|json!({"coordinate_indices":grid,"group_indices":groups})).collect::<Vec<_>>(),"unequal_grids":unequal}));
    }
    Ok(
        json!({"schema_version":"iv-grid-audit-v1","status":if duplicate_groups>0||unequal_count>0{"GRID_FINDINGS"}else{"NO_GRID_FINDINGS"},"provenance":{"csv_sha256":sha256(&raw),"csv_bytes":raw.len(),"row_ordinal_convention":"one-based logical data record, excluding header"},"selection":{"decoded_columns":REQUIRED,"opaque_columns":header.iter().filter(|h|!REQUIRED.contains(&h.as_str())).collect::<Vec<_>>(),"response_values_inspected":false},"parameters":{"voltage_unit_declared":unit,"unit_conversion":"none","comparison":"exact_decimal_set","comparison_columns":["condition","branch"]},"counts":{"input_rows":rows,"declared_sweep_groups":groups.len(),"comparison_sets":comparisons.len(),"groups_with_duplicate_coordinates":duplicate_groups,"comparison_sets_with_unequal_grids":unequal_count},"groups":group_reports,"comparisons":comparison_reports,"limitations":["Only voltage and required identities are decoded; other values are scanned for CSV framing only.","Condition and branch are exact global comparison labels, not verified common physical protocols.","A declared sweep group is not a verified physical DUT, acquisition or independent observation.","Missing means absent from the observed union, not from a planned or physically required grid.","A coordinate missing from every supplied group and a wholly absent group cannot be detected.","Set equality ignores order and does not validate chronology, direction, hysteresis or interpolation.","Duplicate coordinates may be intentional repeated observations; none are dropped or averaged.","Units, measurement quality, response/target eligibility and fitness for modeling are not verified."]}),
    )
}
