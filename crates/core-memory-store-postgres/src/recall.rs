//! Recall: a read of the stored text and of what a judge said of it.
//!
//! Recall keeps nothing of its own. It reads `chunk_text`, the one copy of
//! source text, through the full-text index of migration 2, and `edges`,
//! which hold what a judge said as IDs. An erase therefore reaches
//! everything recall could return, in the erase's own transaction.
//!
//! By default a chunk matches if it holds any word of the query, and the
//! matches are ranked by how rare their words are among them. Nothing is
//! kept for that either: the index says which chunks hold each word, and
//! the counts are made from the matches of the one query.
//!
//! A query that comes with a vector is also compared with the vectors of
//! `chunk_vectors`, which an erase deletes with the text they were made
//! from. A vector finds a chunk; what is returned is the chunk's text.
//!
//! Every statement here runs in one read-only transaction on one
//! snapshot, which takes `schema_migrations` before anything else.

use std::collections::{BTreeMap, BTreeSet};

use core_memory_contract::{
    Arrow, BeadId, BeadKind, ChunkId, EdgeType, EventId, EventKey, MatchedBy, RECALL_MAX_LINKED,
    RECALL_MAX_TEXT_BYTES, RecallAnswer, RecallCompared, RecallFound, RecallMatch, RecallMeaning,
    RecallVia, RecallWords, RecalledArrow, RecalledBead, RecalledChunk, RecalledEvent, TimeBasis,
    Timestamp,
};
use core_memory_core::{MeaningLeg, QuestionVector, RecallQuery, Search, StoreError};
use postgres::Transaction;
use postgres::error::SqlState;
use time::OffsetDateTime;
use uuid::Uuid;

use crate::connect::SearchLimits;
use crate::embed::{dimensions, named, vector_schema};
use crate::error::{backend, corrupt};
use crate::store::{PostgresStore, read_transaction, to_u32, to_u64};

/// The words of a chunk's text as the index of migration 2 holds them. A
/// query must use this expression exactly, or it cannot use the index.
const SEARCHED: &str = "pg_catalog.to_tsvector('pg_catalog.english'::pg_catalog.regconfig, \
                        pg_catalog.\"left\"(t.text, 100000))";
/// The caller's words as a query, read as a web search reads them: every
/// word required.
const QUERY: &str =
    "pg_catalog.websearch_to_tsquery('pg_catalog.english'::pg_catalog.regconfig, $1)";
/// The caller's text as the words the index holds: its English stems,
/// without the common words. Read as plain text, so nothing in it is an
/// operator.
const STEMS: &str = "pg_catalog.tsvector_to_array(pg_catalog.to_tsvector(\
                     'pg_catalog.english'::pg_catalog.regconfig, $1))";
/// Arrows that end the currency of the bead they point at.
const ENDING: &str = "(SELECT a.arrow FROM core_memory.arrows AS a WHERE a.ends_currency)";
/// Arrows that say two beads cannot both hold.
const DISPUTING: &str = "(SELECT a.arrow FROM core_memory.arrows AS a WHERE a.disputes)";

/// The constant of reciprocal rank fusion: a bead's score is the sum, over
/// the searches that found it, of one over this plus its place in that
/// search. With 60, the usual value, a bead both searches found outranks
/// one that either put first.
const FUSION_K: f32 = 60.0;

/// How many matches each search gives when two are put together. More
/// than the answer holds, so that a bead both found a little way down is
/// not lost to one that a single search put higher.
fn fused_depth(limit: u32) -> i64 {
    i64::from(limit.saturating_mul(3).max(30))
}

/// One bead's place in an answer, before its details are read.
struct Placed {
    bead: Uuid,
    via: RecallVia,
}

/// A bead a search found.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Hit {
    bead: Uuid,
    rank: f32,
    by: MatchedBy,
    distance: Option<f32>,
}

/// Puts the matches of the two searches in one order, by reciprocal rank
/// fusion. Each list is best first. Places are what is added up, never
/// the two searches' own scores, which measure different things.
fn fuse(words: &[(Uuid, f32)], near: &[(Uuid, f32)], limit: usize) -> Vec<Hit> {
    let mut hits: BTreeMap<Uuid, Hit> = BTreeMap::new();
    for (place, (bead, _)) in words.iter().enumerate() {
        hits.insert(
            *bead,
            Hit {
                bead: *bead,
                rank: 1.0 / (FUSION_K + place as f32 + 1.0),
                by: MatchedBy::Words,
                distance: None,
            },
        );
    }
    for (place, (bead, distance)) in near.iter().enumerate() {
        let score = 1.0 / (FUSION_K + place as f32 + 1.0);
        hits.entry(*bead)
            .and_modify(|hit| {
                hit.rank += score;
                hit.by = MatchedBy::Both;
                hit.distance = Some(*distance);
            })
            .or_insert(Hit {
                bead: *bead,
                rank: score,
                by: MatchedBy::Meaning,
                distance: Some(*distance),
            });
    }
    let mut fused: Vec<Hit> = hits.into_values().collect();
    // Best first. Two beads with one score keep a fixed order: the one
    // whose words matched first, then by ID.
    fused.sort_by(|a, b| {
        b.rank
            .total_cmp(&a.rank)
            .then((a.by == MatchedBy::Meaning).cmp(&(b.by == MatchedBy::Meaning)))
            .then(a.bead.cmp(&b.bead))
    });
    fused.truncate(limit);
    fused
}

/// What a search by meaning came to.
enum Near {
    /// The live chunk beads whose vectors are nearest the query's, nearest
    /// first, with their cosine distances.
    Found(Vec<(Uuid, f32)>),
    /// The database cannot compare vectors.
    NotInstalled,
    /// The comparison ran past the time one search may take.
    TooSlow,
}

/// What a search by the words came to.
enum Worded {
    /// The live beads whose text matched, best first, with their ranks.
    Found(Vec<(Uuid, f32)>),
    /// The query has no word to look for.
    NoTerms,
    /// The search ran past the time one search may take.
    TooSlow,
}

/// What an answer says of how it was searched for.
#[derive(Debug, Clone, Copy)]
struct Searched {
    meaning: RecallMeaning,
    words: RecallWords,
    compared: Option<RecallCompared>,
}

/// Sets the limit for the statements that follow in this transaction.
fn limit_statements(tx: &mut Transaction<'_>, ms: u128) -> Result<(), StoreError> {
    tx.query_one(
        "SELECT pg_catalog.set_config('statement_timeout', $1, true)",
        &[&ms.to_string()],
    )
    .map_err(backend)?;
    Ok(())
}

/// Runs one search of a recall under the time one search may take, and
/// gives `None` if it ran past it.
///
/// The cost of a search grows with what is stored: every vector is
/// compared, and every chunk that holds a word is counted. Running past the
/// limit ends that search and not the recall. It runs inside a savepoint,
/// the other search still answers, and the caller is told, where a failed
/// recall would be asked for again and run as long again.
fn within<T>(
    tx: &mut Transaction<'_>,
    limits: SearchLimits,
    search: impl FnOnce(&mut Transaction<'_>) -> Result<T, postgres::Error>,
) -> Result<Option<T>, StoreError> {
    let mut held = tx.transaction().map_err(backend)?;
    limit_statements(&mut held, limits.search_ms)?;
    match search(&mut held) {
        Ok(found) => {
            // A savepoint that is kept keeps what it set, so the session's
            // own limit is put back before it is.
            limit_statements(&mut held, limits.statement_ms)?;
            held.commit().map_err(backend)?;
            Ok(Some(found))
        }
        Err(e) if e.code() == Some(&SqlState::QUERY_CANCELED) => {
            held.rollback().map_err(backend)?;
            Ok(None)
        }
        Err(e) => Err(backend(e)),
    }
}

/// Compares the query's vector with the stored ones.
///
/// Every vector of the scopes searched is compared: the search is exact,
/// and its cost grows with what is stored. Only vectors the query's own
/// embedder made are compared, and only those of scopes that allow the
/// embedder's destination now: a scope that has taken its consent back is
/// no longer searched this way.
fn nearest(
    tx: &mut Transaction<'_>,
    question: &QuestionVector,
    scopes: &Option<Vec<String>>,
    depth: i64,
    limits: SearchLimits,
) -> Result<Near, StoreError> {
    let Some(schema) = vector_schema(tx)? else {
        return Ok(Near::NotInstalled);
    };
    let size = dimensions(&question.vector)?;
    let sql = format!(
        "SELECT near.chunk_id, near.distance::float4
                   FROM (SELECT v.chunk_id,
                                v.vector::{schema}.vector
                                    OPERATOR({schema}.<=>) $1::real[]::{schema}.vector
                                    AS distance
                           FROM core_memory.chunk_vectors AS v
                          WHERE v.embedder_rev = $2 AND v.dimensions = $3
                            AND ($4::text[] IS NULL OR v.scope = ANY ($4))
                            AND EXISTS (SELECT 1 FROM core_memory.scope_destinations AS d
                                         WHERE d.scope = v.scope AND d.destination = $5
                                           AND d.state = 'granted')) AS near
                   JOIN core_memory.beads AS b
                     ON b.bead_id = near.chunk_id AND b.kind = 'chunk' AND b.state = 'live'
                  WHERE $6::float8 IS NULL OR near.distance <= $6
                  ORDER BY near.distance, b.time_at DESC, b.seq DESC
                  LIMIT $7"
    );
    let found = within(tx, limits, |tx| {
        tx.query(
            &sql,
            &[
                &question.vector,
                &question.embedder_rev.as_bytes().as_slice(),
                &size,
                scopes,
                &question.destination,
                &question.max_distance.map(f64::from),
                &depth,
            ],
        )
    })?;
    Ok(match found {
        Some(rows) => Near::Found(rows.iter().map(|r| (r.get(0), r.get(1))).collect()),
        None => Near::TooSlow,
    })
}

/// How much of what is searched a search by meaning reaches: the vectors
/// [`nearest`] compares, and the beads of messages and tool calls in the
/// scopes searched.
fn compared(
    tx: &mut Transaction<'_>,
    question: &QuestionVector,
    scopes: &Option<Vec<String>>,
) -> Result<RecallCompared, StoreError> {
    let row = tx
        .query_one(
            "SELECT (SELECT pg_catalog.count(*)
                       FROM core_memory.chunk_vectors AS v
                      WHERE v.embedder_rev = $1 AND v.dimensions = $2
                        AND ($3::text[] IS NULL OR v.scope = ANY ($3))
                        AND EXISTS (SELECT 1 FROM core_memory.scope_destinations AS d
                                     WHERE d.scope = v.scope AND d.destination = $4
                                       AND d.state = 'granted')),
                    (SELECT pg_catalog.count(*)
                       FROM core_memory.beads AS b
                      WHERE b.kind IN ('chunk', 'tool') AND b.state = 'live'
                        AND ($3::text[] IS NULL OR b.scope = ANY ($3)))",
            &[
                &question.embedder_rev.as_bytes().as_slice(),
                &dimensions(&question.vector)?,
                scopes,
                &question.destination,
            ],
        )
        .map_err(backend)?;
    Ok(RecallCompared {
        vectors: to_u64(row.get(0), "vectors compared")?,
        beads: to_u64(row.get(1), "beads searched")?,
    })
}

/// Looks for the query's words in the text of the scopes searched.
fn worded(
    tx: &mut Transaction<'_>,
    query: &RecallQuery,
    scopes: &Option<Vec<String>>,
    depth: i64,
    limits: SearchLimits,
) -> Result<Worded, StoreError> {
    let found = match query.matching {
        RecallMatch::All => {
            // A query of common words alone asks for nothing, and
            // PostgreSQL would match nothing with it. One of words to leave
            // out alone, or one where a word to leave out is an
            // alternative, names no word the index can look up: PostgreSQL
            // would read every chunk to answer it (`querytree` says `T`).
            // Neither is searched, and saying so is the honest answer.
            let searchable: bool = tx
                .query_one(
                    &format!(
                        "SELECT pg_catalog.numnode(q) > 0 AND pg_catalog.querytree(q) <> 'T'
                           FROM (SELECT {QUERY} AS q) AS asked"
                    ),
                    &[&query.query],
                )
                .map_err(backend)?
                .get(0);
            if !searchable {
                return Ok(Worded::NoTerms);
            }
            let sql = format!(
                "SELECT b.bead_id, max(pg_catalog.ts_rank_cd({SEARCHED}, {QUERY}))::float4
                   FROM core_memory.chunk_text AS t
                   JOIN core_memory.chunks AS c ON c.chunk_id = t.chunk_id
                   JOIN core_memory.beads AS b
                     ON b.bead_id = ANY (ARRAY[t.chunk_id, c.event_id])
                    AND b.kind IN ('chunk', 'tool') AND b.state = 'live'
                  WHERE {SEARCHED} @@ {QUERY}
                    AND ($2::text[] IS NULL OR t.scope = ANY ($2))
                  GROUP BY b.bead_id, b.time_at, b.seq
                  ORDER BY 2 DESC, b.time_at DESC, b.seq DESC
                  LIMIT $3"
            );
            within(tx, limits, |tx| {
                tx.query(&sql, &[&query.query, scopes, &depth])
            })?
        }
        RecallMatch::Any => {
            let stems: Vec<String> = tx
                .query_one(&format!("SELECT {STEMS}"), &[&query.query])
                .map_err(backend)?
                .get(0);
            if stems.is_empty() {
                return Ok(Worded::NoTerms);
            }
            // The index is asked, word by word, which chunks hold it. No
            // chunk's text is read again to rank it: breaking text into
            // words is what a search costs, and it was done once, for the
            // index. Each word is written as PostgreSQL writes a stem,
            // quoted, so nothing a caller sent is read as an operator.
            //
            // A word counts for more the fewer of the matching chunks hold
            // it: the natural logarithm of one plus the matching chunks
            // over the chunks that hold the word. A chunk's rank is that,
            // added up over the words it holds, in one order so that two
            // chunks with the same words have the same rank. So a chunk
            // that holds every word has the highest rank there is. Chunks
            // of one rank come newest first. A tool call's rank is its
            // best chunk's.
            let sql = format!(
                "WITH held AS MATERIALIZED (
                     SELECT w.stem, h.chunk_id
                       FROM pg_catalog.unnest($1::text[]) AS w (stem)
                       CROSS JOIN LATERAL (
                           SELECT t.chunk_id
                             FROM core_memory.chunk_text AS t
                            WHERE {SEARCHED} @@ pg_catalog.array_to_tsvector(
                                      ARRAY[w.stem])::text::pg_catalog.tsquery
                              AND ($2::text[] IS NULL OR t.scope = ANY ($2))
                       ) AS h
                 ), weights AS (
                     SELECT h.stem,
                            pg_catalog.ln(1 + n.chunks::float8 / pg_catalog.count(*)) AS weight
                       FROM held AS h
                       CROSS JOIN (SELECT pg_catalog.count(DISTINCT x.chunk_id) AS chunks
                                     FROM held AS x) AS n
                      GROUP BY h.stem, n.chunks
                 ), ranked AS (
                     SELECT h.chunk_id,
                            pg_catalog.sum(w.weight ORDER BY w.stem) AS rank
                       FROM held AS h
                       JOIN weights AS w ON w.stem = h.stem
                      GROUP BY h.chunk_id
                 )
                 SELECT b.bead_id, pg_catalog.max(r.rank)::float4
                   FROM ranked AS r
                   JOIN core_memory.chunks AS c ON c.chunk_id = r.chunk_id
                   JOIN core_memory.beads AS b
                     ON b.bead_id = ANY (ARRAY[r.chunk_id, c.event_id])
                    AND b.kind IN ('chunk', 'tool') AND b.state = 'live'
                  GROUP BY b.bead_id, b.time_at, b.seq
                  ORDER BY 2 DESC, b.time_at DESC, b.seq DESC
                  LIMIT $3"
            );
            within(tx, limits, |tx| tx.query(&sql, &[&stems, scopes, &depth]))?
        }
    };
    Ok(match found {
        Some(rows) => Worded::Found(rows.iter().map(|r| (r.get(0), r.get(1))).collect()),
        None => Worded::TooSlow,
    })
}

impl PostgresStore {
    pub(crate) fn read_recall(&mut self, query: &RecallQuery) -> Result<RecallAnswer, StoreError> {
        let limits = self.search_limits();
        let mut tx = read_transaction(self.client()?)?;
        let scopes: Option<Vec<String>> = named(&query.scopes);

        // By meaning, if the query came with a vector.
        let mut reached = None;
        let (near, meaning) = match (query.search, &query.meaning) {
            (Search::Words, _) => (None, RecallMeaning::NotAsked),
            (_, MeaningLeg::Without(why)) => (None, *why),
            (_, MeaningLeg::Vector(question)) => {
                let depth = match query.search {
                    Search::Meaning => i64::from(query.limit),
                    Search::Both | Search::Words => fused_depth(query.limit),
                };
                match nearest(&mut tx, question, &scopes, depth, limits)? {
                    Near::Found(near) => {
                        reached = Some(compared(&mut tx, question, &scopes)?);
                        (Some(near), RecallMeaning::Used)
                    }
                    Near::NotInstalled => (None, RecallMeaning::NotInstalled),
                    Near::TooSlow => (None, RecallMeaning::Unavailable),
                }
            }
        };
        if query.search == Search::Meaning {
            let said = Searched {
                meaning,
                words: RecallWords::NotAsked,
                compared: reached,
            };
            let Some(near) = near else {
                return Ok(empty(RecallFound::NotSearched, said));
            };
            let hits: Vec<Hit> = near
                .iter()
                .map(|(bead, distance)| Hit {
                    bead: *bead,
                    // Nearest first, as a number that is higher for better.
                    rank: 1.0 - distance,
                    by: MatchedBy::Meaning,
                    distance: Some(*distance),
                })
                .collect();
            return answer(tx, query, &scopes, hits, said);
        }

        // By the words. Put together with a search by meaning, the words
        // give more matches than the answer holds; alone, as many as it
        // holds.
        let depth = match near {
            Some(_) => fused_depth(query.limit),
            None => i64::from(query.limit),
        };
        let (words, worded) = match worded(&mut tx, query, &scopes, depth, limits)? {
            Worded::Found(words) => (Some(words), RecallWords::Used),
            Worded::NoTerms => (None, RecallWords::NoTerms),
            Worded::TooSlow => (None, RecallWords::Unavailable),
        };
        let said = Searched {
            meaning,
            words: worded,
            compared: reached,
        };
        let hits: Vec<Hit> = match (words, near) {
            (None, None) => {
                let found = match worded {
                    RecallWords::NoTerms => RecallFound::NoTerms,
                    _ => RecallFound::NotSearched,
                };
                return Ok(empty(found, said));
            }
            (Some(words), Some(near)) => fuse(&words, &near, query.limit as usize),
            (None, Some(near)) => fuse(&[], &near, query.limit as usize),
            (Some(words), None) => words
                .iter()
                .map(|(bead, rank)| Hit {
                    bead: *bead,
                    rank: *rank,
                    by: MatchedBy::Words,
                    distance: None,
                })
                .collect(),
        };
        answer(tx, query, &scopes, hits, said)
    }
}

/// The answer that `hits`, best first, make: each match, what stands in
/// the place of one that is no longer current, what a judge related to
/// them, and the arrows between all of them.
fn answer(
    mut tx: Transaction<'_>,
    query: &RecallQuery,
    scopes: &Option<Vec<String>>,
    hits: Vec<Hit>,
    said: Searched,
) -> Result<RecallAnswer, StoreError> {
    if hits.is_empty() {
        return Ok(empty(RecallFound::NoMatch, said));
    }
    let matched = hits;
    let ids: Vec<Uuid> = matched.iter().map(|hit| hit.bead).collect();

    // The bead whose arrow ended each match that is no longer current.
    let enders: BTreeMap<Uuid, Uuid> = tx
        .query(
            &format!(
                "SELECT e.to_bead, e.from_bead FROM core_memory.edges AS e
                      WHERE e.to_bead = ANY ($1) AND e.arrow IN {ENDING}"
            ),
            &[&ids],
        )
        .map_err(backend)?
        .iter()
        .map(|r| (r.get(0), r.get(1)))
        .collect();
    let ended: Vec<Uuid> = ids
        .iter()
        .copied()
        .filter(|id| enders.contains_key(id))
        .collect();
    let heads = successors(&mut tx, &ended, scopes)?;

    // Matches, best first, each followed by what now stands in its
    // place.
    let mut placed: Vec<Placed> = Vec::new();
    let mut seen: BTreeSet<Uuid> = BTreeSet::new();
    let matched_ids: BTreeSet<Uuid> = ids.iter().copied().collect();
    for Hit {
        bead,
        rank,
        by,
        distance,
    } in &matched
    {
        if (!enders.contains_key(bead) || query.include_ended) && seen.insert(*bead) {
            placed.push(Placed {
                bead: *bead,
                via: RecallVia::Match {
                    rank: *rank,
                    by: *by,
                    distance: *distance,
                },
            });
        }
        if let Some(head) = heads.get(bead) {
            // A successor that matched too is listed as a match.
            if !matched_ids.contains(head) && seen.insert(*head) {
                placed.push(Placed {
                    bead: *head,
                    via: RecallVia::Successor { of: BeadId(*bead) },
                });
            }
        }
    }

    // What a judge related to them, up to `follow` steps away. A match
    // left out because it was ended is not brought back that way, and
    // nothing is reached through it.
    let seeds: Vec<Uuid> = placed.iter().map(|p| p.bead).collect();
    let left_out: Vec<Uuid> = ended
        .iter()
        .copied()
        .filter(|id| !seen.contains(id))
        .collect();
    let mut linked_omitted = 0;
    if query.follow > 0 && !seeds.is_empty() {
        let rows = tx
            .query(
                "WITH RECURSIVE reach (bead, steps, onward) AS (
                         SELECT s, 0, true FROM pg_catalog.unnest($1::uuid[]) AS s
                       UNION
                         SELECT n.bead,
                                r.steps + CASE WHEN b.kind = 'group' THEN 0 ELSE 1 END,
                                n.arrow NOT LIKE '!%'
                           FROM reach AS r
                           CROSS JOIN LATERAL (
                               SELECT e.to_bead AS bead, e.arrow FROM core_memory.edges AS e
                                WHERE e.from_bead = r.bead
                               UNION ALL
                               SELECT e.from_bead, e.arrow FROM core_memory.edges AS e
                                WHERE e.to_bead = r.bead
                           ) AS n
                           JOIN core_memory.beads AS b ON b.bead_id = n.bead
                          WHERE r.steps < $2 AND r.onward AND b.state = 'live'
                            AND NOT (n.bead = ANY ($5))
                            AND (b.kind = 'group' OR $3::text[] IS NULL OR b.scope = ANY ($3))
                     )
                     SELECT r.bead, min(r.steps), pg_catalog.count(*) OVER ()
                       FROM reach AS r
                       JOIN core_memory.beads AS b ON b.bead_id = r.bead
                      WHERE NOT (r.bead = ANY ($1))
                      GROUP BY r.bead, b.time_at, b.seq
                      ORDER BY 2, b.time_at DESC, b.seq DESC
                      LIMIT $4",
                &[
                    &seeds,
                    &to_i32_steps(query.follow),
                    &scopes,
                    &i64::from(RECALL_MAX_LINKED),
                    &left_out,
                ],
            )
            .map_err(backend)?;
        if let Some(first) = rows.first() {
            let total = to_u64(first.get(2), "beads reached")?;
            linked_omitted =
                u32::try_from(total.saturating_sub(rows.len() as u64)).unwrap_or(u32::MAX);
        }
        for row in &rows {
            placed.push(Placed {
                bead: row.get(0),
                via: RecallVia::Linked {
                    steps: to_u32(row.get(1), "steps")?,
                },
            });
        }
    }

    let beads = describe(&mut tx, &placed, scopes)?;
    let all: Vec<Uuid> = placed.iter().map(|p| p.bead).collect();
    let arrows = tx
        .query(
            "SELECT e.from_bead, e.to_bead, e.arrow, e.edge_type FROM core_memory.edges AS e
                  WHERE e.from_bead = ANY ($1) AND e.to_bead = ANY ($1)
                  ORDER BY e.workspace_id, e.seq",
            &[&all],
        )
        .map_err(backend)?
        .iter()
        .map(|r| {
            let arrow: &str = r.get(2);
            let edge_type: &str = r.get(3);
            Ok(RecalledArrow {
                from_bead: BeadId(r.get(0)),
                to_bead: BeadId(r.get(1)),
                arrow: Arrow::from_name(arrow)
                    .ok_or_else(|| corrupt(format!("unknown arrow `{arrow}`")))?,
                edge_type: EdgeType::from_name(edge_type)
                    .ok_or_else(|| corrupt(format!("unknown edge type `{edge_type}`")))?,
            })
        })
        .collect::<Result<Vec<_>, StoreError>>()?;
    tx.commit().map_err(backend)?;
    Ok(RecallAnswer {
        found: RecallFound::Matched,
        beads,
        arrows,
        linked_omitted,
        meaning: said.meaning,
        words: said.words,
        compared: said.compared,
    })
}

fn empty(found: RecallFound, said: Searched) -> RecallAnswer {
    RecallAnswer {
        found,
        beads: Vec::new(),
        arrows: Vec::new(),
        linked_omitted: 0,
        meaning: said.meaning,
        words: said.words,
        compared: said.compared,
    }
}

fn to_i32_steps(follow: u32) -> i32 {
    i32::try_from(follow).unwrap_or(i32::MAX)
}

/// For each ended bead, the current end of the chain that ended it, when
/// that bead is live and in a scope the caller may recall from. A chain
/// never branches, so it has one end.
fn successors(
    tx: &mut Transaction<'_>,
    ended: &[Uuid],
    scopes: &Option<Vec<String>>,
) -> Result<BTreeMap<Uuid, Uuid>, StoreError> {
    if ended.is_empty() {
        return Ok(BTreeMap::new());
    }
    let rows = tx
        .query(
            &format!(
                "WITH RECURSIVE chain (start, bead, depth) AS (
                     SELECT s, s, 0 FROM pg_catalog.unnest($1::uuid[]) AS s
                   UNION ALL
                     SELECT chain.start, e.from_bead, chain.depth + 1
                       FROM chain
                       JOIN core_memory.edges AS e
                         ON e.to_bead = chain.bead AND e.arrow IN {ENDING}
                      WHERE chain.depth < 100000
                 ),
                 ends AS (
                     SELECT DISTINCT ON (chain.start) chain.start, chain.bead
                       FROM chain
                      ORDER BY chain.start, chain.depth DESC
                 )
                 SELECT ends.start, ends.bead
                   FROM ends
                   JOIN core_memory.beads AS b ON b.bead_id = ends.bead
                  WHERE b.state = 'live' AND ends.bead <> ends.start
                    AND ($2::text[] IS NULL OR b.scope = ANY ($2))"
            ),
            &[&ended, scopes],
        )
        .map_err(backend)?;
    Ok(rows.iter().map(|r| (r.get(0), r.get(1))).collect())
}

/// What a bead was read from, and how much text it holds.
struct Detail {
    kind: BeadKind,
    event: Option<RecalledEvent>,
    time_at: Timestamp,
    time_basis: TimeBasis,
    ended_by: Option<(Uuid, bool)>,
    disputed: bool,
    type_key: Option<String>,
    text_bytes: u64,
}

/// The placed beads with their events, currency, disputes, types and, as
/// far as the answer has room, their text.
fn describe(
    tx: &mut Transaction<'_>,
    placed: &[Placed],
    scopes: &Option<Vec<String>>,
) -> Result<Vec<RecalledBead>, StoreError> {
    let ids: Vec<Uuid> = placed.iter().map(|p| p.bead).collect();
    let rows = tx
        .query(
            &format!(
                "SELECT b.bead_id, b.kind, b.time_at, b.time_basis, b.event_id,
                        ev.scope, ev.source_ns, ev.external_id, ev.revision,
                        ender.from_bead,
                        ($2::text[] IS NULL OR eb.scope = ANY ($2)),
                        ender.from_bead IS NULL AND EXISTS (
                            SELECT 1 FROM core_memory.edges AS d
                              JOIN core_memory.beads AS o
                                ON o.bead_id = CASE WHEN d.from_bead = b.bead_id
                                                    THEN d.to_bead ELSE d.from_bead END
                             WHERE (d.from_bead = b.bead_id OR d.to_bead = b.bead_id)
                               AND d.arrow IN {DISPUTING} AND o.state = 'live'
                               AND NOT EXISTS (
                                   SELECT 1 FROM core_memory.edges AS oe
                                    WHERE oe.to_bead = o.bead_id AND oe.arrow IN {ENDING})),
                        (SELECT r.type_key FROM core_memory.chunk_reads AS r
                          WHERE b.kind = 'chunk' AND r.chunk_id = b.chunk_id
                          ORDER BY r.seq DESC LIMIT 1),
                        coalesce((SELECT sum(pg_catalog.octet_length(t.text))
                                    FROM core_memory.chunks AS c
                                    JOIN core_memory.chunk_text AS t ON t.chunk_id = c.chunk_id
                                   WHERE (b.kind = 'chunk' AND c.chunk_id = b.chunk_id)
                                      OR (b.kind = 'tool' AND c.event_id = b.event_id)), 0)
                   FROM core_memory.beads AS b
                   LEFT JOIN core_memory.events AS ev ON ev.event_id = b.event_id
                   LEFT JOIN core_memory.edges AS ender
                     ON ender.to_bead = b.bead_id AND ender.arrow IN {ENDING}
                   LEFT JOIN core_memory.beads AS eb ON eb.bead_id = ender.from_bead
                  WHERE b.bead_id = ANY ($1)"
            ),
            &[&ids, scopes],
        )
        .map_err(backend)?;
    let mut details: BTreeMap<Uuid, Detail> = BTreeMap::new();
    for row in &rows {
        let bead: Uuid = row.get(0);
        let kind: &str = row.get(1);
        let kind = BeadKind::from_name(kind)
            .ok_or_else(|| corrupt(format!("bead {bead} has unknown kind `{kind}`")))?;
        let basis: &str = row.get(3);
        let event = match row.get::<_, Option<Uuid>>(4) {
            None => None,
            Some(event_id) => Some(RecalledEvent {
                event_id: EventId(event_id),
                key: EventKey {
                    scope: row
                        .get::<_, Option<String>>(5)
                        .ok_or_else(|| corrupt(format!("bead {bead} has no event")))?,
                    source_ns: row
                        .get::<_, Option<String>>(6)
                        .ok_or_else(|| corrupt(format!("live bead {bead} has no event key")))?,
                    external_id: row
                        .get::<_, Option<String>>(7)
                        .ok_or_else(|| corrupt(format!("live bead {bead} has no event key")))?,
                    revision: row.get(8),
                },
            }),
        };
        details.insert(
            bead,
            Detail {
                kind,
                event,
                time_at: Timestamp(row.get::<_, OffsetDateTime>(2)),
                time_basis: TimeBasis::from_name(basis)
                    .ok_or_else(|| corrupt(format!("unknown time basis `{basis}`")))?,
                ended_by: row
                    .get::<_, Option<Uuid>>(9)
                    .map(|ender| (ender, row.get::<_, Option<bool>>(10).unwrap_or(false))),
                disputed: row.get::<_, Option<bool>>(11).unwrap_or(false),
                type_key: row.get(12),
                text_bytes: to_u64(row.get(13), "text length")?,
            },
        );
    }

    // Text goes to the beads in the answer's order, as long as it fits.
    let mut room = RECALL_MAX_TEXT_BYTES;
    let mut with_text: Vec<Uuid> = Vec::new();
    let mut omitted: BTreeSet<Uuid> = BTreeSet::new();
    for p in placed {
        let detail = details
            .get(&p.bead)
            .ok_or_else(|| corrupt(format!("bead {} was not found again", p.bead)))?;
        if detail.kind == BeadKind::Group {
            continue;
        }
        if detail.text_bytes <= room {
            room -= detail.text_bytes;
            with_text.push(p.bead);
        } else {
            omitted.insert(p.bead);
        }
    }
    let mut texts: BTreeMap<Uuid, Vec<RecalledChunk>> = BTreeMap::new();
    if !with_text.is_empty() {
        let rows = tx
            .query(
                "SELECT b.bead_id, c.chunk_id, c.ordinal, t.text
                   FROM core_memory.beads AS b
                   JOIN core_memory.chunks AS c ON c.chunk_id = b.chunk_id
                   JOIN core_memory.chunk_text AS t ON t.chunk_id = c.chunk_id
                  WHERE b.bead_id = ANY ($1) AND b.kind = 'chunk'
                 UNION ALL
                 SELECT b.bead_id, c.chunk_id, c.ordinal, t.text
                   FROM core_memory.beads AS b
                   JOIN core_memory.chunks AS c ON c.event_id = b.event_id
                   JOIN core_memory.chunk_text AS t ON t.chunk_id = c.chunk_id
                  WHERE b.bead_id = ANY ($1) AND b.kind = 'tool'
                  ORDER BY 1, 3",
                &[&with_text],
            )
            .map_err(backend)?;
        for row in &rows {
            texts.entry(row.get(0)).or_default().push(RecalledChunk {
                chunk_id: ChunkId(row.get(1)),
                ordinal: to_u32(row.get(2), "chunk ordinal")?,
                text: row.get(3),
            });
        }
    }

    let mut beads = Vec::with_capacity(placed.len());
    for p in placed {
        let Some(detail) = details.remove(&p.bead) else {
            return Err(corrupt(format!("bead {} was not found again", p.bead)));
        };
        let chunks = texts.remove(&p.bead).unwrap_or_default();
        // A bead of source content always has its text while it is live.
        if detail.kind != BeadKind::Group && !omitted.contains(&p.bead) && chunks.is_empty() {
            return Err(corrupt(format!("live bead {} has no text", p.bead)));
        }
        beads.push(RecalledBead {
            bead_id: BeadId(p.bead),
            kind: detail.kind,
            event: detail.event,
            time_at: detail.time_at,
            time_basis: detail.time_basis,
            via: p.via.clone(),
            current: detail.ended_by.is_none(),
            ended_by: detail
                .ended_by
                .and_then(|(ender, readable)| readable.then_some(BeadId(ender))),
            disputed: detail.disputed,
            type_key: detail.type_key,
            chunks,
            text_omitted: omitted.contains(&p.bead),
        });
    }
    Ok(beads)
}

#[cfg(test)]
mod tests {
    use core_memory_contract::MatchedBy;
    use uuid::Uuid;

    use super::{SEARCHED, fuse, fused_depth};

    #[test]
    fn two_searches_are_put_together_by_place_and_never_by_score() {
        let bead = |n: u128| Uuid::from_u128(n);
        // The words' scores and the distances measure different things:
        // only each bead's place in its list counts.
        let words = [(bead(1), 900.0), (bead(2), 0.5), (bead(3), 0.1)];
        let near = [(bead(4), 0.01), (bead(3), 0.2), (bead(5), 0.9)];
        let fused = fuse(&words, &near, 10);
        let order: Vec<(Uuid, MatchedBy)> = fused.iter().map(|hit| (hit.bead, hit.by)).collect();
        assert_eq!(
            order,
            [
                // Third by the words and second by meaning: found by both,
                // it outranks what either search put first.
                (bead(3), MatchedBy::Both),
                // First in each. The one whose words matched goes first.
                (bead(1), MatchedBy::Words),
                (bead(4), MatchedBy::Meaning),
                (bead(2), MatchedBy::Words),
                (bead(5), MatchedBy::Meaning),
            ]
        );
        assert!((fused[0].rank - (1.0 / 63.0 + 1.0 / 62.0)).abs() < 1e-6);
        assert!((fused[1].rank - 1.0 / 61.0).abs() < 1e-6);
        // A match by meaning says how far it is; one by the words has no
        // distance to give.
        assert_eq!(fused[0].distance, Some(0.2));
        assert_eq!(fused[1].distance, None);
        assert_eq!(fused[2].distance, Some(0.01));
        // The answer holds as many as were asked for, the best of them.
        let fewer = fuse(&words, &near, 2);
        assert_eq!(
            fewer.iter().map(|hit| hit.bead).collect::<Vec<_>>(),
            [bead(3), bead(1)]
        );
        // One search finding nothing leaves the other's order as it was.
        let alone = fuse(&[], &near, 10);
        assert_eq!(
            alone.iter().map(|hit| hit.bead).collect::<Vec<_>>(),
            [bead(4), bead(3), bead(5)]
        );
        assert!(fuse(&[], &[], 10).is_empty());
        assert_eq!(fused_depth(10), 30);
        assert_eq!(fused_depth(50), 150);
    }

    #[test]
    fn the_search_is_the_expression_the_index_holds() {
        // A query can use an expression index only if it computes the same
        // expression. A difference would leave recall reading every chunk.
        let squeeze = |s: &str| s.split_whitespace().collect::<String>();
        let migration = squeeze(include_str!("../sql/0002_recall.sql"));
        assert!(migration.contains(&squeeze(&SEARCHED.replace("t.text", "text"))));
    }
}
