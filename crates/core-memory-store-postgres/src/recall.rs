//! Recall: a read of the stored text and of what a judge said of it.
//!
//! Recall keeps nothing of its own. It reads `chunk_text`, the one copy of
//! source text, through the full-text index of migration 2, and `edges`,
//! which hold what a judge said as IDs. An erase therefore reaches
//! everything recall could return, in the erase's own transaction.
//!
//! Every statement here runs in one read-only transaction on one
//! snapshot, which takes `schema_migrations` before anything else.

use std::collections::{BTreeMap, BTreeSet};

use core_memory_contract::{
    Arrow, BeadId, BeadKind, ChunkId, EdgeType, EventId, EventKey, RECALL_MAX_LINKED,
    RECALL_MAX_TEXT_BYTES, RecallAnswer, RecallFound, RecallVia, RecalledArrow, RecalledBead,
    RecalledChunk, RecalledEvent, TimeBasis, Timestamp,
};
use core_memory_core::{RecallQuery, ScopeGrants, StoreError};
use postgres::Transaction;
use time::OffsetDateTime;
use uuid::Uuid;

use crate::error::{backend, corrupt};
use crate::store::{PostgresStore, read_transaction, to_u32, to_u64};

/// The words of a chunk's text as the index of migration 2 holds them. A
/// query must use this expression exactly, or it cannot use the index.
const SEARCHED: &str = "pg_catalog.to_tsvector('pg_catalog.english'::pg_catalog.regconfig, \
                        pg_catalog.\"left\"(t.text, 100000))";
/// The caller's words as a query, read as a web search reads them.
const QUERY: &str =
    "pg_catalog.websearch_to_tsquery('pg_catalog.english'::pg_catalog.regconfig, $1)";
/// Arrows that end the currency of the bead they point at.
const ENDING: &str = "(SELECT a.arrow FROM core_memory.arrows AS a WHERE a.ends_currency)";
/// Arrows that say two beads cannot both hold.
const DISPUTING: &str = "(SELECT a.arrow FROM core_memory.arrows AS a WHERE a.disputes)";

/// One bead's place in an answer, before its details are read.
struct Placed {
    bead: Uuid,
    via: RecallVia,
}

impl PostgresStore {
    pub(crate) fn read_recall(&mut self, query: &RecallQuery) -> Result<RecallAnswer, StoreError> {
        let mut tx = read_transaction(self.client()?)?;
        let scopes: Option<Vec<String>> = match &query.scopes {
            ScopeGrants::All => None,
            ScopeGrants::Only(named) => Some(named.iter().cloned().collect()),
        };

        // A query of common words alone asks for nothing, and PostgreSQL
        // would match nothing with it. One of words to leave out alone, or
        // one where a word to leave out is an alternative, names no word the
        // index can look up: PostgreSQL would read every chunk to answer
        // it (`querytree` says `T`). Neither is searched, and saying so is
        // the honest answer.
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
            return Ok(empty(RecallFound::NoTerms));
        }

        let matches = tx
            .query(
                &format!(
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
                ),
                &[&query.query, &scopes, &i64::from(query.limit)],
            )
            .map_err(backend)?;
        if matches.is_empty() {
            return Ok(empty(RecallFound::NoMatch));
        }
        let matched: Vec<(Uuid, f32)> = matches.iter().map(|r| (r.get(0), r.get(1))).collect();
        let ids: Vec<Uuid> = matched.iter().map(|(id, _)| *id).collect();

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
        let heads = successors(&mut tx, &ended, &scopes)?;

        // Matches, best first, each followed by what now stands in its
        // place.
        let mut placed: Vec<Placed> = Vec::new();
        let mut seen: BTreeSet<Uuid> = BTreeSet::new();
        let matched_ids: BTreeSet<Uuid> = ids.iter().copied().collect();
        for (bead, rank) in &matched {
            if (!enders.contains_key(bead) || query.include_ended) && seen.insert(*bead) {
                placed.push(Placed {
                    bead: *bead,
                    via: RecallVia::Match { rank: *rank },
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
        // nothing is reached through it. A group costs no
        // step, so one next to a bead at the limit is reached too.
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
                          WHERE (r.steps < $2 OR b.kind = 'group') AND r.onward AND b.state = 'live'
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

        let beads = describe(&mut tx, &placed, &scopes)?;
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
        })
    }
}

fn empty(found: RecallFound) -> RecallAnswer {
    RecallAnswer {
        found,
        beads: Vec::new(),
        arrows: Vec::new(),
        linked_omitted: 0,
    }
}

fn to_i32_steps(follow: u32) -> i32 {
    i32::try_from(follow).unwrap_or(i32::MAX)
}

/// For each ended bead, the current end of the chain that ended it, when
/// that bead is live and in a scope the caller may recall from. A chain
/// with a bead in a scope the caller may not recall from, anywhere along it,
/// has no successor to show: the walk stops at that bead, and goes no further
/// through that scope. A chain
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
                "WITH RECURSIVE chain (start, bead, depth, granted) AS (
                     SELECT s, s, 0, true FROM pg_catalog.unnest($1::uuid[]) AS s
                   UNION ALL
                     SELECT chain.start, e.from_bead, chain.depth + 1,
                            chain.granted
                            AND (nb.kind = 'group' OR $2::text[] IS NULL OR nb.scope = ANY ($2))
                       FROM chain
                       JOIN core_memory.edges AS e
                         ON e.to_bead = chain.bead AND e.arrow IN {ENDING}
                       JOIN core_memory.beads AS nb ON nb.bead_id = e.from_bead
                      WHERE chain.granted AND chain.depth < 100000
                 ),
                 ends AS (
                     SELECT DISTINCT ON (chain.start) chain.start, chain.bead, chain.granted
                       FROM chain
                      ORDER BY chain.start, chain.depth DESC
                 )
                 SELECT ends.start, ends.bead
                   FROM ends
                   JOIN core_memory.beads AS b ON b.bead_id = ends.bead
                  WHERE b.state = 'live' AND ends.bead <> ends.start AND ends.granted
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
    use super::SEARCHED;

    #[test]
    fn the_search_is_the_expression_the_index_holds() {
        // A query can use an expression index only if it computes the same
        // expression. A difference would leave recall reading every chunk.
        let squeeze = |s: &str| s.split_whitespace().collect::<String>();
        let migration = squeeze(include_str!("../sql/0002_recall.sql"));
        assert!(migration.contains(&squeeze(&SEARCHED.replace("t.text", "text"))));
    }
}
