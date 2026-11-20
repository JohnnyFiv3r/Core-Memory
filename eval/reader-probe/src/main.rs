//! Sends the engine's chunk reader the same LoCoMo turns under a few
//! settings, and says for each what the model chose and how each call
//! ended. The second live run (eval/locomo-smoke) typed every chunk, small
//! talk included, and four harmless questions were refused; this finds
//! out why. See README.md.
//!
//! The requests are the engine's own: its reader prompt and reply shape
//! (`read::render`), its Anthropic request body (`AnthropicTransport::body`)
//! and its HTTP client. Only the setting a variant names is changed.

// A probe's output is what it prints, for a person to read.
#![allow(clippy::print_stdout, clippy::print_stderr)]

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use core_memory_core::model::BindingConfig;
use core_memory_core::read::{self, ChunkInput, Quotes, TypeOption};
use core_memory_model_anthropic::{API_VERSION, AnthropicTransport};
use core_memory_model_http::{HttpClient, Roots};
use serde_json::{Value, json};

const ENDPOINT: &str = "https://api.anthropic.com";
const MODEL: &str = "claude-haiku-5-5";
const MAX_OUTPUT_TOKENS: u32 = 4096;
const KEY_ENV: &str = "CORE_MEMORY_ANTHROPIC_KEY";
/// Dollars per million tokens, input and output.
const PRICE: (f64, f64) = (0.10, 0.50);
const SAMPLE: &str = "conv-26";

/// The vocabulary of eval/locomo-smoke/core-memory.toml.
const TYPES: [(&str, &str); 5] = [
    (
        "life_event",
        "Something that happened to the speaker or someone they know.",
    ),
    (
        "plan",
        "Something someone intends, arranges or commits to do.",
    ),
    (
        "preference",
        "What someone likes, values, wants or believes.",
    ),
    (
        "fact",
        "A lasting fact about a person: who they are, their relationships, work, home or history.",
    ),
    ("feeling", "How someone feels about something."),
];

/// Messages that only keep the conversation going, where abstaining is
/// right. The second run typed them all and refused D8:3, D8:5, D9:5 and
/// D9:7.
const SMALL_TALK: [&str; 14] = [
    "D1:1", "D1:4", "D1:8", "D3:15", "D3:19", "D4:12", "D5:7", "D8:3", "D8:5", "D8:18", "D8:38",
    "D9:5", "D9:7", "D18:6",
];

/// Messages that state something one of the types describes.
const STATEMENTS: [&str; 10] = [
    "D1:3", "D1:16", "D2:8", "D3:16", "D4:3", "D5:10", "D7:16", "D7:22", "D14:33", "D17:8",
];

struct Variant {
    name: &'static str,
    effort: Option<&'static str>,
    per_call: usize,
    /// Whether the reply shape is enforced, as the engine does.
    enforced: bool,
}

const VARIANTS: [Variant; 4] = [
    Variant {
        name: "as the second run: effort low, 8 to a call",
        effort: Some("low"),
        per_call: 8,
        enforced: true,
    },
    Variant {
        name: "effort medium, 8 to a call",
        effort: Some("medium"),
        per_call: 8,
        enforced: true,
    },
    Variant {
        name: "effort low, 1 to a call",
        effort: Some("low"),
        per_call: 1,
        enforced: true,
    },
    Variant {
        name: "effort low, 8 to a call, reply shape not enforced",
        effort: Some("low"),
        per_call: 8,
        enforced: false,
    },
];

struct Turn {
    id: &'static str,
    small_talk: bool,
    text: String,
}

/// What became of one turn in one variant.
#[derive(Default)]
struct Tally {
    abstained_small_talk: usize,
    typed_statements: usize,
    calls: usize,
    refused: usize,
    cut_off: usize,
    invalid: usize,
    failed: usize,
    input_tokens: u64,
    output_tokens: u64,
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let dry_run = args.iter().any(|a| a == "--dry-run");
    let Some(data) = args.iter().find(|a| !a.starts_with("--")) else {
        eprintln!("usage: reader-probe <locomo10.json> [--dry-run]");
        return ExitCode::from(2);
    };
    match run(Path::new(data), dry_run) {
        Ok(()) => ExitCode::SUCCESS,
        Err(problem) => {
            eprintln!("reader-probe: {problem}");
            ExitCode::FAILURE
        }
    }
}

fn run(data: &Path, dry_run: bool) -> Result<(), String> {
    let turns = turns(data)?;
    let key = if dry_run {
        String::new()
    } else {
        std::env::var(KEY_ENV)
            .ok()
            .filter(|k| !k.trim().is_empty())
            .ok_or(format!("${KEY_ENV} must hold an Anthropic API key"))?
    };
    let roots = match std::env::var_os("MODELS_CA_FILE") {
        Some(path) => Some(Roots::with_file(&PathBuf::from(path))?),
        None => None,
    };
    let client = HttpClient::trusting(Duration::from_secs(120), roots.as_ref());
    let types: Vec<TypeOption<'_>> = TYPES
        .iter()
        .map(|(key, definition)| TypeOption {
            key,
            definition: Some(definition),
        })
        .collect();
    let allowed: BTreeSet<&str> = TYPES.iter().map(|(key, _)| *key).collect();

    let mut summary = Vec::new();
    for variant in &VARIANTS {
        println!("\n## {}\n", variant.name);
        let binding: BindingConfig = serde_json::from_value(json!({
            "id": "probe",
            "provider": "anthropic",
            "endpoint": ENDPOINT,
            "model": MODEL,
            "reasoning_effort": variant.effort,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "api_key_env": KEY_ENV,
        }))
        .map_err(|e| e.to_string())?;
        let mut tally = Tally::default();
        for batch in turns.chunks(variant.per_call) {
            let inputs: Vec<ChunkInput<'_>> = batch
                .iter()
                .map(|turn| ChunkInput {
                    text: &turn.text,
                    role: Some("user"),
                })
                .collect();
            let request = read::render(&types, &inputs, MAX_OUTPUT_TOKENS);
            let mut body = AnthropicTransport::body(&binding, &request);
            if !variant.enforced
                && let Some(config) = body["output_config"].as_object_mut()
            {
                config.remove("format");
            }
            if dry_run {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&body).unwrap_or_default()
                );
                break;
            }
            tally.calls += 1;
            call(&client, &key, &body, batch, &allowed, &mut tally)?;
        }
        summary.push((variant.name, tally));
    }
    if dry_run {
        return Ok(());
    }

    println!("\n## Summary\n");
    println!(
        "| Variant | Small talk abstained | Statements typed | Calls | Refused | Cut off | Invalid | Failed | Cost |"
    );
    println!("|---|---|---|---|---|---|---|---|---|");
    let mut total = 0.0;
    for (name, t) in &summary {
        let cost = (t.input_tokens as f64 * PRICE.0 + t.output_tokens as f64 * PRICE.1) / 1e6;
        total += cost;
        println!(
            "| {name} | {} of {} | {} of {} | {} | {} | {} | {} | {} | ${cost:.3} |",
            t.abstained_small_talk,
            SMALL_TALK.len(),
            t.typed_statements,
            STATEMENTS.len(),
            t.calls,
            t.refused,
            t.cut_off,
            t.invalid,
            t.failed,
        );
    }
    println!("\nTotal cost: ${total:.3}");
    Ok(())
}

/// Sends one batch and prints, for each turn, what the model chose. A key
/// or model the API turns down stops the probe: every call would fail
/// alike.
fn call(
    client: &HttpClient,
    key: &str,
    body: &Value,
    batch: &[Turn],
    allowed: &BTreeSet<&str>,
    tally: &mut Tally,
) -> Result<(), String> {
    let reply = match client.post_json(
        &format!("{ENDPOINT}/v1/messages"),
        &[("x-api-key", key), ("anthropic-version", API_VERSION)],
        body,
    ) {
        Ok(reply) => reply,
        Err(e) => {
            tally.failed += 1;
            println!("- call failed before a reply: {e:?}");
            return Ok(());
        }
    };
    let said: Value = serde_json::from_str(&reply.body).unwrap_or(Value::Null);
    if matches!(reply.status, 401 | 403 | 404) {
        return Err(format!(
            "the API turned the request down (HTTP {}: {})",
            reply.status, said["error"]["type"]
        ));
    }
    if !(200..300).contains(&reply.status) {
        tally.failed += 1;
        println!(
            "- HTTP {}: {} {}",
            reply.status,
            said["error"]["type"],
            said["error"]["message"]
                .as_str()
                .unwrap_or_default()
                .chars()
                .take(200)
                .collect::<String>()
        );
        return Ok(());
    }
    tally.input_tokens += said["usage"]["input_tokens"].as_u64().unwrap_or(0);
    tally.output_tokens += said["usage"]["output_tokens"].as_u64().unwrap_or(0);
    let stop = said["stop_reason"].as_str().unwrap_or("?");
    let ids: Vec<&str> = batch.iter().map(|turn| turn.id).collect();
    println!(
        "- call for {}: stop_reason {stop}{}, {} output tokens",
        ids.join(" "),
        match said["stop_details"]["category"].as_str() {
            Some(category) => format!(" ({category})"),
            None => String::new(),
        },
        said["usage"]["output_tokens"]
    );
    match stop {
        "refusal" => {
            tally.refused += 1;
            return Ok(());
        }
        "max_tokens" => {
            tally.cut_off += 1;
            return Ok(());
        }
        _ => {}
    }
    let text: String = said["content"]
        .as_array()
        .map(|blocks| {
            blocks
                .iter()
                .filter(|b| b["type"] == "text")
                .filter_map(|b| b["text"].as_str())
                .collect()
        })
        .unwrap_or_default();
    let texts: Vec<&str> = batch.iter().map(|turn| turn.text.as_str()).collect();
    if let Err(problems) = read::validate(&text, &texts, allowed, Quotes::DropUnfound) {
        tally.invalid += 1;
        println!(
            "  the engine would reject this reply: {}",
            problems.first().map(String::as_str).unwrap_or("?")
        );
    }
    let choices = choices(&text);
    for (i, turn) in batch.iter().enumerate() {
        let choice = choices
            .get(&format!("C{}", i + 1))
            .cloned()
            .unwrap_or_else(|| "(no answer)".into());
        if turn.small_talk && choice.starts_with("abstain") {
            tally.abstained_small_talk += 1;
        }
        if !turn.small_talk && choice.starts_with("type") {
            tally.typed_statements += 1;
        }
        let kind = if turn.small_talk {
            "small talk"
        } else {
            "statement"
        };
        let shown: String = turn.text.chars().take(70).collect();
        println!("  - {} ({kind}): {choice} — \"{shown}…\"", turn.id);
    }
    Ok(())
}

/// What the reply chose for each handle: `type <key>`, `abstain <reason>`,
/// or a note that it gave both or neither.
fn choices(text: &str) -> BTreeMap<String, String> {
    let trimmed = text
        .trim()
        .trim_start_matches("```json")
        .trim_start_matches("```")
        .trim_end_matches("```");
    let value: Value = serde_json::from_str(trimmed.trim()).unwrap_or(Value::Null);
    let mut out = BTreeMap::new();
    for item in value["items"].as_array().into_iter().flatten() {
        let Some(handle) = item["chunk"].as_str() else {
            continue;
        };
        let choice = match (item["type"].as_str(), item["abstain"].as_str()) {
            (Some(key), None) => format!("type {key}"),
            (None, Some(reason)) => format!("abstain {reason}"),
            (Some(key), Some(reason)) => format!("both: type {key}, abstain {reason}"),
            (None, None) => "neither".into(),
        };
        out.insert(handle.to_string(), choice);
    }
    out
}

/// The probe's turns from conv-26, small talk and statements alternating
/// so each call of eight sees both.
fn turns(data: &Path) -> Result<Vec<Turn>, String> {
    let text = std::fs::read_to_string(data).map_err(|e| format!("{}: {e}", data.display()))?;
    let all: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    let conversation = all
        .as_array()
        .and_then(|samples| samples.iter().find(|s| s["sample_id"] == SAMPLE))
        .map(|sample| &sample["conversation"])
        .ok_or(format!("{SAMPLE} is not in the file"))?;
    let mut by_id = BTreeMap::new();
    for (key, session) in conversation.as_object().into_iter().flatten() {
        if !key.starts_with("session_") || key.ends_with("date_time") {
            continue;
        }
        for turn in session.as_array().into_iter().flatten() {
            if let (Some(id), Some(text)) = (turn["dia_id"].as_str(), turn["text"].as_str()) {
                by_id.insert(id.to_string(), text.to_string());
            }
        }
    }
    let mut small = SMALL_TALK.iter().map(|id| (*id, true));
    let mut said = STATEMENTS.iter().map(|id| (*id, false));
    let mut order = Vec::new();
    loop {
        let (a, b) = (small.next(), said.next());
        if a.is_none() && b.is_none() {
            break;
        }
        order.extend(a);
        order.extend(b);
    }
    order
        .into_iter()
        .map(|(id, small_talk)| {
            Ok(Turn {
                id,
                small_talk,
                text: by_id
                    .get(id)
                    .cloned()
                    .ok_or(format!("{id} is not in {SAMPLE}"))?,
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_reply_is_read_as_one_choice_per_handle() {
        let reply = "```json\n{\"items\": [\
            {\"chunk\": \"C1\", \"type\": \"fact\"},\
            {\"chunk\": \"C2\", \"abstain\": \"no_fit\"},\
            {\"chunk\": \"C3\", \"type\": \"plan\", \"abstain\": \"ambiguous\"},\
            {\"chunk\": \"C4\"}]}\n```";
        let read = choices(reply);
        assert_eq!(read["C1"], "type fact");
        assert_eq!(read["C2"], "abstain no_fit");
        assert_eq!(read["C3"], "both: type plan, abstain ambiguous");
        assert_eq!(read["C4"], "neither");
        assert!(choices("not json").is_empty());
    }
}
