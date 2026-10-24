use crate::common::*;
use serde::{
    de::{self, MapAccess, SeqAccess, Visitor},
    Deserialize, Deserializer,
};
use std::collections::{BTreeSet, HashSet};
const HEADER: &[&str] = &[
    "experiment_key",
    "lot_number",
    "wafer_number",
    "loc_id",
    "X",
    "Y",
    "preox_thickness",
    "postox_thickness",
    "stepheight",
    "oxide_etch",
    "si_etch",
];
const FIELDS: &[&str] = &["experiment_key", "lot_number", "wafer_number", "loc_id"];
struct Unique(Value);
impl<'de> Deserialize<'de> for Unique {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Unique;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("JSON with unique object keys")
            }
            fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> Result<Unique, M::Error> {
                let mut out = serde_json::Map::new();
                while let Some((k, v)) = map.next_entry::<String, Unique>()? {
                    if out.contains_key(&k) {
                        return Err(de::Error::custom("duplicate JSON key"));
                    }
                    out.insert(k, v.0);
                }
                Ok(Unique(Value::Object(out)))
            }
            fn visit_seq<S: SeqAccess<'de>>(self, mut seq: S) -> Result<Unique, S::Error> {
                let mut out = vec![];
                while let Some(v) = seq.next_element::<Unique>()? {
                    out.push(v.0)
                }
                Ok(Unique(Value::Array(out)))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Unique, E> {
                Ok(Unique(json!(v)))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Unique, E> {
                if !v.is_finite() {
                    return Err(de::Error::custom("nonfinite JSON number"));
                }
                Ok(Unique(json!(v)))
            }
            fn visit_unit<E: de::Error>(self) -> Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_none<E: de::Error>(self) -> Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
        }
        d.deserialize_any(V)
    }
}
fn text(raw: &[u8]) -> Res<Option<String>> {
    if raw.len() > 256 {
        return err("identity byte limit exceeded");
    };
    let s = std::str::from_utf8(raw).map_err(|_| "invalid identity UTF-8")?;
    if s.chars().any(char::is_control) {
        return err("control character in identity");
    };
    Ok(if s.is_empty() { None } else { Some(s.into()) })
}
fn named_date(c: &regex::Captures) -> bool {
    let y: u32 = c[1].parse().unwrap();
    let m: u32 = c[2].parse().unwrap();
    let d: u32 = c[3].parse().unwrap();
    let days = match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if y % 4 == 0 && (y % 100 != 0 || y % 400 == 0) {
                29
            } else {
                28
            }
        }
        _ => 0,
    };
    y > 0 && d > 0 && d <= days && &c[4] != "00"
}
fn exact_keys(v: &Value, keys: &[&str]) -> bool {
    v.as_object()
        .is_some_and(|o| o.len() == keys.len() && keys.iter().all(|k| o.contains_key(*k)))
}
fn labels(v: &Value, lengths: &[usize]) -> Res<Vec<String>> {
    let list = v.as_array().ok_or("labels must be an array")?;
    if !lengths.contains(&list.len()) {
        return err("label array width invalid");
    };
    let mut out = vec![];
    for v in list {
        let s = v.as_str().ok_or("label must be text")?;
        if text(s.as_bytes())?.is_none() {
            return err("empty label");
        };
        out.push(s.to_string())
    }
    if out.iter().collect::<HashSet<_>>().len() != out.len() {
        return err("duplicate label");
    };
    Ok(out)
}
fn metadata(blob: &[u8]) -> Res<Value> {
    let m = serde_json::from_slice::<Unique>(blob)
        .map_err(|_| "invalid or duplicate-key metadata JSON")?
        .0;
    if !exact_keys(
        &m,
        &[
            "schema_version",
            "process_file_sha256",
            "canonical_labels",
            "groups",
        ],
    ) {
        return err("metadata top-level schema mismatch");
    };
    if m["schema_version"] != "bosch-channel-metadata-v1" {
        return err("metadata schema version mismatch");
    };
    let digest = m["process_file_sha256"]
        .as_str()
        .ok_or("invalid declared process digest")?;
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return err("declared process-file digest syntax mismatch");
    };
    labels(&m["canonical_labels"], &[31])?;
    let groups = m["groups"]
        .as_array()
        .ok_or("metadata groups must be array")?;
    if groups.is_empty() || groups.len() > 128 {
        return err("metadata group-count limit");
    };
    let pattern =
        regex::Regex::new(r"^/Day_([0-9]{4})_([0-9]{2})_([0-9]{2})_Wafer_([0-9]{2})$").unwrap();
    let mut seen = HashSet::new();
    for g in groups {
        if !exact_keys(g, &["group_path", "feature_labels"]) {
            return err("metadata group schema mismatch");
        };
        let path = g["group_path"].as_str().ok_or("invalid BOSCH group name")?;
        let cap = pattern.captures(path).ok_or("invalid BOSCH group name")?;
        if !named_date(&cap) || !seen.insert(path) {
            return err("invalid or duplicate BOSCH group name");
        };
        labels(&g["feature_labels"], &[31, 44])?;
    }
    Ok(m)
}
fn alignment(m: &Value) -> Value {
    let groups = m["groups"].as_array().unwrap();
    let sets: Vec<BTreeSet<String>> = groups
        .iter()
        .map(|g| {
            g["feature_labels"]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().into())
                .collect()
        })
        .collect();
    let common: BTreeSet<String> = sets[0]
        .iter()
        .filter(|s| sets.iter().all(|set| set.contains(*s)))
        .cloned()
        .collect();
    let union: BTreeSet<String> = sets.iter().flatten().cloned().collect();
    let canonical = m["canonical_labels"].as_array().unwrap();
    let aligned:Vec<Value>=groups.iter().map(|g|{let labels=g["feature_labels"].as_array().unwrap();let position=|label:&Value|labels.iter().position(|v|v==label);json!({"group_path":g["group_path"],"feature_count":labels.len(),"feature_labels_in_source_order":labels,"canonical_name_column_map":canonical.iter().map(|l|json!({"label":l,"zero_based_column_index":position(l)})).collect::<Vec<_>>(),"missing_canonical_labels":canonical.iter().filter(|l|position(l).is_none()).collect::<Vec<_>>(),"noncanonical_labels_in_source_order":labels.iter().filter(|l|!canonical.contains(l)).collect::<Vec<_>>(),"common_name_column_map":common.iter().map(|l|json!({"label":l,"zero_based_column_index":position(&json!(l))})).collect::<Vec<_>>(),"noncommon_labels_in_source_order":labels.iter().filter(|l|!common.contains(l.as_str().unwrap())).collect::<Vec<_>>()})}).collect();
    json!({"ordering":"Unicode codepoint lexical order; exact names, no normalization","canonical_labels_in_declared_order":canonical,"common_labels":common,"union_labels":union,"common_label_count":common.len(),"union_label_count":union.len(),"groups":aligned,"sensor_values_read":false,"physical_units_or_channel_roles_verified":false,"automatic_feature_selection":false})
}
fn inventory(rows: &[Value], m: &Value) -> Value {
    let mut duplicates: IndexMap<Vec<Option<String>>, Vec<usize>> = IndexMap::new();
    let mut wafer_keys: IndexMap<Vec<String>, Vec<&Value>> = IndexMap::new();
    let mut unresolved = vec![];
    for row in rows {
        let ordinal = row["logical_data_row_ordinal"].as_u64().unwrap() as usize;
        let key: Vec<Option<String>> = FIELDS
            .iter()
            .map(|n| row[*n].as_str().map(String::from))
            .collect();
        duplicates.entry(key.clone()).or_default().push(ordinal);
        if key[..3].iter().any(Option::is_none) {
            unresolved.push(ordinal)
        } else {
            wafer_keys
                .entry(key[..3].iter().map(|s| s.clone().unwrap()).collect())
                .or_default()
                .push(row);
        }
    }
    let process_paths: HashSet<&str> = m["groups"]
        .as_array()
        .unwrap()
        .iter()
        .map(|g| g["group_path"].as_str().unwrap())
        .collect();
    let experiment = regex::Regex::new(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})_([0-9]{2})$").unwrap();
    let mut results = vec![];
    let mut proposed: IndexMap<String, Vec<usize>> = IndexMap::new();
    for (key, records) in wafer_keys {
        let mut path = None;
        let status = if let Some(c) = experiment.captures(&key[0]) {
            if !named_date(&c) {
                "UNSUPPORTED_EXPERIMENT_KEY"
            } else if key[2] != c[4].parse::<u32>().unwrap().to_string() {
                "WAFER_NUMBER_TEXT_MISMATCH"
            } else {
                let p = format!("/Day_{}_{}_{}_Wafer_{}", &c[1], &c[2], &c[3], &c[4]);
                let status = if process_paths.contains(p.as_str()) {
                    "EXACT_NAME_CANDIDATE"
                } else {
                    "NO_EXACT_PROCESS_GROUP"
                };
                path = Some(p);
                status
            }
        } else {
            "UNSUPPORTED_EXPERIMENT_KEY"
        };
        let locations: Vec<Value> = records.iter().map(|r| r["loc_id"].clone()).collect();
        let mut counts: IndexMap<Option<String>, usize> = IndexMap::new();
        for l in &locations {
            *counts.entry(l.as_str().map(String::from)).or_default() += 1;
        }
        if status == "EXACT_NAME_CANDIDATE" {
            proposed
                .entry(path.clone().unwrap())
                .or_default()
                .push(results.len());
        }
        results.push(json!({"experiment_key":key[0],"lot_number":key[1],"wafer_number":key[2],"source_row_ordinals":records.iter().map(|r|r["logical_data_row_ordinal"].clone()).collect::<Vec<_>>(),"location_labels_in_row_order":locations,"distinct_nonnull_location_count":counts.keys().filter(|k|k.is_some()).count(),"null_location_count":counts.get(&None).copied().unwrap_or(0),"duplicate_nonnull_location_labels":counts.iter().filter(|(k,n)|k.is_some()&&**n>1).map(|(k,_)|k.clone().unwrap()).collect::<Vec<_>>(),"proposed_process_group":path,"name_join_status":status,"finite_target_eligibility_verified":false}));
    }
    for candidates in proposed.values() {
        if candidates.len() > 1 {
            for &i in candidates {
                results[i]["name_join_status"] = json!("AMBIGUOUS_MULTIPLE_IDENTITY_KEYS")
            }
        }
    }
    let null_counts: serde_json::Map<String, Value> = FIELDS
        .iter()
        .map(|n| {
            (
                n.to_string(),
                json!(rows.iter().filter(|r| r[*n].is_null()).count()),
            )
        })
        .collect();
    let dup: Vec<Value> = duplicates
        .iter()
        .filter(|(_, v)| v.len() > 1)
        .map(|(k, rows)| {
            let id: serde_json::Map<String, Value> = FIELDS
                .iter()
                .zip(k)
                .map(|(n, v)| (n.to_string(), json!(v)))
                .collect();
            json!({"identity":id,"source_row_ordinals":rows})
        })
        .collect();
    json!({"rows":rows,"row_count":rows.len(),"null_counts":null_counts,"exact_duplicate_four_field_tuples":dup,"incomplete_wafer_key_row_ordinals":unresolved,"complete_identity_keys":results,"complete_identity_key_count":results.len(),"process_groups_without_complete_identity_name_candidate":m["groups"].as_array().unwrap().iter().filter(|g|!proposed.contains_key(g["group_path"].as_str().unwrap())).map(|g|g["group_path"].clone()).collect::<Vec<_>>(),"rows_dropped_or_filled":0,"physical_wafer_identity_or_independence_verified":false})
}
pub fn run(a: &Args) -> Res<Value> {
    let csv = read_regular(&a.input, MAX_BYTES)?;
    let meta = read_regular(
        std::path::Path::new(a.req("channel-metadata")?),
        2 * 1024 * 1024,
    )?;
    let records = csv_records_bosch(&csv, FIELDS)?;
    if records.len() < 2 || records.len() > 2001 {
        return err("CSV needs a bounded set of nonempty records");
    };
    if records[0]
        .fields
        .iter()
        .map(Vec::as_slice)
        .collect::<Vec<_>>()
        != HEADER.iter().map(|s| s.as_bytes()).collect::<Vec<_>>()
    {
        return err("exact BOSCH v1 header mismatch");
    };
    let mut rows = vec![];
    for (i, r) in records.iter().skip(1).enumerate() {
        if r.fields.len() != 11 {
            return err("CSV width mismatch");
        };
        let mut row = json!({"logical_data_row_ordinal":i+1});
        for (j, name) in FIELDS.iter().enumerate() {
            row[*name] = json!(text(&r.fields[j])?);
        }
        rows.push(row);
    }
    let m = metadata(&meta)?;
    Ok(
        json!({"schema_version":"bosch-identity-audit-v1","status":"COMPLETE_METADATA_AUDIT_NOT_SCIENTIFIC_VALIDATION","dataset_reference":"https://doi.org/10.5281/zenodo.17122442","provenance":{"csv_sha256":sha256(&csv),"csv_bytes":csv.len(),"channel_metadata_sha256":sha256(&meta),"channel_metadata_bytes":meta.len(),"declared_process_file_sha256":m["process_file_sha256"],"declared_process_hash_verified_against_file":false},"identity_inventory":inventory(&rows,&m),"channel_alignment":alignment(&m),"limitations":["CSV bytes are read and hashed; only header and four identity columns are decoded.","Other CSV fields are traversed for quoting/delimiters, not decoded, validated or emitted.","Channel sidecar is a user declaration; native Process_data.nc is not opened.","Name candidates are not verified physical joins, reset episodes or independent samples.","Location membership does not establish finite targets, metrology quality or eligibility.","No rows, duplicate identities, locations or channels are automatically excluded.","No target/sensor values, feature summaries, fitted models or causal estimates are produced."]}),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reject_duplicate_json_keys() {
        assert!(serde_json::from_str::<Unique>(r#"{"x":1,"x":2}"#).is_err());
        assert!(serde_json::from_str::<Unique>(r#"{"x":{"a":1,"a":2}}"#).is_err());
    }
}
