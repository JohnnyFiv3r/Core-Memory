//! Vectors: handing out the chunks that are owed one, storing what an
//! embedder made, and finding whether a query can be compared with them.
//!
//! The permit of an embedding call is the permit of any call that sends a
//! scope's chunks ([`PostgresStore::write_permit`]): the same consent, the
//! same budget, the same receipt.

use std::collections::BTreeMap;

use core_memory_contract::{ChunkId, Sha256Digest, Timestamp};
use core_memory_core::store::{
    CallFinished, EmbedBatch, EmbedFinish, LeasedChunk, MeaningReady, Purpose, ScopeState,
};
use core_memory_core::{ScopeGrants, StoreError};
use postgres::GenericClient;
use uuid::Uuid;

use crate::error::{backend, corrupt};
use crate::store::{
    PostgresStore, begin_write, hold_schema, read_one, set_aside_spent, to_i32, to_i64, to_u32,
    update_work, write_transaction,
};

/// The schema the `vector` extension is installed in, as a quoted
/// identifier ready to stand in a statement, or `None` if the database has
/// no such extension or this role may not use its schema.
///
/// Engine sessions name every object with its schema, and the extension's
/// type and operators are no exception: where it is installed is asked,
/// never assumed and never left to a search path.
pub(crate) fn vector_schema<C: GenericClient>(
    client: &mut C,
) -> Result<Option<String>, StoreError> {
    let found = client
        .query_opt(
            "SELECT n.nspname
               FROM pg_catalog.pg_extension AS x
               JOIN pg_catalog.pg_namespace AS n ON n.oid = x.extnamespace
              WHERE x.extname = 'vector'
                AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')",
            &[],
        )
        .map_err(backend)?;
    Ok(found.map(|row| quoted(row.get(0))))
}

/// `name` as a quoted identifier.
fn quoted(name: &str) -> String {
    format!("\"{}\"", name.replace('"', "\"\""))
}

/// The scopes to search as a statement's parameter: none for every scope.
pub(crate) fn named(scopes: &ScopeGrants) -> Option<Vec<String>> {
    match scopes {
        ScopeGrants::All => None,
        ScopeGrants::Only(only) => Some(only.iter().cloned().collect()),
    }
}

impl PostgresStore {
    pub(crate) fn write_claim_embeddings(
        &mut self,
        limit: u32,
        max_attempts: u32,
        now: Timestamp,
        lease_until: Timestamp,
    ) -> Result<Option<EmbedBatch>, StoreError> {
        // Claimed as chunks to be read are: with SKIP LOCKED, so workers
        // never wait for each other, and a lease that ran out is claimed
        // like ready work.
        const DUE: &str = "w.kind = 'embed'
            AND (w.state = 'ready' OR (w.state = 'leased' AND w.leased_until < $1))
            AND w.not_before <= $1";
        let mut tx = write_transaction(self.client()?)?;
        hold_schema(&mut tx)?;
        set_aside_spent(&mut tx, Purpose::Embed, max_attempts, now)?;
        let Some(head) = tx
            .query_opt(
                &format!(
                    "SELECT w.item_id, w.scope, w.workspace_id, w.alone
                       FROM core_memory.work AS w
                      WHERE {DUE}
                      ORDER BY w.not_before, w.enqueued_at, w.item_id
                      LIMIT 1
                        FOR UPDATE OF w SKIP LOCKED"
                ),
                &[&now.0],
            )
            .map_err(backend)?
        else {
            // Nothing is due. What was set aside above still is.
            tx.commit().map_err(backend)?;
            return Ok(None);
        };
        let (scope, workspace): (String, String) = (head.get(1), head.get(2));
        let mut ids: Vec<Uuid> = vec![head.get(0)];
        // A chunk that was part of a failed call goes alone from then on.
        if !head.get::<_, bool>(3) && limit > 1 {
            let rest = tx
                .query(
                    &format!(
                        "SELECT w.item_id
                           FROM core_memory.work AS w
                          WHERE {DUE} AND w.scope = $2 AND NOT w.alone AND w.item_id <> $3
                          ORDER BY w.not_before, w.enqueued_at, w.item_id
                          LIMIT $4
                            FOR UPDATE OF w SKIP LOCKED"
                    ),
                    &[&now.0, &scope, &ids[0], &i64::from(limit - 1)],
                )
                .map_err(backend)?;
            ids.extend(rest.iter().map(|row| row.get::<_, Uuid>(0)));
        }
        let leased = tx
            .query(
                "UPDATE core_memory.work
                    SET state = 'leased', leased_until = $1, attempts = attempts + 1
                  WHERE kind = 'embed' AND item_id = ANY ($2)
              RETURNING item_id, attempts",
                &[&lease_until.0, &ids],
            )
            .map_err(backend)?;
        let attempts: BTreeMap<Uuid, i32> =
            leased.iter().map(|row| (row.get(0), row.get(1))).collect();
        tx.commit().map_err(backend)?;
        let chunks = ids
            .iter()
            .map(|id| {
                Ok(LeasedChunk {
                    chunk_id: ChunkId(*id),
                    attempts: to_u32(*attempts.get(id).unwrap_or(&1), "attempts")?,
                })
            })
            .collect::<Result<_, StoreError>>()?;
        Ok(Some(EmbedBatch {
            scope,
            workspace,
            chunks,
        }))
    }

    pub(crate) fn write_finish_embedding(
        &mut self,
        f: &EmbedFinish<'_>,
    ) -> Result<CallFinished, StoreError> {
        // One number for each vector.
        let take = match i32::try_from(f.vectors.len()) {
            Ok(0) => None,
            Ok(n) => Some(n),
            Err(_) => return Err(corrupt("a call embedded too many chunks")),
        };
        let offered = f.vectors.len() as u32;
        let mut tx = write_transaction(self.client()?)?;
        let writing = begin_write(&mut tx, f.scope, take)?;
        // A provider's own count of tokens is a number from outside. One
        // too large for the column is recorded as the largest it holds.
        let tokens = f
            .input_tokens
            .map(|count| i64::try_from(count).unwrap_or(i64::MAX));
        let recorded = tx
            .execute(
                "INSERT INTO core_memory.outbound_results
                     (call_id, result, input_tokens, output_tokens, finished_at)
                 VALUES ($1, $2, $3, $4, $5)
                 ON CONFLICT (call_id) DO NOTHING",
                &[
                    &f.call_id.0,
                    &f.result.as_str(),
                    &tokens,
                    // An embedding has no reply in tokens.
                    &tokens.map(|_| 0_i64),
                    &f.at.0,
                ],
            )
            .map_err(backend)?;
        if recorded == 0 {
            // The end of this call is recorded: an earlier try committed,
            // and its answer was lost on the way back. What that try
            // stored and settled stands, and nothing is stored twice.
            let stored: i64 = tx
                .query_one(
                    "SELECT count(*) FROM core_memory.chunk_vectors WHERE call_id = $1",
                    &[&f.call_id.0],
                )
                .map_err(backend)?
                .get(0);
            tx.commit().map_err(backend)?;
            let stored = u32::try_from(stored).unwrap_or(u32::MAX).min(offered);
            return Ok(CallFinished {
                stored,
                dropped: offered - stored,
            });
        }

        let mut finished = CallFinished {
            stored: 0,
            dropped: offered,
        };
        // A scope that stopped being active while the model worked keeps
        // its work and gets nothing stored.
        let numbered = writing
            .filter(|w| w.state == ScopeState::Active)
            .and_then(|w| w.first_seq.map(|seq| (w.workspace, seq)));
        if let Some((workspace, first_seq)) = numbered {
            // Only for chunks the call's receipt names, and only those
            // that still have their text: erasing a chunk while the model
            // worked leaves nothing behind. A chunk that already has a
            // vector of this embedder keeps it: rows are never changed.
            let insert = tx
                .prepare(
                    "INSERT INTO core_memory.chunk_vectors (
                         workspace_id, seq, chunk_id, scope, call_id, embedder_rev,
                         binding_id, model, dimensions, input_bytes, vector, embedded_at
                     )
                     SELECT $1, $2, t.chunk_id, $3, $4, $5, $6, $7, $8, $9, $10, $11
                       FROM core_memory.chunk_text AS t
                      WHERE t.chunk_id = $12 AND t.scope = $3
                        AND t.chunk_id = ANY ((SELECT o.chunk_ids
                                                 FROM core_memory.outbound_calls AS o
                                                WHERE o.call_id = $4 AND o.scope = $3
                                                  AND o.purpose = 'embed')::uuid[])
                     ON CONFLICT (chunk_id, embedder_rev) DO NOTHING",
                )
                .map_err(backend)?;
            let mut settled: Vec<Uuid> = Vec::with_capacity(f.vectors.len());
            for (n, write) in f.vectors.iter().enumerate() {
                let dimensions = dimensions(&write.vector)?;
                let stored = tx
                    .execute(
                        &insert,
                        &[
                            &workspace,
                            &(first_seq + n as i64),
                            &f.scope,
                            &f.call_id.0,
                            &f.embedder.binding_rev.as_bytes().as_slice(),
                            &f.embedder.binding_id,
                            &f.embedder.model,
                            &dimensions,
                            &to_i64(write.input_bytes, "input bytes")?,
                            &write.vector,
                            &f.at.0,
                            &write.chunk_id.0,
                        ],
                    )
                    .map_err(backend)?;
                if stored == 1 {
                    finished.stored += 1;
                    finished.dropped -= 1;
                }
                settled.push(write.chunk_id.0);
            }
            // A chunk that has its vector is no longer owed one, whichever
            // call made it. The work of an erased chunk went with it.
            tx.execute(
                "DELETE FROM core_memory.work AS w
                  WHERE w.kind = 'embed' AND w.item_id = ANY ($1)
                    AND EXISTS (SELECT 1 FROM core_memory.chunk_vectors AS v
                                 WHERE v.chunk_id = w.item_id AND v.embedder_rev = $2)",
                &[&settled, &f.embedder.binding_rev.as_bytes().as_slice()],
            )
            .map_err(backend)?;
        }
        update_work(&mut tx, f.work, None)?;
        tx.commit().map_err(backend)?;
        Ok(finished)
    }

    pub(crate) fn read_meaning_ready(
        &mut self,
        scopes: &ScopeGrants,
        destination: &str,
        embedder_rev: &Sha256Digest,
    ) -> Result<MeaningReady, StoreError> {
        // One statement, so one snapshot, and the schema version first.
        let row = read_one(
            self.client()?,
            "SELECT v.version,
                    EXISTS (SELECT 1
                              FROM pg_catalog.pg_extension AS x
                             WHERE x.extname = 'vector'
                               AND pg_catalog.has_schema_privilege(x.extnamespace, 'USAGE')),
                    EXISTS (SELECT 1
                              FROM core_memory.chunk_vectors AS c
                              JOIN core_memory.scope_destinations AS d
                                ON d.scope = c.scope AND d.destination = $2
                               AND d.state = 'granted'
                             WHERE c.embedder_rev = $1
                               AND ($3::text[] IS NULL OR c.scope = ANY ($3)))
               FROM (SELECT max(m.version) AS version
                       FROM core_memory.schema_migrations AS m) AS v",
            &[
                &embedder_rev.as_bytes().as_slice(),
                &destination,
                &named(scopes),
            ],
        )?;
        Ok(match (row.get::<_, bool>(1), row.get::<_, bool>(2)) {
            (false, _) => MeaningReady::NoVectorType,
            (true, false) => MeaningReady::NoVectors,
            (true, true) => MeaningReady::Ready,
        })
    }
}

/// The length of a vector as the store counts it.
pub(crate) fn dimensions(vector: &[f32]) -> Result<i32, StoreError> {
    to_i32(
        u32::try_from(vector.len()).map_err(|_| corrupt("a vector is too long"))?,
        "vector length",
    )
}
