-- Core Memory engine schema, version 3: views for a host that reads the
-- engine's tables in place.
--
-- A host that runs the engine in its own database reads the core_memory
-- tables directly, with grants and row-security policies of its own. It
-- keeps no copy, so a correction or an erasure is visible to it the
-- moment the engine commits it. These two views save it from repeating
-- rules the engine owns, and change with the engine when they do:
--
--   bead_state  every live bead: whether it is current, the bead whose
--               arrow ended it and that arrow, whether it is in dispute,
--               and the type its chunk was last read as.
--   bead_text   the stored text of every live bead of source content,
--               chunk by chunk.
--
-- They are recall's rules, as recall applies them. A bead is current while
-- no repl or withdraws edge points at it, whatever has become of the bead
-- that ended it: an erased statement's replacement stays in force. A
-- current bead is disputed while a !contradicts edge joins it to another
-- live, current bead.
--
-- Both views run with the rights of the role that reads them
-- (security_invoker), so that role's own grants and policies on the tables
-- underneath decide every row it sees, and no view shows more than its
-- tables would. A reader needs SELECT on the view and on each table it
-- reads, and a policy on each of those tables: bead_state reads beads,
-- edges and chunk_reads; bead_text reads beads, chunks and chunk_text. A
-- policy that hides an edge also hides what the edge says: a bead ended by
-- a bead the reader may not see is ended only if the reader may see the
-- edge that ended it.
--
-- Nothing is granted here, to PUBLIC or to the engine's roles. The host
-- grants what it reads to its own roles.
--
-- The arrows that end a bead and that put two in dispute are named here,
-- as the engine's own indexes name them (edges_ended_once_idx,
-- edges_disputes_idx). A test checks them against the flags of the arrows
-- directory. Changing the directory is a migration, and that migration
-- replaces these views too.

CREATE VIEW core_memory.bead_state WITH (security_invoker = true) AS
SELECT b.workspace_id,
       b.seq,
       b.bead_id,
       b.scope,
       b.kind,
       b.event_id,
       b.chunk_id,
       b.created_by,
       b.time_at,
       b.time_basis,
       ender.from_bead IS NULL AS current,
       ender.from_bead AS ended_by,
       ender.arrow AS ended_with,
       ender.from_bead IS NULL AND EXISTS (
           SELECT 1
             FROM core_memory.edges AS d
             JOIN core_memory.beads AS o
               ON o.bead_id = CASE WHEN d.from_bead = b.bead_id
                                   THEN d.to_bead ELSE d.from_bead END
            WHERE (d.from_bead = b.bead_id OR d.to_bead = b.bead_id)
              AND d.arrow = '!contradicts'
              AND o.state = 'live'
              AND NOT EXISTS (
                  SELECT 1
                    FROM core_memory.edges AS oe
                   WHERE oe.to_bead = o.bead_id
                     AND oe.arrow IN ('repl', 'withdraws'))
       ) AS disputed,
       (SELECT r.type_key
          FROM core_memory.chunk_reads AS r
         WHERE b.kind = 'chunk' AND r.chunk_id = b.chunk_id
         ORDER BY r.seq DESC
         LIMIT 1) AS type_key
  FROM core_memory.beads AS b
  LEFT JOIN core_memory.edges AS ender
    ON ender.to_bead = b.bead_id AND ender.arrow IN ('repl', 'withdraws')
 WHERE b.state = 'live';

-- A message's chunk is a bead of its own; a tool call is one bead with
-- every chunk of its event.
CREATE VIEW core_memory.bead_text WITH (security_invoker = true) AS
SELECT b.workspace_id, b.bead_id, b.scope, c.ordinal, c.chunk_id, t.text
  FROM core_memory.beads AS b
  JOIN core_memory.chunks AS c ON c.chunk_id = b.chunk_id
  JOIN core_memory.chunk_text AS t ON t.chunk_id = c.chunk_id
 WHERE b.state = 'live' AND b.kind = 'chunk'
UNION ALL
SELECT b.workspace_id, b.bead_id, b.scope, c.ordinal, c.chunk_id, t.text
  FROM core_memory.beads AS b
  JOIN core_memory.chunks AS c ON c.event_id = b.event_id
  JOIN core_memory.chunk_text AS t ON t.chunk_id = c.chunk_id
 WHERE b.state = 'live' AND b.kind = 'tool';

REVOKE ALL ON core_memory.bead_state, core_memory.bead_text FROM PUBLIC;
