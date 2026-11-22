//! The judge's store calls: what is claimed for a read, what a read may be
//! shown, and how what it said is stored.
//!
//! A judge reads for a workspace as a whole: it is shown beads of every
//! scope of the workspace that allows it. So these calls start with
//! `core_memory.begin_workspace_write`, which holds the schema and makes
//! the transaction the workspace's one writer, and decide scope by scope,
//! under that lock, what may be read and what may be stored.

use std::collections::{BTreeMap, BTreeSet};

use core_memory_contract::{
    Arrow, BeadId, BeadRecord, CallId, ChunkFacets, Direction, EdgeRecord, EdgeType, EventDetails,
    EventKey, IdKey, JudgeRef, MadeBead, NameLinkRecord, PairColumns, ReaderRef, TimeBasis,
    Timestamp, ToolStatus, Verdict, VerdictNote, VerdictRecord,
};
use core_memory_core::StoreError;
use core_memory_core::store::{
    ChainWrite, Change, JudgeBatch, JudgeClaim, JudgePermit, JudgePermitRequest, JudgeWork,
    JudgmentFinish, JudgmentFinished, JudgmentWrite, LeasedBead, Purpose, ScopeState, Sending,
    Shown, ShownBead, ShownContent, WindowOf, Withheld,
};
use postgres::GenericClient;
use postgres::types::Json;
use time::{Duration, OffsetDateTime};
use uuid::Uuid;

use crate::error::{backend, corrupt};
use crate::migrate::SCHEMA_VERSION;
use crate::store::{
    PostgresStore, QueuedReads, begin_write, digest, hold_schema, set_aside_spent, to_i32, to_i64,
    to_u32, to_u64, update_work, write_transaction,
};

/// Work that may be handed out now: ready, or leased to a worker whose
/// lease ran out.
const DUE: &str = "(w.state = 'ready' OR (w.state = 'leased' AND w.leased_until < $1))
    AND w.not_before <= $1";
/// A bead whose judge can be shown what it needs. A tool call's bead is
/// queued once a model has written about the call. A chunk's bead waits
/// for the chunk to be read, so the judge is shown its type, but not for
/// ever, and not at all when nothing reads.
const READY: &str = "(w.kind = 'second_read' OR b.kind = 'tool' OR NOT $2 OR w.enqueued_at <= $3
    OR EXISTS (SELECT 1 FROM core_memory.chunk_reads AS r WHERE r.chunk_id = w.item_id))";
/// A judge is reading in the workspace: it holds work whose lease has not
/// run out.
const BUSY: &str = "EXISTS (
    SELECT 1 FROM core_memory.work AS busy
     WHERE busy.workspace_id = w.workspace_id AND busy.kind IN ('judge', 'second_read')
       AND busy.state = 'leased' AND busy.leased_until >= $1)";

/// Starts a transaction's writes for a workspace as a whole. The store
/// refuses an engine built for another schema version and makes the
/// transaction the only writer of the workspace until it ends. With
/// `take`, the workspace gives up that many numbers from its change
/// counter, and the first is returned.
fn begin_workspace_write<C: GenericClient>(
    tx: &mut C,
    workspace: &str,
    take: Option<i32>,
) -> Result<Option<i64>, StoreError> {
    let row = tx
        .query_one(
            "SELECT core_memory.begin_workspace_write($1, $2, $3)",
            &[&workspace, &(SCHEMA_VERSION as i32), &take],
        )
        .map_err(backend)?;
    Ok(row.get(0))
}

/// `at` moved by `millis`, or `at` itself where the calendar ends.
fn moved(at: OffsetDateTime, millis: u64, later: bool) -> OffsetDateTime {
    let by = Duration::milliseconds(i64::try_from(millis).unwrap_or(i64::MAX));
    let moved = if later {
        at.checked_add(by)
    } else {
        at.checked_sub(by)
    };
    moved.unwrap_or(at)
}

/// How many results a query may give, from a count the caller chose.
fn at_most(count: u32) -> i64 {
    i64::from(count)
}

/// A bead as stored, and whether it may be shown now.
struct Loaded {
    /// What keeps it from being shown, if anything does.
    withheld: Option<Withheld>,
    /// Whether it is waiting to be judged itself.
    pending: bool,
    /// The bead as a judge is shown it. `None` if there is nothing to show.
    shown: Option<ShownBead>,
}

/// Reads beads of source content of one workspace by ID, with everything a
/// judge is shown of them and what decides whether it may be.
fn load_beads<C: GenericClient>(
    tx: &mut C,
    workspace: &str,
    destination: &str,
    ids: &[Uuid],
) -> Result<BTreeMap<Uuid, Loaded>, StoreError> {
    if ids.is_empty() {
        return Ok(BTreeMap::new());
    }
    let rows = tx
        .query(
            "SELECT b.bead_id, b.scope, b.seq, b.time_at, b.time_basis, b.kind, b.state,
                    s.state,
                    EXISTS (SELECT 1 FROM core_memory.scope_destinations AS d
                             WHERE d.scope = b.scope AND d.destination = $3
                               AND d.state = 'granted'),
                    e.source_ns, e.external_id, e.revision, e.info, e.chunk_count,
                    c.ordinal, t.text, t.facets,
                    (SELECT r.type_key FROM core_memory.chunk_reads AS r
                      WHERE r.chunk_id = b.chunk_id ORDER BY r.seq DESC LIMIT 1),
                    o.status, o.action, o.outcome,
                    NOT EXISTS (SELECT 1 FROM core_memory.edges AS x
                                 WHERE x.to_bead = b.bead_id
                                   AND x.arrow IN ('repl', 'withdraws')),
                    (SELECT x.arrow FROM core_memory.edges AS x
                      WHERE x.from_bead = b.bead_id AND x.arrow IN ('repl', 'withdraws')
                      ORDER BY x.seq LIMIT 1),
                    (SELECT g.from_bead
                       FROM core_memory.edges AS g
                       JOIN core_memory.beads AS gb ON gb.bead_id = g.from_bead
                      WHERE g.to_bead = b.bead_id AND g.arrow = 'consists'
                        AND gb.state = 'live'
                      ORDER BY g.seq LIMIT 1),
                    EXISTS (SELECT 1 FROM core_memory.work AS w
                             WHERE w.kind = 'judge' AND w.item_id = b.bead_id)
               FROM core_memory.beads AS b
               JOIN core_memory.scopes AS s ON s.scope = b.scope
               JOIN core_memory.events AS e ON e.event_id = b.event_id
               LEFT JOIN core_memory.chunks AS c ON c.chunk_id = b.chunk_id
               LEFT JOIN core_memory.chunk_text AS t ON t.chunk_id = b.chunk_id
               LEFT JOIN LATERAL (
                   SELECT x.status, x.action, x.outcome
                     FROM core_memory.tool_outcomes AS x
                    WHERE x.event_id = b.event_id
                    ORDER BY x.seq DESC LIMIT 1) AS o ON b.kind = 'tool'
              WHERE b.workspace_id = $1 AND b.bead_id = ANY ($2)
                AND b.kind IN ('chunk', 'tool')",
            &[&workspace, &ids, &destination],
        )
        .map_err(backend)?;
    let mut loaded = BTreeMap::new();
    for row in &rows {
        let id: Uuid = row.get(0);
        let scope: String = row.get(1);
        let live = row.get::<_, &str>(6) == "live";
        let active = row.get::<_, &str>(7) == ScopeState::Active.as_str();
        let consents: bool = row.get(8);
        let text: Option<String> = row.get(15);
        let outcome: (Option<&str>, Option<String>, Option<String>) =
            (row.get(18), row.get(19), row.get(20));
        let details: Option<Json<EventDetails>> = row.try_get(12).map_err(|e| {
            corrupt(format!(
                "the metadata of bead {id}'s event does not read back: {e}"
            ))
        })?;
        // What there is to show: a chunk's text, or what a model wrote
        // about a tool call. An erased bead has neither.
        let content = match (live, row.get::<_, &str>(5), text, outcome, &details) {
            (true, "chunk", Some(text), _, _) => Some(ShownContent::Text(text)),
            (true, "tool", _, (Some(status), Some(action), Some(outcome)), Some(Json(details))) => {
                let tool = details.tool_call.as_ref().map(|call| call.tool.clone());
                let status = ToolStatus::from_name(status)
                    .ok_or_else(|| corrupt(format!("unknown tool status `{status}`")))?;
                tool.map(|tool| ShownContent::ToolCall {
                    tool,
                    action,
                    outcome,
                    status,
                })
            }
            _ => None,
        };
        let shown = match (content, details) {
            (Some(content), Some(Json(details))) => {
                let basis: &str = row.get(4);
                let facets: Option<Json<ChunkFacets>> = row.try_get(16).map_err(|e| {
                    corrupt(format!("the facets of bead {id} do not read back: {e}"))
                })?;
                let chain = row
                    .get::<_, Option<&str>>(22)
                    .map(|name| {
                        Arrow::from_name(name)
                            .ok_or_else(|| corrupt(format!("unknown arrow `{name}`")))
                    })
                    .transpose()?;
                let chunks: i32 = row.get(13);
                let part = match row.get::<_, Option<i32>>(14) {
                    Some(ordinal) if chunks > 1 => Some((
                        to_u32(ordinal, "chunk ordinal")? + 1,
                        to_u32(chunks, "chunk count")?,
                    )),
                    _ => None,
                };
                let missing = || corrupt(format!("bead {id} is live and its event has no key"));
                Some(ShownBead {
                    bead_id: BeadId(id),
                    scope: scope.clone(),
                    seq: to_u64(row.get(2), "change number")?,
                    time_at: Timestamp(row.get::<_, OffsetDateTime>(3)),
                    time_basis: TimeBasis::from_name(basis)
                        .ok_or_else(|| corrupt(format!("unknown time basis `{basis}`")))?,
                    content,
                    type_key: row.get(17),
                    is_head: row.get(21),
                    chain,
                    group: row.get::<_, Option<Uuid>>(23).map(BeadId),
                    part,
                    key: EventKey {
                        scope,
                        source_ns: row.get::<_, Option<String>>(9).ok_or_else(missing)?,
                        external_id: row.get::<_, Option<String>>(10).ok_or_else(missing)?,
                        revision: row.get(11),
                    },
                    details,
                    facets: facets.map(|Json(facets)| facets),
                })
            }
            _ => None,
        };
        let withheld = if shown.is_none() {
            Some(Withheld::NothingToShow)
        } else if !active {
            Some(Withheld::ScopeNotActive)
        } else if !consents {
            Some(Withheld::NoConsent)
        } else {
            None
        };
        loaded.insert(
            id,
            Loaded {
                withheld,
                pending: row.get(24),
                shown,
            },
        );
    }
    Ok(loaded)
}

/// A condition on a candidate bead `b` of the window: some new bead in
/// `$new` has not been read with it by the judge whose binding revision
/// is `$rev`. A bead this judge has already read with every new bead is
/// not shown again: that would read pairs twice. It happens when beads
/// stored before there was a judge are queued after later ones were read
/// beside them.
fn unread_with(new: &str, rev: &str) -> String {
    format!(
        "EXISTS (
             SELECT 1 FROM unnest({new}::uuid[]) AS n (id)
              WHERE NOT EXISTS (
                  SELECT 1 FROM core_memory.verdicts AS v
                    JOIN core_memory.judge_reads AS r ON r.call_id = v.call_id
                   WHERE r.read_no = 1 AND r.binding_rev = {rev}
                     AND ((v.earlier_bead = n.id AND v.later_bead = b.bead_id)
                          OR (v.later_bead = n.id AND v.earlier_bead = b.bead_id))))"
    )
}

/// How many beads of the events a new bead's source names are shown with
/// it, at most.
const NAMED_PER_BEAD: usize = 4;

/// The beads near the new ones that a judge may be shown, most wanted
/// first: the beads of open disputes, then the beads of the events the new
/// beads' sources name, then by distance in time.
fn near<C: GenericClient>(
    tx: &mut C,
    request: &JudgePermitRequest<'_>,
    new: &[ShownBead],
    asked: &[Uuid],
) -> Result<Vec<Uuid>, StoreError> {
    let WindowOf::Near {
        within_ms,
        max_beads,
        conflict_beads,
        ..
    } = request.window
    else {
        return Ok(Vec::new());
    };
    let (Some(first), Some(last)) = (new.first(), new.last()) else {
        return Ok(Vec::new());
    };
    let (from, to) = (first.time_at.0, last.time_at.0);
    let new_ids: Vec<Uuid> = new.iter().map(|bead| bead.bead_id.0).collect();
    let rev = request.reader.binding_rev.as_bytes().as_slice();
    let mut wanted: Vec<Uuid> = Vec::new();
    let want = |wanted: &mut Vec<Uuid>, id: Uuid| {
        if !asked.contains(&id) && !wanted.contains(&id) {
            wanted.push(id);
        }
    };

    // Open disputes, most recent first: two beads that contradict each
    // other while both are live and current.
    if conflict_beads > 0 {
        let rows = tx
            .query(
                "SELECT e.from_bead, e.to_bead
                   FROM core_memory.edges AS e
                   JOIN core_memory.beads AS a ON a.bead_id = e.from_bead
                   JOIN core_memory.beads AS b ON b.bead_id = e.to_bead
                  WHERE e.workspace_id = $1 AND e.arrow = '!contradicts'
                    AND a.state = 'live' AND b.state = 'live'
                    AND NOT EXISTS (
                        SELECT 1 FROM core_memory.edges AS x
                         WHERE x.to_bead IN (e.from_bead, e.to_bead)
                           AND x.arrow IN ('repl', 'withdraws'))
                  ORDER BY e.seq DESC
                  LIMIT $2",
                &[&request.workspace, &at_most(conflict_beads)],
            )
            .map_err(backend)?;
        let mut candidates: Vec<Uuid> = Vec::new();
        for row in &rows {
            for column in [0, 1] {
                let id: Uuid = row.get(column);
                if !candidates.contains(&id) {
                    candidates.push(id);
                }
            }
        }
        let unread: BTreeSet<Uuid> = tx
            .query(
                &format!(
                    "SELECT b.bead_id FROM unnest($1::uuid[]) AS b (bead_id) WHERE {}",
                    unread_with("$2", "$3")
                ),
                &[&candidates, &new_ids, &rev],
            )
            .map_err(backend)?
            .iter()
            .map(|row| row.get(0))
            .collect();
        for id in candidates {
            if wanted.len() < conflict_beads as usize && unread.contains(&id) {
                want(&mut wanted, id);
            }
        }
    }

    // What each new bead's source names: the event it is a revision of,
    // replies to or belongs under, in its own scope. The source says the
    // two are related, so they are shown however far apart in time they
    // are: the latest revision first, each in the order of its chunks, up
    // to NAMED_PER_BEAD for each new bead. Only beads that may be shown and
    // are not waiting to be judged themselves, as by time.
    for bead in new {
        let structure = &bead.details.structure;
        let mut taken = 0;
        for reference in [
            &structure.revision_of,
            &structure.reply_to,
            &structure.parent,
        ]
        .into_iter()
        .flatten()
        {
            if taken >= NAMED_PER_BEAD {
                break;
            }
            let source_ns = reference
                .source_ns
                .as_deref()
                .unwrap_or(&bead.key.source_ns);
            let rows = tx
                .query(
                    &format!(
                        "SELECT b.bead_id
                           FROM core_memory.events AS e
                           JOIN core_memory.beads AS b ON b.event_id = e.event_id
                           JOIN core_memory.scopes AS s ON s.scope = b.scope
                          WHERE e.scope = $1 AND e.source_ns = $2 AND e.external_id = $3
                            AND ($4::text IS NULL OR e.revision = $4)
                            AND e.state = 'live' AND b.workspace_id = $5
                            AND b.state = 'live' AND b.kind IN ('chunk', 'tool')
                            AND b.bead_id <> ALL ($6)
                            AND s.state = 'active'
                            AND EXISTS (
                                SELECT 1 FROM core_memory.scope_destinations AS d
                                 WHERE d.scope = b.scope AND d.destination = $7
                                   AND d.state = 'granted')
                            AND NOT EXISTS (
                                SELECT 1 FROM core_memory.work AS w
                                 WHERE w.kind = 'judge' AND w.item_id = b.bead_id)
                            AND (b.kind = 'chunk' OR EXISTS (
                                SELECT 1 FROM core_memory.tool_outcomes AS o
                                 WHERE o.event_id = b.event_id))
                            AND {unread}
                          ORDER BY e.seq DESC, b.seq
                          LIMIT $10",
                        unread = unread_with("$8", "$9")
                    ),
                    &[
                        &bead.key.scope,
                        &source_ns,
                        &reference.external_id,
                        &reference.revision,
                        &request.workspace,
                        &asked,
                        &request.destination,
                        &new_ids,
                        &rev,
                        &i64::try_from(NAMED_PER_BEAD - taken).unwrap_or(i64::MAX),
                    ],
                )
                .map_err(backend)?;
            for row in &rows {
                let id: Uuid = row.get(0);
                if !asked.contains(&id) && !wanted.contains(&id) {
                    wanted.push(id);
                    taken += 1;
                }
            }
        }
    }

    // The beads between the new ones, then those before and after them,
    // nearest first. Chosen by time alone, among beads that may be shown
    // and are not waiting to be judged themselves.
    let stretch = |tx: &mut C,
                   range: &str,
                   order: &str,
                   low: OffsetDateTime,
                   high: OffsetDateTime|
     -> Result<Vec<(Uuid, OffsetDateTime)>, StoreError> {
        let rows = tx
            .query(
                &format!(
                    "SELECT b.bead_id, b.time_at
                       FROM core_memory.beads AS b
                       JOIN core_memory.scopes AS s ON s.scope = b.scope
                      WHERE b.workspace_id = $1 AND {range}
                        AND b.state = 'live' AND b.kind IN ('chunk', 'tool')
                        AND b.bead_id <> ALL ($4)
                        AND s.state = 'active'
                        AND EXISTS (
                            SELECT 1 FROM core_memory.scope_destinations AS d
                             WHERE d.scope = b.scope AND d.destination = $5
                               AND d.state = 'granted')
                        AND NOT EXISTS (
                            SELECT 1 FROM core_memory.work AS w
                             WHERE w.kind = 'judge' AND w.item_id = b.bead_id)
                        AND (b.kind = 'chunk' OR EXISTS (
                            SELECT 1 FROM core_memory.tool_outcomes AS o
                             WHERE o.event_id = b.event_id))
                        AND {unread}
                      ORDER BY {order}
                      LIMIT $6",
                    unread = unread_with("$7", "$8")
                ),
                &[
                    &request.workspace,
                    &low,
                    &high,
                    &asked,
                    &request.destination,
                    &at_most(max_beads),
                    &new_ids,
                    &rev,
                ],
            )
            .map_err(backend)?;
        Ok(rows.iter().map(|row| (row.get(0), row.get(1))).collect())
    };
    let between = stretch(
        tx,
        "b.time_at >= $2 AND b.time_at <= $3",
        "b.seq DESC",
        from,
        to,
    )?;
    let before = stretch(
        tx,
        "b.time_at >= $2 AND b.time_at < $3",
        "b.time_at DESC, b.seq DESC",
        moved(from, within_ms, false),
        from,
    )?;
    let after = stretch(
        tx,
        "b.time_at > $2 AND b.time_at <= $3",
        "b.time_at, b.seq",
        to,
        moved(to, within_ms, true),
    )?;
    for (id, _) in between {
        want(&mut wanted, id);
    }
    let (mut before, mut after) = (before.into_iter().peekable(), after.into_iter().peekable());
    loop {
        let next = match (before.peek(), after.peek()) {
            (Some((_, earlier)), Some((_, later))) if from - *earlier <= *later - to => {
                before.next()
            }
            (Some(_), None) => before.next(),
            (_, Some(_)) => after.next(),
            (None, None) => break,
        };
        if let Some((id, _)) = next {
            want(&mut wanted, id);
        }
    }
    Ok(wanted)
}

/// What became of an arrow that ends currency which a read gave.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ChainFate {
    Written,
    Held,
    NotCurrent,
    Taken,
}

impl ChainFate {
    fn note(self) -> Option<VerdictNote> {
        match self {
            ChainFate::Written => None,
            ChainFate::Held => Some(VerdictNote::AwaitsSecondRead),
            ChainFate::NotCurrent => Some(VerdictNote::NotCurrent),
            ChainFate::Taken => Some(VerdictNote::ChainTaken),
        }
    }
}

/// The arrow a stored key names.
fn arrow_named(name: &str) -> Result<Arrow, StoreError> {
    Arrow::from_name(name).ok_or_else(|| corrupt(format!("unknown arrow `{name}`")))
}

/// Numbers from the workspace's change counter, handed out in order.
struct Numbers {
    next: i64,
    left: usize,
}

impl Numbers {
    fn take(&mut self) -> Result<i64, StoreError> {
        if self.left == 0 {
            return Err(corrupt(
                "a judgment needed more change numbers than it took",
            ));
        }
        self.left -= 1;
        self.next += 1;
        Ok(self.next - 1)
    }
}

impl PostgresStore {
    pub(crate) fn write_claim_judgment(
        &mut self,
        claim: &JudgeClaim,
    ) -> Result<Option<JudgeBatch>, StoreError> {
        // Work that was handed out as often as it may be is set aside in a
        // transaction of its own. That takes row locks, and a transaction
        // that holds row locks must not then wait for a workspace: an
        // erasure holds the workspace and waits for the rows.
        let mut tx = write_transaction(self.client()?)?;
        hold_schema(&mut tx)?;
        for kind in [Purpose::Judge, Purpose::SecondRead] {
            set_aside_spent(&mut tx, kind, claim.max_attempts, claim.now)?;
        }
        tx.commit().map_err(backend)?;

        let mut tx = write_transaction(self.client()?)?;
        hold_schema(&mut tx)?;
        let waits = claim.wait_for_reads_ms.is_some();
        let waited_since = moved(claim.now.0, claim.wait_for_reads_ms.unwrap_or(0), false);
        // A workspace with work that is due and no judge reading in it,
        // found without a lock. What is found is read again under the
        // workspace's lock before anything is done with it.
        let Some(found) = tx
            .query_opt(
                &format!(
                    "SELECT w.workspace_id
                       FROM core_memory.work AS w
                       JOIN core_memory.beads AS b ON b.bead_id = w.item_id
                      WHERE w.kind IN ('judge', 'second_read') AND {DUE} AND {READY}
                        AND NOT {BUSY}
                      ORDER BY w.not_before, w.enqueued_at, w.item_id
                      LIMIT 1"
                ),
                &[&claim.now.0, &waits, &waited_since],
            )
            .map_err(backend)?
        else {
            return Ok(None);
        };
        let workspace: String = found.get(0);
        begin_workspace_write(&mut tx, &workspace, None)?;
        // One judge reads in a workspace at a time, so that no pair is
        // read by two judges at once.
        let busy: bool = tx
            .query_one(
                "SELECT EXISTS (
                     SELECT 1 FROM core_memory.work AS busy
                      WHERE busy.workspace_id = $2 AND busy.kind IN ('judge', 'second_read')
                        AND busy.state = 'leased' AND busy.leased_until >= $1)",
                &[&claim.now.0, &workspace],
            )
            .map_err(backend)?
            .get(0);
        if busy {
            return Ok(None);
        }
        let id_key: Vec<u8> = tx
            .query_one(
                "SELECT id_key FROM core_memory.workspaces WHERE workspace_id = $1",
                &[&workspace],
            )
            .map_err(backend)?
            .get(0);
        let id_key = IdKey::from_slice(&id_key)
            .ok_or_else(|| corrupt("a workspace ID key is not 32 bytes"))?;
        let lease = |tx: &mut postgres::Transaction<'_>,
                     kind: Purpose,
                     ids: &[Uuid]|
         -> Result<BTreeMap<Uuid, u32>, StoreError> {
            let rows = tx
                .query(
                    "UPDATE core_memory.work
                        SET state = 'leased', leased_until = $1, attempts = attempts + 1
                      WHERE kind = $2 AND item_id = ANY ($3)
                  RETURNING item_id, attempts",
                    &[&claim.lease_until.0, &kind.as_str(), &ids],
                )
                .map_err(backend)?;
            rows.iter()
                .map(|row| Ok((row.get(0), to_u32(row.get(1), "attempts")?)))
                .collect()
        };

        // An arrow that is held comes first: until it is settled, what is
        // current in its chain is not. Arrows that share a bead are read
        // in the order they were held, so a chain is written from its
        // oldest statement on. One whose earlier neighbour was set aside
        // as failed does not wait for it.
        loop {
            let Some(row) = tx
                .query_opt(
                    &format!(
                        "SELECT w.item_id
                           FROM core_memory.work AS w
                           LEFT JOIN core_memory.pending_chains AS p
                             ON p.source_bead = w.item_id
                          WHERE w.workspace_id = $2 AND w.kind = 'second_read' AND {DUE}
                            AND NOT EXISTS (
                                SELECT 1
                                  FROM core_memory.pending_chains AS e
                                  JOIN core_memory.work AS ew
                                    ON ew.kind = 'second_read' AND ew.item_id = e.source_bead
                                 WHERE e.workspace_id = p.workspace_id
                                   AND e.ordinal < p.ordinal AND ew.state <> 'failed'
                                   AND (e.source_bead = ANY (p.head_beads)
                                        OR p.source_bead = ANY (e.head_beads)
                                        OR e.head_beads && p.head_beads))
                          ORDER BY p.ordinal NULLS FIRST, w.not_before, w.enqueued_at,
                                   w.item_id
                          LIMIT 1
                            FOR UPDATE OF w SKIP LOCKED"
                    ),
                    &[&claim.now.0, &workspace],
                )
                .map_err(backend)?
            else {
                break;
            };
            let source: Uuid = row.get(0);
            let Some(held) = tx
                .query_opt(
                    "SELECT p.arrow, p.head_beads,
                            (SELECT bool_and(b.state = 'live') FROM core_memory.beads AS b
                              WHERE b.bead_id = p.source_bead OR b.bead_id = ANY (p.head_beads)),
                            EXISTS (
                                SELECT 1 FROM core_memory.edges AS x
                                 WHERE x.arrow IN ('repl', 'withdraws')
                                   AND (x.to_bead = p.source_bead
                                        OR x.to_bead = ANY (p.head_beads))),
                            (SELECT e.arrow FROM core_memory.edges AS e
                              WHERE e.from_bead = p.source_bead
                                AND e.arrow IN ('repl', 'withdraws')
                              ORDER BY e.seq LIMIT 1),
                            (SELECT count(*) FROM core_memory.edges AS e
                              WHERE e.from_bead = p.source_bead
                                AND e.arrow IN ('repl', 'withdraws'))
                       FROM core_memory.pending_chains AS p
                      WHERE p.source_bead = $1",
                    &[&source],
                )
                .map_err(backend)?
            else {
                // The arrow is no longer held, so its second read is no
                // longer owed.
                tx.execute(
                    "DELETE FROM core_memory.work WHERE kind = 'second_read' AND item_id = $1",
                    &[&source],
                )
                .map_err(backend)?;
                continue;
            };
            let arrow = arrow_named(held.get(0))?;
            let heads: Vec<Uuid> = held.get(1);
            let live = held.get::<_, Option<bool>>(2).unwrap_or(false);
            let ended: bool = held.get(3);
            let carried = held
                .get::<_, Option<&str>>(4)
                .map(arrow_named)
                .transpose()?;
            let ends = usize::try_from(held.get::<_, i64>(5)).unwrap_or(usize::MAX);
            let room = carried
                .is_none_or(|carried| carried == arrow && ends.saturating_add(heads.len()) <= 2);
            if !live || ended || !room {
                // The arrow can no longer be written: a bead it is about
                // was replaced or withdrawn meanwhile, or its source ended
                // beads with an arrow it cannot join. No second read is
                // made for it, and its first verdict keeps its note.
                tx.execute(
                    "DELETE FROM core_memory.pending_chains WHERE source_bead = $1",
                    &[&source],
                )
                .map_err(backend)?;
                tx.execute(
                    "DELETE FROM core_memory.work WHERE kind = 'second_read' AND item_id = $1",
                    &[&source],
                )
                .map_err(backend)?;
                continue;
            }
            let attempts = lease(&mut tx, Purpose::SecondRead, &[source])?;
            tx.commit().map_err(backend)?;
            return Ok(Some(JudgeBatch {
                workspace,
                id_key,
                work: JudgeWork::SecondRead {
                    source: LeasedBead {
                        bead_id: BeadId(source),
                        attempts: attempts.get(&source).copied().unwrap_or(1),
                    },
                    arrow,
                    heads: heads.into_iter().map(BeadId).collect(),
                },
            }));
        }

        // New beads: the one that has waited longest, the earliest in time
        // among those that have waited as long, and those near it in time,
        // so that each lies in the window of the others.
        let Some(head) = tx
            .query_opt(
                &format!(
                    "SELECT w.item_id, w.alone, b.time_at
                       FROM core_memory.work AS w
                       JOIN core_memory.beads AS b ON b.bead_id = w.item_id
                      WHERE w.workspace_id = $4 AND w.kind = 'judge' AND {DUE} AND {READY}
                      ORDER BY w.not_before, w.enqueued_at, b.time_at, b.seq
                      LIMIT 1
                        FOR UPDATE OF w SKIP LOCKED"
                ),
                &[&claim.now.0, &waits, &waited_since, &workspace],
            )
            .map_err(backend)?
        else {
            // Committed, so that a second read that was no longer owed
            // stays removed.
            tx.commit().map_err(backend)?;
            return Ok(None);
        };
        let head_id: Uuid = head.get(0);
        let at: OffsetDateTime = head.get(2);
        let mut ids = vec![head_id];
        // A bead that was part of a read that failed is read alone from
        // then on.
        if !head.get::<_, bool>(1) && claim.max_new > 1 {
            let rest = tx
                .query(
                    &format!(
                        "SELECT w.item_id
                           FROM core_memory.work AS w
                           JOIN core_memory.beads AS b ON b.bead_id = w.item_id
                          WHERE w.workspace_id = $4 AND w.kind = 'judge' AND {DUE} AND {READY}
                            AND NOT w.alone AND w.item_id <> $5
                            AND b.time_at >= $6 AND b.time_at <= $7
                          ORDER BY greatest(b.time_at - $8, $8 - b.time_at), b.seq
                          LIMIT $9
                            FOR UPDATE OF w SKIP LOCKED"
                    ),
                    &[
                        &claim.now.0,
                        &waits,
                        &waited_since,
                        &workspace,
                        &head_id,
                        &moved(at, claim.within_ms, false),
                        &moved(at, claim.within_ms, true),
                        &at,
                        &at_most(claim.max_new - 1),
                    ],
                )
                .map_err(backend)?;
            ids.extend(rest.iter().map(|row| row.get::<_, Uuid>(0)));
        }
        let attempts = lease(&mut tx, Purpose::Judge, &ids)?;
        tx.commit().map_err(backend)?;
        Ok(Some(JudgeBatch {
            workspace,
            id_key,
            work: JudgeWork::Window(
                ids.iter()
                    .map(|id| LeasedBead {
                        bead_id: BeadId(*id),
                        attempts: attempts.get(id).copied().unwrap_or(1),
                    })
                    .collect(),
            ),
        }))
    }

    pub(crate) fn write_permit_judgment(
        &mut self,
        request: &JudgePermitRequest<'_>,
    ) -> Result<JudgePermit, StoreError> {
        if !matches!(request.purpose, Purpose::Judge | Purpose::SecondRead) {
            return Err(corrupt("a judge's permit was asked for another purpose"));
        }
        let mut tx = write_transaction(self.client()?)?;
        // One number, for the receipt. From here to the commit nothing in
        // the workspace changes: no scope's state, no consent, no text.
        let receipt_seq = begin_workspace_write(&mut tx, request.workspace, Some(1))?
            .ok_or_else(|| corrupt("a workspace gave no change number"))?;

        let mut asked: Vec<Uuid> = Vec::with_capacity(request.new.len());
        for bead in request.new {
            if !asked.contains(&bead.0) {
                asked.push(bead.0);
            }
        }
        let mut loaded = load_beads(&mut tx, request.workspace, request.destination, &asked)?;
        let (mut new, mut withheld) = (Vec::new(), Vec::new());
        for id in &asked {
            match loaded.remove(id) {
                Some(Loaded {
                    withheld: None,
                    shown: Some(bead),
                    ..
                }) => new.push(bead),
                Some(Loaded {
                    withheld: Some(reason),
                    ..
                }) => withheld.push((BeadId(*id), reason)),
                // Not a bead of source content of this workspace.
                _ => withheld.push((BeadId(*id), Withheld::NothingToShow)),
            }
        }
        if new.is_empty() {
            return Ok(JudgePermit::NothingToJudge(withheld));
        }
        new.sort_by_key(|bead| (bead.time_at, bead.seq));

        // The beads beside them, most wanted first. Each is decided on its
        // own: a bead is shown only while its scope is active and has
        // granted the destination.
        let (wanted, limits) = match request.window {
            WindowOf::These(these) => {
                let mut wanted: Vec<Uuid> = Vec::with_capacity(these.len());
                for bead in these {
                    if !asked.contains(&bead.0) && !wanted.contains(&bead.0) {
                        wanted.push(bead.0);
                    }
                }
                (wanted, None)
            }
            WindowOf::Near {
                max_beads,
                max_bytes,
                ..
            } => (
                near(&mut tx, request, &new, &asked)?,
                Some((max_beads as usize, max_bytes)),
            ),
        };
        let mut found = load_beads(&mut tx, request.workspace, request.destination, &wanted)?;
        let (mut window, mut bytes) = (Vec::new(), 0u64);
        let mut missing = Vec::new();
        for id in &wanted {
            let bead = match found.remove(id) {
                Some(Loaded {
                    withheld: None,
                    pending,
                    shown: Some(bead),
                }) => (pending, bead),
                Some(Loaded {
                    withheld: Some(reason),
                    ..
                }) => {
                    missing.push((BeadId(*id), reason));
                    continue;
                }
                _ => {
                    missing.push((BeadId(*id), Withheld::NothingToShow));
                    continue;
                }
            };
            let (pending, bead) = bead;
            if let Some((max_beads, max_bytes)) = limits {
                // A bead that waits to be judged meets these when its own
                // turn comes.
                if pending {
                    continue;
                }
                if window.len() >= max_beads
                    || bytes.saturating_add(bead.content.bytes()) > max_bytes
                {
                    break;
                }
            }
            bytes += bead.content.bytes();
            window.push(bead);
        }
        // Beads named for a repair or a second read are shown together or
        // not at all: the read is about all of them.
        if limits.is_none() && !missing.is_empty() {
            return Ok(JudgePermit::NothingToJudge(missing));
        }
        // One new bead and nothing beside it is no pair. Nothing is sent
        // and no receipt is written.
        if limits.is_some() && window.is_empty() && new.len() == 1 {
            return Ok(JudgePermit::NothingToCompare(withheld));
        }
        window.sort_by_key(|bead| (bead.time_at, bead.seq));
        let shown = Shown { window, new };

        let Some(Sending {
            bytes,
            reserved_tokens: reserved,
        }) = (request.sending)(&shown)
        else {
            return Ok(JudgePermit::Unsendable);
        };
        if let Some(budget) = request.budget {
            let spent = tx
                .query_one(
                    "SELECT count(*), coalesce(sum(reserved_tokens), 0)::bigint
                       FROM core_memory.outbound_calls
                      WHERE workspace_id = $1 AND binding_id = $2
                        AND permitted_at >= date_trunc('day', $3::timestamptz, 'UTC')",
                    &[
                        &request.workspace,
                        &request.reader.binding_id,
                        &request.at.0,
                    ],
                )
                .map_err(backend)?;
            let calls = to_u64(spent.get(0), "calls today")?;
            let tokens = to_u64(spent.get(1), "tokens today")?;
            if calls >= budget.max_requests_per_day
                || tokens.saturating_add(reserved) > budget.max_tokens_per_day
            {
                return Ok(JudgePermit::OverBudget);
            }
        }
        // The receipt names every bead shown, and the chunks whose text is
        // sent. A tool call is shown as what a model wrote about it, so
        // none of its chunks is.
        let beads: Vec<Uuid> = shown.listed().map(|bead| bead.bead_id.0).collect();
        let chunks: Vec<Uuid> = shown
            .listed()
            .filter(|bead| matches!(bead.content, ShownContent::Text(_)))
            .map(|bead| bead.bead_id.0)
            .collect();
        let receipt = tx
            .query_one(
                "INSERT INTO core_memory.outbound_calls (
                     call_id, workspace_id, seq, purpose, scope, destination,
                     binding_id, binding_rev, model, attempt, repairs,
                     chunk_ids, bead_ids, bytes_sent, reserved_tokens, permitted_at
                 ) VALUES (
                     gen_random_uuid(), $1, $2, $3, NULL, $4, $5, $6, $7, $8, $9,
                     $10, $11, $12, $13, $14
                 ) RETURNING call_id",
                &[
                    &request.workspace,
                    &receipt_seq,
                    &request.purpose.as_str(),
                    &request.destination,
                    &request.reader.binding_id,
                    &request.reader.binding_rev.as_bytes().as_slice(),
                    &request.reader.model,
                    &i32::from(request.attempt),
                    &request.repairs.map(|call| call.0),
                    &chunks,
                    &beads,
                    &to_i64(bytes, "bytes sent")?,
                    &to_i64(reserved, "reserved tokens")?,
                    &request.at.0,
                ],
            )
            .map_err(backend)?;
        let call_id = CallId(receipt.get::<_, Uuid>(0));
        // The receipt is durable before any text leaves this function.
        tx.commit().map_err(backend)?;
        Ok(JudgePermit::Granted {
            call_id,
            shown,
            withheld,
        })
    }

    pub(crate) fn write_finish_judgment(
        &mut self,
        f: &JudgmentFinish<'_>,
    ) -> Result<JudgmentFinished, StoreError> {
        let mut tx = write_transaction(self.client()?)?;
        begin_workspace_write(&mut tx, f.workspace, None)?;
        // A provider's own count of tokens is a number from outside.
        let tokens = |count: u64| i64::try_from(count).unwrap_or(i64::MAX);
        let recorded = tx
            .execute(
                "INSERT INTO core_memory.outbound_results
                     (call_id, result, input_tokens, output_tokens, finished_at)
                 SELECT o.call_id, $2, $3, $4, $5
                   FROM core_memory.outbound_calls AS o
                  WHERE o.call_id = $1 AND o.workspace_id = $6
                    AND o.purpose IN ('judge', 'second_read')
                 ON CONFLICT (call_id) DO NOTHING",
                &[
                    &f.call_id.0,
                    &f.result.as_str(),
                    &f.usage.map(|u| tokens(u.input_tokens)),
                    &f.usage.map(|u| tokens(u.output_tokens)),
                    &f.at.0,
                    &f.workspace,
                ],
            )
            .map_err(backend)?;
        if recorded == 0 {
            // The end of this call is recorded: an earlier try committed,
            // and its answer was lost on the way back. What that try
            // stored and settled stands. Or the call has no receipt of a
            // judge's in this workspace, and then nothing is stored for it.
            let stored = stored_by(&mut tx, f.call_id)?;
            tx.commit().map_err(backend)?;
            return Ok(stored);
        }
        let finished = match f.judgment {
            Some(judgment) => store_judgment(&mut tx, f, judgment)?,
            None => JudgmentFinished::default(),
        };
        update_work(&mut tx, f.work, Some(f.lease))?;
        tx.commit().map_err(backend)?;
        Ok(finished)
    }

    /// Queues for the judge the beads of a scope that no judge has read
    /// as new: those stored before a judge was configured. With `again`,
    /// also those that only other judges than this one have read. A tool
    /// call's bead is queued only once a model has written about the call.
    pub fn queue_judgments(
        &mut self,
        scope: &str,
        again: Option<&ReaderRef>,
        at: Timestamp,
    ) -> Result<QueuedReads, StoreError> {
        let queued = self.write_queue_judgments(scope, again, at);
        self.settled(queued)
    }

    fn write_queue_judgments(
        &mut self,
        scope: &str,
        again: Option<&ReaderRef>,
        at: Timestamp,
    ) -> Result<QueuedReads, StoreError> {
        let mut tx = write_transaction(self.client()?)?;
        // The workspace is held, so no bead of the scope is erased between
        // being found live and being queued.
        match begin_write(&mut tx, scope, None)? {
            None => return Ok(QueuedReads::ScopeNotActive(None)),
            Some(writing) if writing.state != ScopeState::Active => {
                return Ok(QueuedReads::ScopeNotActive(Some(writing.state)));
            }
            Some(_) => {}
        }
        let this_judge = again.map(|judge| judge.binding_rev.as_bytes().as_slice());
        let rows = tx
            .query(
                "INSERT INTO core_memory.work
                     (kind, item_id, event_id, workspace_id, scope, state, not_before,
                      enqueued_at)
                 SELECT 'judge', b.bead_id, b.event_id, b.workspace_id, b.scope, 'ready', $2, $2
                   FROM core_memory.beads AS b
                  WHERE b.scope = $1 AND b.state = 'live' AND b.kind IN ('chunk', 'tool')
                    AND (b.kind = 'chunk' OR EXISTS (
                        SELECT 1 FROM core_memory.tool_outcomes AS o
                         WHERE o.event_id = b.event_id))
                    AND NOT EXISTS (
                        SELECT 1 FROM core_memory.judge_reads AS r
                         WHERE r.new_beads @> ARRAY[b.bead_id] AND r.read_no = 1
                           AND ($3::bytea IS NULL OR r.binding_rev = $3))
                 ON CONFLICT (kind, item_id) DO NOTHING
                 RETURNING (SELECT x.kind FROM core_memory.beads AS x
                             WHERE x.bead_id = item_id)",
                &[&scope, &at.0, &this_judge],
            )
            .map_err(backend)?;
        tx.commit().map_err(backend)?;
        let tool_calls = rows
            .iter()
            .filter(|row| row.get::<_, &str>(0) == "tool")
            .count() as u64;
        Ok(QueuedReads::Queued {
            chunks: rows.len() as u64 - tool_calls,
            tool_calls,
        })
    }
}

/// The one scope a bead's content belongs to: its own for a bead of source
/// content. A group bead belongs to one scope when the members it was made
/// with all do. Those were written with it, by the same call, and never
/// change, so what an export of one scope gives stays given. A member added
/// later from another scope is left out of that export with its edge.
/// `bead` is an SQL expression.
fn home_scope(bead: &str) -> String {
    format!(
        "(SELECT CASE h.kind
                 WHEN 'group' THEN (
                     SELECT CASE WHEN count(DISTINCT mb.scope) = 1 THEN min(mb.scope) END
                       FROM core_memory.edges AS hm
                       JOIN core_memory.beads AS mb ON mb.bead_id = hm.to_bead
                      WHERE hm.from_bead = h.bead_id AND hm.arrow = 'consists'
                        AND hm.call_id = h.call_id)
                 ELSE h.scope
                 END
            FROM core_memory.beads AS h
           WHERE h.bead_id = {bead})"
    )
}

/// The condition that keeps an export of one scope to what lies wholly
/// inside it. A judge reads across the scopes of a workspace, so what it
/// says can join beads of two scopes, and an export of one scope does not
/// say that the other exists. `$scope` is the parameter that holds the
/// scope, or NULL for all of them.
fn inside(scope: &str, beads: &[&str]) -> String {
    let all: Vec<String> = beads
        .iter()
        .map(|bead| format!("{} = {scope}", home_scope(bead)))
        .collect();
    format!("({scope}::text IS NULL OR ({}))", all.join(" AND "))
}

/// The same condition for a row that carries the scopes of its beads
/// itself. `scopes` are its columns that hold them.
fn within(scope: &str, scopes: &[&str]) -> String {
    let all: Vec<String> = scopes
        .iter()
        .map(|column| format!("{column} = {scope}"))
        .collect();
    format!("({scope}::text IS NULL OR ({}))", all.join(" AND "))
}

/// The change numbers of what a judge wrote, as branches of the query that
/// finds the next page of an export. `$1` is the workspace, `$2` the
/// number to start after and `$3` the scope.
pub(crate) fn exported_seqs() -> String {
    format!(
        "UNION ALL
         SELECT b.seq FROM core_memory.beads AS b
          WHERE b.workspace_id = $1 AND b.seq > $2 AND b.kind = 'group'
            AND b.state = 'live' AND {beads}
         UNION ALL
         SELECT x.seq FROM core_memory.edges AS x
          WHERE x.workspace_id = $1 AND x.seq > $2 AND {edges}
         UNION ALL
         SELECT v.seq FROM core_memory.verdicts AS v
          WHERE v.workspace_id = $1 AND v.seq > $2 AND {verdicts}
         UNION ALL
         SELECT n.seq FROM core_memory.name_links AS n
          WHERE n.workspace_id = $1 AND n.seq > $2 AND {names}",
        beads = inside("$3", &["b.bead_id"]),
        edges = inside("$3", &["x.from_bead", "x.to_bead"]),
        verdicts = within("$3", &["v.earlier_scope", "v.later_scope"]),
        names = within("$3", &["n.a_scope", "n.b_scope"]),
    )
}

/// Adds what a judge wrote, with change numbers after `after` and up to
/// `last`, to the changes of an export.
pub(crate) fn export_judgments(
    tx: &mut postgres::Transaction<'_>,
    workspace: &str,
    (after, last): (i64, i64),
    scope: Option<&str>,
    changes: &mut BTreeMap<u64, Change>,
) -> Result<(), StoreError> {
    let named = |name: &str, what: &str| corrupt(format!("unknown {what} `{name}`"));
    let at = |row: &postgres::Row, column: usize| Timestamp(row.get::<_, OffsetDateTime>(column));

    // ---- the beads a judge made ----
    let rows = tx
        .query(
            &format!(
                "SELECT b.bead_id, b.seq, b.call_id, b.created_at
                   FROM core_memory.beads AS b
                  WHERE b.workspace_id = $1 AND b.seq > $2 AND b.seq <= $3
                    AND b.kind = 'group' AND b.state = 'live' AND {inside}
                  ORDER BY b.seq",
                inside = inside("$4", &["b.bead_id"])
            ),
            &[&workspace, &after, &last, &scope],
        )
        .map_err(backend)?;
    for row in &rows {
        let bead_id = BeadId(row.get::<_, Uuid>(0));
        let call: Option<Uuid> = row.get(2);
        let record = BeadRecord {
            bead_id,
            workspace: workspace.to_string(),
            seq: to_u64(row.get(1), "change number")?,
            made: MadeBead::Group,
            call_id: CallId(
                call.ok_or_else(|| corrupt(format!("bead {bead_id} names no judge's call")))?,
            ),
            created_at: at(row, 3),
        };
        changes.insert(record.seq, Change::Bead(Box::new(record)));
    }

    // ---- relationships ----
    let rows = tx
        .query(
            &format!(
                "SELECT x.seq, x.edge_type, x.from_bead, x.to_bead, x.call_id, x.created_at,
                        x.arrow
                   FROM core_memory.edges AS x
                  WHERE x.workspace_id = $1 AND x.seq > $2 AND x.seq <= $3 AND {inside}
                  ORDER BY x.seq",
                inside = inside("$4", &["x.from_bead", "x.to_bead"])
            ),
            &[&workspace, &after, &last, &scope],
        )
        .map_err(backend)?;
    for row in &rows {
        let edge_type: &str = row.get(1);
        let record = EdgeRecord {
            workspace: workspace.to_string(),
            seq: to_u64(row.get(0), "change number")?,
            edge_type: EdgeType::from_name(edge_type)
                .ok_or_else(|| named(edge_type, "relationship"))?,
            arrow: arrow_named(row.get(6))?,
            from_bead: BeadId(row.get::<_, Uuid>(2)),
            to_bead: BeadId(row.get::<_, Uuid>(3)),
            call_id: CallId(row.get::<_, Uuid>(4)),
            created_at: at(row, 5),
        };
        changes.insert(record.seq, Change::Edge(Box::new(record)));
    }

    // ---- verdicts, each with the read that gave it ----
    let rows = tx
        .query(
            &format!(
                "SELECT v.seq, v.earlier_bead, v.later_bead, v.verdict, v.direction, v.arrow,
                        v.note, v.columns, v.columns_rev, v.call_id, r.read_no, r.rubric_rev,
                        r.binding_id, r.binding_rev, r.model, r.judged_at, v.earlier_scope,
                        v.later_scope
                   FROM core_memory.verdicts AS v
                   JOIN core_memory.judge_reads AS r ON r.call_id = v.call_id
                  WHERE v.workspace_id = $1 AND v.seq > $2 AND v.seq <= $3 AND {inside}
                  ORDER BY v.seq",
                inside = within("$4", &["v.earlier_scope", "v.later_scope"])
            ),
            &[&workspace, &after, &last, &scope],
        )
        .map_err(backend)?;
    for row in &rows {
        let seq = to_u64(row.get(0), "change number")?;
        let (verdict, direction): (&str, &str) = (row.get(3), row.get(4));
        let Json(columns): Json<PairColumns> = row.try_get(7).map_err(|e| {
            corrupt(format!(
                "the columns of verdict {seq} do not read back: {e}"
            ))
        })?;
        let record = VerdictRecord {
            workspace: workspace.to_string(),
            seq,
            earlier_bead: BeadId(row.get::<_, Uuid>(1)),
            later_bead: BeadId(row.get::<_, Uuid>(2)),
            earlier_scope: row.get(16),
            later_scope: row.get(17),
            verdict: Verdict::from_name(verdict).ok_or_else(|| named(verdict, "verdict"))?,
            arrow: row.get::<_, Option<&str>>(5).map(arrow_named).transpose()?,
            direction: Direction::from_name(direction)
                .ok_or_else(|| named(direction, "direction"))?,
            note: row
                .get::<_, Option<&str>>(6)
                .map(|note| VerdictNote::from_name(note).ok_or_else(|| named(note, "note")))
                .transpose()?,
            read_no: u8::try_from(row.get::<_, i32>(10))
                .map_err(|_| corrupt(format!("verdict {seq} has no read number")))?,
            columns,
            columns_rev: to_u32(row.get(8), "columns revision")?,
            judge: JudgeRef {
                binding_id: row.get(12),
                binding_rev: digest(row.get(13), "binding revision")?,
                model: row.get(14),
                rubric_rev: to_u32(row.get(11), "rubric revision")?,
            },
            call_id: CallId(row.get::<_, Uuid>(9)),
            judged_at: at(row, 15),
        };
        changes.insert(record.seq, Change::Verdict(Box::new(record)));
    }

    // ---- name links ----
    let rows = tx
        .query(
            &format!(
                "SELECT n.seq, n.a_bead, n.a_start, n.a_end, n.b_bead, n.b_start, n.b_end,
                        n.call_id, n.linked_at, n.a_scope, n.b_scope
                   FROM core_memory.name_links AS n
                  WHERE n.workspace_id = $1 AND n.seq > $2 AND n.seq <= $3 AND {inside}
                  ORDER BY n.seq",
                inside = within("$4", &["n.a_scope", "n.b_scope"])
            ),
            &[&workspace, &after, &last, &scope],
        )
        .map_err(backend)?;
    for row in &rows {
        let record = NameLinkRecord {
            workspace: workspace.to_string(),
            seq: to_u64(row.get(0), "change number")?,
            a_bead: BeadId(row.get::<_, Uuid>(1)),
            a_scope: row.get(9),
            a_start: to_u64(row.get(2), "name offset")?,
            a_end: to_u64(row.get(3), "name offset")?,
            b_bead: BeadId(row.get::<_, Uuid>(4)),
            b_scope: row.get(10),
            b_start: to_u64(row.get(5), "name offset")?,
            b_end: to_u64(row.get(6), "name offset")?,
            call_id: CallId(row.get::<_, Uuid>(7)),
            linked_at: at(row, 8),
        };
        changes.insert(record.seq, Change::NameLink(Box::new(record)));
    }
    Ok(())
}

/// What a call that already ended stored, for a caller that asks again.
fn stored_by<C: GenericClient>(tx: &mut C, call: CallId) -> Result<JudgmentFinished, StoreError> {
    let row = tx
        .query_one(
            "SELECT (SELECT coalesce(cardinality(r.new_beads), 0)
                       FROM core_memory.judge_reads AS r WHERE r.call_id = $1),
                    (SELECT count(*) FROM core_memory.verdicts WHERE call_id = $1),
                    (SELECT count(*) FROM core_memory.edges
                      WHERE call_id = $1 AND arrow NOT IN ('repl', 'withdraws', 'consists')),
                    (SELECT count(DISTINCT from_bead) FROM core_memory.edges
                      WHERE call_id = $1 AND arrow IN ('repl', 'withdraws')),
                    (SELECT count(*) FROM core_memory.pending_chains WHERE call_id = $1),
                    (SELECT count(DISTINCT from_bead) FROM core_memory.edges
                      WHERE call_id = $1 AND arrow = 'consists'),
                    (SELECT count(*) FROM core_memory.name_links WHERE call_id = $1)",
            &[&call.0],
        )
        .map_err(backend)?;
    let count = |column: usize| -> u32 {
        let n: Option<i64> = row
            .try_get::<_, Option<i64>>(column)
            .ok()
            .flatten()
            .or_else(|| {
                row.try_get::<_, Option<i32>>(column)
                    .ok()
                    .flatten()
                    .map(i64::from)
            });
        u32::try_from(n.unwrap_or(0)).unwrap_or(u32::MAX)
    };
    Ok(JudgmentFinished {
        judged: count(0),
        verdicts: count(1),
        edges: count(2),
        chains: count(3),
        held: count(4),
        chains_dropped: 0,
        groups: count(5),
        names: count(6),
        dropped: 0,
    })
}

/// Stores what a valid reply says, for the beads that may still have
/// something stored about them.
fn store_judgment(
    tx: &mut postgres::Transaction<'_>,
    f: &JudgmentFinish<'_>,
    j: &JudgmentWrite,
) -> Result<JudgmentFinished, StoreError> {
    let mut finished = JudgmentFinished {
        dropped: j.verdicts.len() as u32,
        ..JudgmentFinished::default()
    };
    // Only beads the call's receipt names: a judge says nothing about a
    // bead it was not shown.
    let receipt = tx
        .query_one(
            "SELECT purpose, bead_ids FROM core_memory.outbound_calls WHERE call_id = $1",
            &[&f.call_id.0],
        )
        .map_err(backend)?;
    let second_read = receipt.get::<_, &str>(0) == Purpose::SecondRead.as_str();
    let kind = if second_read {
        Purpose::SecondRead
    } else {
        Purpose::Judge
    };
    let named: Vec<Uuid> = receipt.get::<_, Option<Vec<Uuid>>>(1).unwrap_or_default();
    // Of those, the ones that are live in a scope that is active. A bead
    // erased while the judge read, or whose scope stopped, gets nothing.
    let valid: BTreeSet<Uuid> = tx
        .query(
            "SELECT b.bead_id
               FROM core_memory.beads AS b
               JOIN core_memory.scopes AS s ON s.scope = b.scope
              WHERE b.workspace_id = $1 AND b.bead_id = ANY ($2)
                AND b.state = 'live' AND s.state = 'active'",
            &[&f.workspace, &named],
        )
        .map_err(backend)?
        .iter()
        .map(|row| row.get(0))
        .collect();
    // A second read settles its held arrow only if what it says of every
    // bead the arrow would end may be stored. A head that is live in a
    // scope that is not active may not be written about now, and the
    // arrow would be lost: the second read is owed again instead, and is
    // put back below as a new bead in a stopped scope is. A head that was
    // erased took the held arrow with it.
    let waits = match f.settles {
        Some(source) => tx
            .query_one(
                "SELECT EXISTS (
                     SELECT 1
                       FROM core_memory.pending_chains AS p
                       JOIN core_memory.beads AS b ON b.bead_id = ANY (p.head_beads)
                       JOIN core_memory.scopes AS s ON s.scope = b.scope
                      WHERE p.source_bead = $1 AND b.state = 'live' AND s.state <> 'active')",
                &[&source.0],
            )
            .map_err(backend)?
            .get::<_, bool>(0)
            .then_some(source.0),
        None => None,
    };
    // Of the new beads, the ones still owed a judgment by this call: their
    // work is still held under its lease, and is done now. A bead whose
    // work is gone was erased, or judged by another worker. A bead whose
    // work is held under another lease was handed to another worker after
    // this one's lease ran out: that worker judges it, and this one's
    // reply stores nothing for it.
    let new: Vec<Uuid> = j
        .new_beads
        .iter()
        .map(|bead| bead.0)
        .filter(|id| valid.contains(id) && waits != Some(*id))
        .collect();
    let owed: BTreeSet<Uuid> = tx
        .query(
            "DELETE FROM core_memory.work
              WHERE kind = $1 AND item_id = ANY ($2)
                AND state = 'leased' AND leased_until = $3
             RETURNING item_id",
            &[&kind.as_str(), &new, &f.lease.0],
        )
        .map_err(backend)?
        .iter()
        .map(|row| row.get(0))
        .collect();
    // A new bead that may not be written about now, because its scope
    // stopped being active while the judge read, is put back, and so is
    // the source of a held arrow whose head's scope stopped. Its work
    // would otherwise keep the lease, and with it every other judge out of
    // the workspace, until the lease ran out. Its attempt is given back:
    // nothing about it was wrong.
    let all_new: Vec<Uuid> = j.new_beads.iter().map(|bead| bead.0).collect();
    tx.execute(
        "UPDATE core_memory.work
            SET state = 'ready', leased_until = NULL, attempts = greatest(attempts - 1, 0),
                not_before = $3, problem = 'scope_not_active'
          WHERE kind = $1 AND item_id = ANY ($2)
            AND state = 'leased' AND leased_until = $4",
        &[&kind.as_str(), &all_new, &f.at.0, &f.lease.0],
    )
    .map_err(backend)?;
    if owed.is_empty() {
        return Ok(finished);
    }
    // A valid second read settles the arrow it was held for, whatever it
    // says: the arrow is written below if this read gives it again.
    if let Some(source) = f.settles.filter(|source| owed.contains(&source.0)) {
        tx.execute(
            "DELETE FROM core_memory.pending_chains WHERE source_bead = $1",
            &[&source.0],
        )
        .map_err(backend)?;
    }
    let is_valid = |bead: &BeadId| valid.contains(&bead.0);
    let is_owed = |bead: &BeadId| owed.contains(&bead.0);

    let verdicts: Vec<_> = j
        .verdicts
        .iter()
        .filter(|v| is_owed(&v.read_for) && is_valid(&v.earlier) && is_valid(&v.later))
        .collect();
    let edges: Vec<_> = j
        .edges
        .iter()
        .filter(|e| is_owed(&e.read_for) && is_valid(&e.from) && is_valid(&e.to))
        .collect();
    let (chains, unwritten): (Vec<&ChainWrite>, Vec<&ChainWrite>) =
        j.chains.iter().partition(|c| {
            c.read_for.iter().all(is_owed) && is_valid(&c.source) && c.heads.iter().all(is_valid)
        });
    let names: Vec<_> = j
        .names
        .iter()
        .filter(|n| is_owed(&n.read_for) && is_valid(&n.a_bead) && is_valid(&n.b_bead))
        .collect();
    // A group keeps the members that may still be written about. A new
    // bead that is no longer owed a judgment is not one of them.
    let groups: Vec<(Option<BeadId>, BeadId, Vec<Uuid>)> = j
        .groups
        .iter()
        .filter_map(|group| {
            let members: Vec<Uuid> = group
                .members
                .iter()
                .filter(|member| {
                    is_valid(member) && (!j.new_beads.contains(member) || is_owed(member))
                })
                .map(|member| member.0)
                .collect();
            let joined = members.iter().any(|member| owed.contains(member));
            (joined && members.len() >= 2).then_some((group.existing, group.bead_id, members))
        })
        .collect();

    // Numbers for every row this may write: the read, each verdict, each
    // relationship, each arrow that ends currency or the held arrow in its
    // place, each group bead with its edges, and each name link.
    let chain_rows: usize = chains.iter().map(|c| c.heads.len().max(1)).sum();
    let group_rows: usize = groups.iter().map(|(_, _, members)| 1 + members.len()).sum();
    let count = 1 + verdicts.len() + edges.len() + chain_rows + group_rows + names.len();
    let take = i32::try_from(count).map_err(|_| corrupt("a judgment is too large to store"))?;
    let first = begin_workspace_write(tx, f.workspace, Some(take))?
        .ok_or_else(|| corrupt("a workspace gave no change number"))?;
    let mut numbers = Numbers {
        next: first,
        left: count,
    };

    // ---- the read ----
    let judged: Vec<Uuid> = j
        .new_beads
        .iter()
        .map(|bead| bead.0)
        .filter(|id| owed.contains(id))
        .collect();
    let window: Vec<Uuid> = j.window_beads.iter().map(|bead| bead.0).collect();
    tx.execute(
        "INSERT INTO core_memory.judge_reads (
             call_id, workspace_id, seq, read_no, new_beads, window_beads, rubric_rev,
             binding_id, binding_rev, model, judged_at
         ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
        &[
            &f.call_id.0,
            &f.workspace,
            &numbers.take()?,
            &i32::from(j.read_no),
            &judged,
            &window,
            &to_i32(j.rubric_rev, "rubric revision")?,
            &f.reader.binding_id,
            &f.reader.binding_rev.as_bytes().as_slice(),
            &f.reader.model,
            &f.at.0,
        ],
    )
    .map_err(backend)?;
    finished.judged = judged.len() as u32;
    // The verdicts are numbered first and written once it is known what
    // became of their arrows: a reader of the changes gets what the judge
    // said of each pair, and then what that amounted to.
    let verdict_seqs: Vec<i64> = verdicts
        .iter()
        .map(|_| numbers.take())
        .collect::<Result<_, _>>()?;

    // ---- arrows that end currency ----
    // What is current, and which sources already end beads, as the
    // workspace stands now. Both change as this read's own arrows are
    // written.
    let mut involved: Vec<Uuid> = Vec::new();
    for chain in &chains {
        involved.push(chain.source.0);
        involved.extend(chain.heads.iter().map(|head| head.0));
    }
    let mut ended: BTreeSet<Uuid> = tx
        .query(
            "SELECT to_bead FROM core_memory.edges
              WHERE arrow IN ('repl', 'withdraws') AND to_bead = ANY ($1)",
            &[&involved],
        )
        .map_err(backend)?
        .iter()
        .map(|row| row.get(0))
        .collect();
    // For each source that already ends beads: the arrow, and how many.
    let mut carried: BTreeMap<Uuid, (Arrow, usize)> = BTreeMap::new();
    for row in &tx
        .query(
            "SELECT from_bead, min(arrow), count(*) FROM core_memory.edges
              WHERE arrow IN ('repl', 'withdraws') AND from_bead = ANY ($1)
              GROUP BY from_bead",
            &[&involved],
        )
        .map_err(backend)?
    {
        let ends = usize::try_from(row.get::<_, i64>(2)).unwrap_or(usize::MAX);
        carried.insert(row.get(0), (arrow_named(row.get(1))?, ends));
    }
    // The arrows that wait for a second read and share a bead with this
    // read's: their sources, and every bead they are about.
    let mut held: BTreeSet<Uuid> = BTreeSet::new();
    let mut touched: BTreeSet<Uuid> = BTreeSet::new();
    for row in &tx
        .query(
            "SELECT source_bead, head_beads FROM core_memory.pending_chains
              WHERE workspace_id = $2 AND (source_bead = ANY ($1) OR head_beads && $1)",
            &[&involved, &f.workspace],
        )
        .map_err(backend)?
    {
        let source: Uuid = row.get(0);
        let heads: Vec<Uuid> = row.get(1);
        held.insert(source);
        touched.insert(source);
        touched.extend(heads);
    }
    // What became of the arrow of each pair of a later statement and a
    // bead it would end.
    let mut fates: BTreeMap<(Uuid, Uuid), ChainFate> = BTreeMap::new();
    for chain in &unwritten {
        // One of its beads may no longer have anything stored about it.
        for head in &chain.heads {
            fates.insert((chain.source.0, head.0), ChainFate::NotCurrent);
        }
    }
    for chain in &chains {
        let source = chain.source.0;
        let heads: Vec<Uuid> = chain.heads.iter().map(|head| head.0).collect();
        // A chain grows at its head: the later statement and each bead it
        // ends must be current.
        let current = !ended.contains(&source) && heads.iter().all(|head| !ended.contains(head));
        let room = match carried.get(&source) {
            None => true,
            Some((carries, ends)) => *carries == chain.arrow && ends + heads.len() <= 2,
        };
        // An arrow that shares a bead with one that waits for its second
        // read waits too, and is read again after it. Written now, it
        // could make the other unwritable: a replacement of a statement
        // whose own replacement is held would end that statement first.
        // A second read settles the oldest held arrow of those it shares a
        // bead with: the later ones wait for it, and not it for them.
        let touches = !second_read
            && std::iter::once(&source)
                .chain(heads.iter())
                .any(|bead| touched.contains(bead));
        let fate = if !current {
            ChainFate::NotCurrent
        } else if !room || held.contains(&source) {
            ChainFate::Taken
        } else if chain.hold || touches {
            tx.execute(
                "INSERT INTO core_memory.pending_chains
                     (source_bead, workspace_id, arrow, head_beads, ordinal, call_id,
                      proposed_at)
                 VALUES ($1, $2, $3, $4, $5, $6, $7)",
                &[
                    &source,
                    &f.workspace,
                    &chain.arrow.as_str(),
                    &heads,
                    &numbers.take()?,
                    &f.call_id.0,
                    &f.at.0,
                ],
            )
            .map_err(backend)?;
            // The second read is owed from now.
            tx.execute(
                "INSERT INTO core_memory.work
                     (kind, item_id, event_id, workspace_id, scope, state, not_before,
                      enqueued_at)
                 SELECT 'second_read', b.bead_id, b.event_id, b.workspace_id, b.scope, 'ready',
                        $2, $2
                   FROM core_memory.beads AS b WHERE b.bead_id = $1
                 ON CONFLICT (kind, item_id) DO NOTHING",
                &[&source, &f.at.0],
            )
            .map_err(backend)?;
            held.insert(source);
            touched.insert(source);
            touched.extend(heads.iter().copied());
            ChainFate::Held
        } else {
            for head in &heads {
                tx.execute(
                    "INSERT INTO core_memory.edges
                         (workspace_id, seq, edge_type, arrow, from_bead, to_bead, call_id,
                          created_at)
                     VALUES ($1, $2, 'leadsto', $3, $4, $5, $6, $7)",
                    &[
                        &f.workspace,
                        &numbers.take()?,
                        &chain.arrow.as_str(),
                        &source,
                        head,
                        &f.call_id.0,
                        &f.at.0,
                    ],
                )
                .map_err(backend)?;
                ended.insert(*head);
            }
            let ends = carried.get(&source).map_or(0, |(_, ends)| *ends);
            carried.insert(source, (chain.arrow, ends + heads.len()));
            ChainFate::Written
        };
        match fate {
            ChainFate::Written => finished.chains += 1,
            ChainFate::Held => finished.held += 1,
            ChainFate::NotCurrent | ChainFate::Taken => finished.chains_dropped += 1,
        }
        for head in heads {
            fates.insert((source, head), fate);
        }
    }

    // ---- verdicts ----
    // A row for every pair, a no included. A verdict whose arrow ends
    // currency and was not written says why.
    let (mut earlier, mut later) = (Vec::new(), Vec::new());
    let (mut said, mut directions) = (Vec::new(), Vec::new());
    let (mut given, mut notes): (Vec<Option<&str>>, Vec<Option<&str>>) = (Vec::new(), Vec::new());
    let mut columns = Vec::new();
    for verdict in &verdicts {
        let note = match (verdict.arrow, verdict.note) {
            (Some(arrow), None) if arrow.ends_currency() => {
                let (from, to) = match verdict.direction {
                    Direction::EarlierToLater => (verdict.earlier, verdict.later),
                    _ => (verdict.later, verdict.earlier),
                };
                fates
                    .get(&(from.0, to.0))
                    .copied()
                    // A second read that does not give the held arrow
                    // again says so itself. Anything else without a
                    // fate was not written.
                    .unwrap_or(ChainFate::NotCurrent)
                    .note()
            }
            (_, note) => note,
        };
        earlier.push(verdict.earlier.0);
        later.push(verdict.later.0);
        said.push(verdict.verdict.as_str());
        directions.push(verdict.direction.as_str());
        given.push(verdict.arrow.map(Arrow::as_str));
        notes.push(note.map(VerdictNote::as_str));
        columns.push(Json(&verdict.columns));
    }
    tx.execute(
        "INSERT INTO core_memory.verdicts (
             workspace_id, seq, earlier_bead, later_bead, verdict, direction, arrow, note,
             call_id, columns, columns_rev
         )
         SELECT $1, v.seq, v.earlier, v.later, v.verdict, v.direction, v.arrow, v.note, $2,
                v.columns, $3
           FROM UNNEST($4::int8[], $5::uuid[], $6::uuid[], $7::text[], $8::text[], $9::text[],
                       $10::text[], $11::jsonb[])
                AS v (seq, earlier, later, verdict, direction, arrow, note, columns)",
        &[
            &f.workspace,
            &f.call_id.0,
            &to_i32(core_memory_contract::COLUMNS_REV, "columns revision")?,
            &verdict_seqs,
            &earlier,
            &later,
            &said,
            &directions,
            &given,
            &notes,
            &columns,
        ],
    )
    .map_err(backend)?;
    finished.verdicts = verdicts.len() as u32;
    finished.dropped = (j.verdicts.len() - verdicts.len()) as u32;

    // ---- relationships between beads of source content ----
    for edge in &edges {
        // The same relationship between the same two beads is one edge,
        // whoever judged it again.
        finished.edges += tx
            .execute(
                "INSERT INTO core_memory.edges
                     (workspace_id, seq, edge_type, arrow, from_bead, to_bead, call_id,
                      created_at)
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                 ON CONFLICT (arrow, from_bead, to_bead) DO NOTHING",
                &[
                    &f.workspace,
                    &numbers.take()?,
                    &edge.arrow.edge_type().as_str(),
                    &edge.arrow.as_str(),
                    &edge.from.0,
                    &edge.to.0,
                    &f.call_id.0,
                    &f.at.0,
                ],
            )
            .map_err(backend)? as u32;
    }

    // ---- groups ----
    for (existing, bead_id, members) in &groups {
        // The groups its members belong to now.
        let rows = tx
            .query(
                "SELECT g.to_bead, g.from_bead
                   FROM core_memory.edges AS g
                   JOIN core_memory.beads AS gb ON gb.bead_id = g.from_bead
                  WHERE g.arrow = 'consists' AND g.to_bead = ANY ($1)
                    AND gb.state = 'live'",
                &[members],
            )
            .map_err(backend)?;
        let member_of: BTreeMap<Uuid, Uuid> =
            rows.iter().map(|row| (row.get(0), row.get(1))).collect();
        let joined: BTreeSet<Uuid> = member_of.values().copied().collect();
        let group = match (joined.len(), existing) {
            // Members of two groups were tied together meanwhile. Nothing
            // makes two groups one.
            (2.., _) => continue,
            (1, _) => match joined.first() {
                Some(group) => *group,
                None => continue,
            },
            // The group the judge was shown is gone: its last live member
            // was erased.
            (0, Some(_)) => continue,
            (0, None) => {
                tx.execute(
                    "INSERT INTO core_memory.beads (
                         bead_id, workspace_id, scope, kind, state, event_id, chunk_id,
                         created_by, call_id, time_at, time_basis, created_at, seq
                     ) VALUES ($1, $2, NULL, 'group', 'live', NULL, NULL, 'judge', $3, $4,
                               'captured', $4, $5)",
                    &[
                        &bead_id.0,
                        &f.workspace,
                        &f.call_id.0,
                        &f.at.0,
                        &numbers.take()?,
                    ],
                )
                .map_err(backend)?;
                bead_id.0
            }
        };
        let mut added = false;
        for member in members.iter().filter(|m| !member_of.contains_key(*m)) {
            tx.execute(
                "INSERT INTO core_memory.edges
                     (workspace_id, seq, edge_type, arrow, from_bead, to_bead, call_id,
                      created_at)
                 VALUES ($1, $2, 'contains', 'consists', $3, $4, $5, $6)",
                &[
                    &f.workspace,
                    &numbers.take()?,
                    &group,
                    member,
                    &f.call_id.0,
                    &f.at.0,
                ],
            )
            .map_err(backend)?;
            added = true;
        }
        finished.groups += u32::from(added);
    }

    // ---- name links ----
    for name in &names {
        // The same two names linked again are one link, whoever linked
        // them again.
        finished.names += tx
            .execute(
                "INSERT INTO core_memory.name_links (
                 workspace_id, seq, a_bead, a_start, a_end, b_bead, b_start, b_end, call_id,
                 linked_at
             ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
             ON CONFLICT (a_bead, a_start, a_end, b_bead, b_start, b_end) DO NOTHING",
                &[
                    &f.workspace,
                    &numbers.take()?,
                    &name.a_bead.0,
                    &to_i64(name.a_start, "name offset")?,
                    &to_i64(name.a_end, "name offset")?,
                    &name.b_bead.0,
                    &to_i64(name.b_start, "name offset")?,
                    &to_i64(name.b_end, "name offset")?,
                    &f.call_id.0,
                    &f.at.0,
                ],
            )
            .map_err(backend)? as u32;
    }
    // The arrows of beads that may no longer be written about.
    finished.chains_dropped += unwritten.len() as u32;
    Ok(finished)
}
