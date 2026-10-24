use crate::common::*;
use std::collections::HashSet;
const GROUP: &[&str] = &["lot", "wafer", "device", "condition", "sequence"];
const COLUMNS: &[&str] = &[
    "lot",
    "wafer",
    "device",
    "condition",
    "sequence",
    "sample_id",
    "coordinate",
];
pub fn run(a: &Args) -> Res<Value> {
    let axis = a.choice("axis", &["voltage", "magnetic-flux-density"])?;
    let units = if axis == "voltage" {
        vec!["V", "mV", "uV", "nV"]
    } else {
        vec!["T", "mT", "uT", "G", "kG"]
    };
    let unit = a.choice("coordinate-unit", &units)?;
    let (rows, digest) = load_rows(&a.input, COLUMNS, false)?;
    let mut groups: IndexMap<Vec<String>, Vec<(Row, Rat)>> = IndexMap::new();
    let mut previous = None;
    let mut ids = HashSet::new();
    for row in &rows {
        let key = group_key(row, GROUP)?;
        if previous.as_ref() != Some(&key) {
            if groups.contains_key(&key) {
                return err("a declared sequence must occupy one contiguous block of CSV records");
            };
            previous = Some(key.clone());
            ids.clear();
        }
        let id = identifier(row.get("sample_id"), 128, false)?;
        if !ids.insert(id) {
            return err("sample IDs must be unique within each declared sequence");
        };
        let value = coordinate(row.get("coordinate"))?;
        groups.entry(key).or_default().push((row.clone(), value));
        if groups.len() > 1000 {
            return err("sequence limit exceeded");
        }
    }
    let mut results = vec![];
    let mut total_segments = 0;
    let mut total_flat = 0;
    let mut total_shared = 0;
    let mut total_reversals = 0;
    let mut singletons = 0;
    for (key, points) in &groups {
        let mut samples:Vec<Value>=points.iter().enumerate().map(|(i,(r,_))|json!({"sample_id":r.get("sample_id"),"source_record_index":r.ordinal,"source_line_start":r.line_start,"source_line_end":r.line_end,"coordinate_text":r.get("coordinate"),"sample_index":i+1,"segment_ids":[]})).collect();
        let mut segments: Vec<Value> = vec![];
        for (i, w) in points.windows(2).enumerate() {
            let direction = if w[1].1 > w[0].1 {
                "increasing"
            } else if w[1].1 < w[0].1 {
                "decreasing"
            } else {
                "flat"
            };
            if segments.last().is_none_or(|s| s["direction"] != direction) {
                segments.push(json!({"segment_id":segments.len()+1,"direction":direction,"edge_start":i+1,"edge_end":i+1}));
            } else {
                segments.last_mut().unwrap()["edge_end"] = json!(i + 1);
            }
        }
        for segment in &mut segments {
            let start = segment["edge_start"].as_u64().unwrap() as usize;
            let end = segment["edge_end"].as_u64().unwrap() as usize;
            segment["edge_count"] = json!(end - start + 1);
            segment["sample_indices"] = json!((start..=end + 1).collect::<Vec<_>>());
            for i in start..=end + 1 {
                samples[i - 1]["segment_ids"]
                    .as_array_mut()
                    .unwrap()
                    .push(segment["segment_id"].clone());
            }
            if segment["direction"] == "flat" {
                total_flat += end - start + 1;
            }
        }
        let shared:Vec<Value>=samples.iter().filter(|s|s["segment_ids"].as_array().unwrap().len()>1).map(|s|json!({"sample_index":s["sample_index"],"sample_id":s["sample_id"],"segment_ids":s["segment_ids"]})).collect();
        let mut reversals = vec![];
        let mut prev: Option<&Value> = None;
        for s in &segments {
            if s["direction"] == "flat" {
                continue;
            }
            if let Some(p) = prev {
                if p["direction"] != s["direction"] {
                    let start = p["edge_end"].as_u64().unwrap() as usize + 1;
                    let end = s["edge_start"].as_u64().unwrap() as usize;
                    let indices: Vec<usize> = (start..=end).collect();
                    reversals.push(json!({"kind":if start==end{"direct"}else{"across_plateau"},"from_segment_id":p["segment_id"],"to_segment_id":s["segment_id"],"turnaround_sample_indices":indices,"turnaround_sample_ids":indices.iter().map(|i|samples[i-1]["sample_id"].clone()).collect::<Vec<_>>()}));
                }
            }
            prev = Some(s);
        }
        let mut warnings = vec![
            "supplied_order_is_not_verified_chronology",
            "segments_are_not_independent_observations_or_cycles",
        ];
        if !shared.is_empty() {
            warnings.push("adjacent_segments_share_endpoint_observations")
        }
        if segments.iter().any(|s| s["direction"] == "flat") {
            warnings.push("equal_coordinate_observations_retained_as_flat_edges")
        }
        if points.len() == 1 {
            warnings.push("singleton_has_no_edges_or_segments");
            singletons += 1;
        }
        total_segments += segments.len();
        total_shared += shared.len();
        total_reversals += reversals.len();
        let mut result = identity(GROUP, key);
        for(k,v)in json!({"samples":samples,"segments":segments,"shared_endpoints":shared,"coordinate_reversals":reversals,"unsegmented_sample_indices":if points.len()==1{vec![1]}else{vec![]},"warnings":warnings}).as_object().unwrap(){result[k]=v.clone();}
        results.push(result);
    }
    Ok(
        json!({"schema_version":1,"tool":"sweep-segments","version":"0.1.0","axis":axis,"coordinate_unit":unit,"provenance":{"input_sha256":digest,"group_columns":GROUP,"source_record_convention":"one-based data record index, excluding header","source_line_convention":"one-based physical CSV lines, including header","sample_index_convention":"one-based index within a declared sequence","edge_index_convention":"edge i connects sample i to sample i+1 within a declared sequence"},"parameters":{"comparison":"exact_decimal","plateau_rule":"separate_flat_edge_runs","unit_conversion":"none; coordinate_text retains original declared-unit token","order":"supplied_contiguous_records","noise_tolerance":"none"},"counts":{"input_samples":rows.len(),"input_sequences":groups.len(),"edges":rows.len()-groups.len(),"segments":total_segments,"flat_edges":total_flat,"shared_endpoint_samples":total_shared,"coordinate_reversals":total_reversals,"singleton_sequences":singletons},"assumptions_and_limits":constants("sweep-segments","LIMITS"),"results":results}),
    )
}
