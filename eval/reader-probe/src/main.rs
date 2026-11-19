//! Sends the engine's chunk reader the same LoCoMo turns under a few
//! vocabularies, and says for each turn what type the model chose and
//! what it quoted. See README.md.
//!
//! The requests are the engine's own: its reader prompt and reply shape
//! (`read::render`), its Anthropic request body (`AnthropicTransport::body`)
//! and its HTTP client, with the engine's settings for the reader. Only
//! the vocabulary changes between variants.

// A probe's output is what it prints, for a person to read.
#![allow(clippy::print_stdout, clippy::print_stderr)]

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use core_memory_contract::SpanKind;
use core_memory_core::model::BindingConfig;
use core_memory_core::read::{self, ChunkInput, ChunkReading, Quotes, ReadChoice, TypeOption};
use core_memory_model_anthropic::{API_VERSION, AnthropicTransport};
use core_memory_model_http::{HttpClient, Roots};
use serde_json::{Value, json};

const ENDPOINT: &str = "https://api.anthropic.com";
const MODEL: &str = "claude-haiku-5-5";
const EFFORT: &str = "low";
const PER_CALL: usize = 8;
const MAX_OUTPUT_TOKENS: u32 = 4096;
const KEY_ENV: &str = "CORE_MEMORY_ANTHROPIC_KEY";
/// Dollars per million tokens, input and output.
const PRICE: (f64, f64) = (0.10, 0.50);
const SAMPLE: &str = "conv-26";

/// memoriesQL's bead types, as its registry defines them (schema 7).
const REGISTRY: [(&str, &str); 12] = [
    (
        "observation",
        "Grounded observed statement or change with no more specific semantic class",
    ),
    (
        "decision",
        "An attributable choice, commitment, or selected alternative",
    ),
    (
        "goal_declaration",
        "An explicit observed desired future state or objective",
    ),
    (
        "goal_update",
        "An observed progress, scope, status, blocking, completion, or abandonment update about a goal",
    ),
    ("action", "An attributable attempted or completed action"),
    ("outcome", "An observed result or consequence"),
    (
        "lesson",
        "An explicitly stated evidence-grounded takeaway from experience",
    ),
    (
        "constraint",
        "An observed requirement, dependency, limit, or blocker",
    ),
    (
        "incident",
        "An observed disruptive event requiring attention or response",
    ),
    (
        "preference",
        "An explicitly expressed choice tendency; not automatically an endorsed subject-model rule",
    ),
    (
        "instruction",
        "An explicitly observed directive or standing rule; source presence alone does not grant authority",
    ),
    (
        "hypothesis",
        "An explicitly stated testable possibility, not a confirmed fact",
    ),
];

/// A type for what keeps people in touch, proposed beside the registry.
///
/// The first wording ("... that tends a relationship rather than telling
/// news") took statements that open with thanks. This one says what a
/// message that also tells something is.
const SOCIAL: (&str, &str) = (
    "social",
    "A greeting, thanks, compliment, sympathy or check-in that tends a relationship and states nothing else. A message that also tells something takes the type of what it tells",
);

/// One type for a goal and the updates about it, in place of
/// `goal_declaration` and `goal_update`, as memoriesQL's registry will
/// have it: how an update relates to its goal is what the judge's
/// relationships record.
const GOAL: (&str, &str) = (
    "goal",
    "An explicit observed desired future state or objective, or an observed update about one: its progress, scope, status, blocking, completion, or abandonment",
);

#[derive(Clone, Copy, PartialEq, Eq)]
enum Group {
    SmallTalk,
    Statement,
    Goal,
}

impl Group {
    fn name(self) -> &'static str {
        match self {
            Group::SmallTalk => "small talk",
            Group::Statement => "statement",
            Group::Goal => "goal",
        }
    }
}

/// Messages that tend a conversation: greetings, thanks, compliments and
/// questions to the other person. The second run's reader refused D8:3,
/// D8:5, D9:5 and D9:7.
const SMALL_TALK: [&str; 14] = [
    "D1:1", "D1:4", "D1:8", "D3:15", "D3:19", "D4:12", "D5:7", "D8:3", "D8:5", "D8:18", "D8:38",
    "D9:5", "D9:7", "D18:6",
];

/// Messages that tell something about the speaker or their life.
const STATEMENTS: [&str; 10] = [
    "D1:3", "D1:16", "D2:8", "D3:16", "D4:3", "D5:10", "D7:16", "D7:22", "D14:33", "D17:8",
];

/// Caroline's goal of working in counselling, and of adopting, stated and
/// then restated with its progress.
const GOALS: [&str; 6] = ["D1:9", "D1:11", "D2:10", "D4:13", "D6:3", "D7:5"];

struct Turn {
    id: &'static str,
    group: Group,
    text: String,
}

struct Variant {
    name: &'static str,
    types: Vec<(&'static str, &'static str)>,
}

fn variants() -> Vec<Variant> {
    // memoriesQL's registry once the two goal types are merged.
    let mut merged: Vec<_> = REGISTRY
        .iter()
        .copied()
        .filter(|(key, _)| !key.starts_with("goal_"))
        .collect();
    merged.insert(2, GOAL);
    let mut with_social = merged.clone();
    with_social.push(SOCIAL);
    vec![
        Variant {
            name: "memoriesQL's 11 types, with one goal",
            types: merged,
        },
        Variant {
            name: "the 11 and social",
            types: with_social,
        },
    ]
}

/// The vocabularies of the probe's second run, kept so that a run can be
/// repeated: memoriesQL's 12, the 12 and the first `social`, and 11 and the
/// first `social` with one `goal`.
#[allow(dead_code)]
fn earlier_variants() -> Vec<Variant> {
    let registry: Vec<_> = REGISTRY.to_vec();
    let first_social = (
        "social",
        "A greeting, thanks, compliment, sympathy or check-in that tends a relationship rather than telling news",
    );
    let mut with_social = registry.clone();
    with_social.push(first_social);
    let mut one_goal: Vec<_> = REGISTRY
        .iter()
        .copied()
        .filter(|(key, _)| !key.starts_with("goal_"))
        .collect();
    one_goal.insert(2, GOAL);
    one_goal.push(first_social);
    vec![
        Variant {
            name: "memoriesQL's 12 types",
            types: registry,
        },
        Variant {
            name: "the 12 and social",
            types: with_social,
        },
        Variant {
            name: "11 and social: one goal for goal_declaration and goal_update",
            types: one_goal,
        },
    ]
}

#[derive(Default)]
struct Tally {
    chosen: BTreeMap<String, usize>,
    entities: usize,
    actions: usize,
    outcomes: usize,
    calls: usize,
    cut_off: usize,
    refused: usize,
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
    let binding: BindingConfig = serde_json::from_value(json!({
        "id": "probe",
        "provider": "anthropic",
        "endpoint": ENDPOINT,
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "api_key_env": KEY_ENV,
    }))
    .map_err(|e| e.to_string())?;

    let variants = variants();
    // What each variant chose for each turn, for the side-by-side table.
    let mut grid: BTreeMap<&str, Vec<String>> = BTreeMap::new();
    let mut summary = Vec::new();
    for variant in &variants {
        println!("\n## {}\n", variant.name);
        let types: Vec<TypeOption<'_>> = variant
            .types
            .iter()
            .map(|(key, definition)| TypeOption {
                key,
                definition: Some(definition),
            })
            .collect();
        let allowed: BTreeSet<&str> = variant.types.iter().map(|(key, _)| *key).collect();
        let mut tally = Tally::default();
        for batch in turns.chunks(PER_CALL) {
            let inputs: Vec<ChunkInput<'_>> = batch
                .iter()
                .map(|turn| ChunkInput {
                    text: &turn.text,
                    role: Some("user"),
                })
                .collect();
            let request = read::render(&types, &inputs, MAX_OUTPUT_TOKENS);
            let body = AnthropicTransport::body(&binding, &request);
            if dry_run {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&body).unwrap_or_default()
                );
                break;
            }
            tally.calls += 1;
            let choices = call(&client, &key, &body, batch, &allowed, &mut tally)?;
            for (turn, choice) in batch.iter().zip(choices) {
                grid.entry(turn.id).or_default().push(choice);
            }
        }
        summary.push((variant.name, tally));
    }
    if dry_run {
        return Ok(());
    }

    println!("\n## Each turn under each vocabulary\n");
    let names: Vec<&str> = variants.iter().map(|v| v.name).collect();
    println!("| Turn | Group | {} |", names.join(" | "));
    println!("|---|---|{}", "---|".repeat(names.len()));
    for turn in &turns {
        let chosen = grid.get(turn.id).cloned().unwrap_or_default();
        println!(
            "| {} | {} | {} |",
            turn.id,
            turn.group.name(),
            chosen.join(" | ")
        );
    }

    println!("\n## Summary\n");
    println!(
        "| Vocabulary | Choices | Entities, actions, outcomes quoted | Calls | Cut off | Refused | Invalid | Failed | Cost |"
    );
    println!("|---|---|---|---|---|---|---|---|---|");
    let mut total = 0.0;
    for (name, t) in &summary {
        let cost = (t.input_tokens as f64 * PRICE.0 + t.output_tokens as f64 * PRICE.1) / 1e6;
        total += cost;
        let chosen: Vec<String> = t.chosen.iter().map(|(k, n)| format!("{k} {n}")).collect();
        println!(
            "| {name} | {} | {}, {}, {} | {} | {} | {} | {} | {} | ${cost:.3} |",
            chosen.join(", "),
            t.entities,
            t.actions,
            t.outcomes,
            t.calls,
            t.cut_off,
            t.refused,
            t.invalid,
            t.failed,
        );
    }
    println!("\nTotal cost: ${total:.3}");
    Ok(())
}

/// Sends one batch, prints for each turn what the model chose and quoted,
/// and returns the choices in batch order. A key or model the API turns
/// down stops the probe: every call would fail alike.
fn call(
    client: &HttpClient,
    key: &str,
    body: &Value,
    batch: &[Turn],
    allowed: &BTreeSet<&str>,
    tally: &mut Tally,
) -> Result<Vec<String>, String> {
    let unread = |why: &str| vec![format!("({why})"); batch.len()];
    let reply = match client.post_json(
        &format!("{ENDPOINT}/v1/messages"),
        &[("x-api-key", key), ("anthropic-version", API_VERSION)],
        body,
    ) {
        Ok(reply) => reply,
        Err(e) => {
            tally.failed += 1;
            println!("- call failed before a reply: {e:?}");
            return Ok(unread("failed"));
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
        println!("- HTTP {}: {}", reply.status, said["error"]["type"]);
        return Ok(unread("failed"));
    }
    tally.input_tokens += said["usage"]["input_tokens"].as_u64().unwrap_or(0);
    tally.output_tokens += said["usage"]["output_tokens"].as_u64().unwrap_or(0);
    let stop = said["stop_reason"].as_str().unwrap_or("?");
    let ids: Vec<&str> = batch.iter().map(|turn| turn.id).collect();
    println!(
        "- call for {}: stop_reason {stop}, {} output tokens",
        ids.join(" "),
        said["usage"]["output_tokens"]
    );
    match stop {
        "refusal" => {
            tally.refused += 1;
            return Ok(unread("refused"));
        }
        "max_tokens" => {
            tally.cut_off += 1;
            return Ok(unread("cut off"));
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
    let readings = match read::validate(&text, &texts, allowed, Quotes::DropUnfound) {
        Ok(readings) => readings,
        Err(problems) => {
            tally.invalid += 1;
            println!(
                "  the engine would reject this reply: {}",
                problems.first().map(String::as_str).unwrap_or("?")
            );
            return Ok(unread("invalid"));
        }
    };
    let mut choices = Vec::new();
    for (turn, reading) in batch.iter().zip(&readings) {
        let choice = match &reading.choice {
            ReadChoice::Type(key) => key.clone(),
            ReadChoice::Abstain(why) => format!("no type ({})", why.as_str()),
        };
        *tally.chosen.entry(choice.clone()).or_default() += 1;
        let shown: String = turn.text.chars().take(70).collect();
        println!(
            "  - {} ({}): {choice} — \"{shown}…\"",
            turn.id,
            turn.group.name()
        );
        let quoted = quotes(&turn.text, reading, tally);
        if !quoted.is_empty() {
            println!("    {}", quoted.join("; "));
        }
        choices.push(choice);
    }
    Ok(choices)
}

/// What a reading quoted, as `kind: "words"`, and counted in `tally`.
fn quotes(text: &str, reading: &ChunkReading, tally: &mut Tally) -> Vec<String> {
    reading
        .spans
        .iter()
        .map(|span| {
            let words = text
                .get(span.byte_start as usize..span.byte_end as usize)
                .unwrap_or("?");
            let kind = match span.kind {
                SpanKind::Entity { entity_kind } => {
                    tally.entities += 1;
                    entity_kind.as_str()
                }
                SpanKind::Action => {
                    tally.actions += 1;
                    "action"
                }
                SpanKind::Outcome => {
                    tally.outcomes += 1;
                    "outcome"
                }
            };
            format!("{kind}: \"{words}\"")
        })
        .collect()
}

/// The probe's turns from conv-26: small talk, statements and goals
/// interleaved, so each call sees all three.
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
    let mut groups = [
        SMALL_TALK
            .iter()
            .map(|id| (*id, Group::SmallTalk))
            .collect::<Vec<_>>(),
        STATEMENTS
            .iter()
            .map(|id| (*id, Group::Statement))
            .collect(),
        GOALS.iter().map(|id| (*id, Group::Goal)).collect(),
    ]
    .map(|group| group.into_iter());
    let mut order = Vec::new();
    loop {
        let before = order.len();
        for group in &mut groups {
            order.extend(group.next());
        }
        if order.len() == before {
            break;
        }
    }
    order
        .into_iter()
        .map(|(id, group)| {
            Ok(Turn {
                id,
                group,
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
    fn the_merged_registry_has_one_goal_and_social_is_added_to_it() {
        let [merged, social] = variants().try_into().ok().unwrap();
        assert_eq!(merged.types.len(), 11);
        assert!(merged.types.contains(&GOAL));
        assert!(!merged.types.iter().any(|(key, _)| key.starts_with("goal_")));
        assert_eq!(social.types.len(), 12);
        assert!(social.types.contains(&SOCIAL));
    }

    #[test]
    fn the_earlier_vocabularies_differ_only_as_proposed() {
        let [registry, social, goal] = earlier_variants().try_into().ok().unwrap();
        assert_eq!(registry.types.len(), 12);
        assert_eq!(social.types.len(), 13);
        assert!(social.types.iter().any(|(key, _)| *key == "social"));
        assert_eq!(goal.types.len(), 12);
        assert!(goal.types.contains(&GOAL));
        assert!(!goal.types.iter().any(|(key, _)| key.starts_with("goal_")));
    }
}
