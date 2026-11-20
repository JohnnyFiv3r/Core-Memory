//! Schema migrations for the `core_memory` schema.
//!
//! Migrations are SQL files embedded in the binary and applied in order,
//! each in its own transaction. A migration never changes once it has
//! shipped: the runner records a checksum of each one it applies and
//! refuses to run against a database whose history differs.

use core_memory_contract::Sha256Digest;
use core_memory_core::StoreError;
use postgres::Client;

use crate::error::{backend, corrupt};

struct Migration {
    version: u32,
    name: &'static str,
    sql: &'static str,
}

const MIGRATIONS: &[Migration] = &[
    Migration {
        version: 1,
        name: "init",
        sql: include_str!("../sql/0001_init.sql"),
    },
    Migration {
        version: 2,
        name: "recall",
        sql: include_str!("../sql/0002_recall.sql"),
    },
];

/// The schema version this build reads and writes.
pub const SCHEMA_VERSION: u32 = MIGRATIONS[MIGRATIONS.len() - 1].version;

/// Serializes migration runs on one database.
const MIGRATION_LOCK: i64 = 0x636f_7265_6d65_6d31;

/// What a migrating session needs. A migration may run longer than one
/// admission statement is allowed to, and waits longer for the writes in
/// flight to finish. The search path makes every unqualified name in a
/// migration mean the built-in one, whatever another role has created in
/// `public`.
const SESSION: &str = "
SET search_path = pg_catalog, pg_temp;
SET default_transaction_isolation = 'read committed';
SET statement_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET lock_timeout = 60000;
";

const BOOTSTRAP: &str = "
CREATE SCHEMA IF NOT EXISTS core_memory;
CREATE TABLE IF NOT EXISTS core_memory.schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    name text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);
";

/// What a migration run did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MigrationReport {
    /// The version before the run; 0 for a database with no schema.
    pub from: u32,
    pub to: u32,
}

/// The checksum of a migration, the same whichever line endings its file
/// was checked out with.
fn checksum(migration: &Migration) -> String {
    Sha256Digest::of(migration.sql.replace("\r\n", "\n").as_bytes()).to_hex()
}

/// A hash of every migration in this build. Changes whenever the schema does.
pub fn migrations_digest() -> String {
    let all: String = MIGRATIONS.iter().map(checksum).collect();
    Sha256Digest::of(all.as_bytes()).to_hex()
}

/// Brings the database to [`SCHEMA_VERSION`]. It changes the session's
/// limits, so the connection should not be used for admissions afterwards.
pub fn migrate(client: &mut Client) -> Result<MigrationReport, StoreError> {
    client.batch_execute(SESSION).map_err(backend)?;
    let encoding: String = client
        .query_one("SHOW server_encoding", &[])
        .map_err(backend)?
        .get(0);
    if encoding != "UTF8" {
        return Err(StoreError::Rejected(format!(
            "the database encoding is {encoding}; Core Memory needs UTF8"
        )));
    }
    client
        .execute("SELECT pg_advisory_lock($1)", &[&MIGRATION_LOCK])
        .map_err(backend)?;
    let result = apply_pending(client);
    // Dropping the connection would release the lock too.
    let _ = client.execute("SELECT pg_advisory_unlock($1)", &[&MIGRATION_LOCK]);
    result
}

/// Refuses a `core_memory` schema that this role did not create. Whoever
/// owns the schema or its migration table can attach code to them, and
/// that code would run as the migrating role.
fn require_ownership(client: &mut Client) -> Result<(), StoreError> {
    let row = client
        .query_one(
            "SELECT pg_get_userbyid(n.nspowner) = current_user,
                    c.relkind = 'r' AND pg_get_userbyid(c.relowner) = current_user,
                    NOT EXISTS (SELECT 1 FROM pg_trigger AS t
                                 WHERE t.tgrelid = c.oid AND NOT t.tgisinternal)
                    AND NOT EXISTS (SELECT 1 FROM pg_rewrite AS r WHERE r.ev_class = c.oid),
                    current_user::text
               FROM pg_namespace AS n
               JOIN pg_class AS c ON c.relnamespace = n.oid AND c.relname = 'schema_migrations'
              WHERE n.nspname = 'core_memory'",
            &[],
        )
        .map_err(backend)?;
    let (schema_ours, table_ours, table_plain): (bool, bool, bool) =
        (row.get(0), row.get(1), row.get(2));
    if schema_ours && table_ours && table_plain {
        return Ok(());
    }
    let role: String = row.get(3);
    Err(StoreError::Rejected(format!(
        "schema core_memory already exists and was not created by `{role}`. \
         Migrate as the role that owns it, or drop it if it should not be there"
    )))
}

fn apply_pending(client: &mut Client) -> Result<MigrationReport, StoreError> {
    client.batch_execute(BOOTSTRAP).map_err(backend)?;
    require_ownership(client)?;
    let from = verified_version(client)?;
    if from > SCHEMA_VERSION {
        return Err(newer_store(from));
    }
    for migration in MIGRATIONS.iter().filter(|m| m.version > from) {
        let context = |e: StoreError| match e {
            StoreError::Backend(why) => StoreError::Backend(format!(
                "migration {} ({}): {why}",
                migration.version, migration.name
            )),
            other => other,
        };
        let mut tx = client.transaction().map_err(backend).map_err(context)?;
        // Every transaction that writes holds this table in share mode
        // until it ends. Locking it here lets those finish, keeps new ones
        // out until the migration commits, and then shows them the new
        // version, which engines built for the old one refuse.
        tx.batch_execute("LOCK TABLE core_memory.schema_migrations IN ACCESS EXCLUSIVE MODE")
            .map_err(backend)
            .map_err(context)?;
        tx.batch_execute(migration.sql)
            .map_err(backend)
            .map_err(context)?;
        tx.execute(
            "INSERT INTO core_memory.schema_migrations (version, name, checksum)
             VALUES ($1, $2, $3)",
            &[
                &(migration.version as i32),
                &migration.name,
                &checksum(migration),
            ],
        )
        .map_err(backend)
        .map_err(context)?;
        tx.commit().map_err(backend).map_err(context)?;
    }
    Ok(MigrationReport {
        from,
        to: SCHEMA_VERSION,
    })
}

fn newer_store(version: u32) -> StoreError {
    StoreError::SchemaMismatch(format!(
        "the store is at schema version {version}, newer than this build ({SCHEMA_VERSION}); upgrade core-memory"
    ))
}

/// The schema version of the database, or `None` if it has no Core Memory
/// schema. Fails if the applied history is not a prefix of this build's.
pub fn current_version(client: &mut Client) -> Result<Option<u32>, StoreError> {
    let exists: bool = client
        .query_one(
            "SELECT to_regclass('core_memory.schema_migrations') IS NOT NULL",
            &[],
        )
        .map_err(backend)?
        .get(0);
    if !exists {
        return Ok(None);
    }
    verified_version(client).map(Some)
}

/// Fails unless the database is at exactly [`SCHEMA_VERSION`].
pub fn require_current(client: &mut Client) -> Result<(), StoreError> {
    match current_version(client)? {
        Some(SCHEMA_VERSION) => Ok(()),
        Some(v) if v > SCHEMA_VERSION => Err(newer_store(v)),
        Some(v) => Err(StoreError::SchemaMismatch(format!(
            "the store is at schema version {v}; this build needs {SCHEMA_VERSION}. Run `core-memory migrate`"
        ))),
        None => Err(StoreError::SchemaMismatch(
            "the database has no Core Memory schema. Run `core-memory migrate`".into(),
        )),
    }
}

/// The highest applied version, after checking every applied migration
/// against this build.
fn verified_version(client: &mut Client) -> Result<u32, StoreError> {
    let rows = client
        .query(
            "SELECT version, checksum FROM core_memory.schema_migrations ORDER BY version",
            &[],
        )
        .map_err(backend)?;
    let mut version = 0;
    for (i, row) in rows.iter().enumerate() {
        let applied: i32 = row.get(0);
        let applied_checksum: String = row.get(1);
        let applied = u32::try_from(applied).unwrap_or(0);
        match MIGRATIONS.get(i) {
            Some(known) if known.version == applied => {
                if checksum(known) != applied_checksum {
                    return Err(corrupt(format!(
                        "migration {applied} in the database differs from this build's"
                    )));
                }
            }
            Some(_) => {
                return Err(corrupt(format!(
                    "the database's migration history has a gap before version {applied}"
                )));
            }
            // Applied by a newer build. The callers report it.
            None => {}
        }
        version = applied;
    }
    Ok(version)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn migrations_are_numbered_from_one_without_gaps() {
        for (i, m) in MIGRATIONS.iter().enumerate() {
            assert_eq!(m.version as usize, i + 1, "{}", m.name);
        }
        assert_eq!(SCHEMA_VERSION as usize, MIGRATIONS.len());
        assert_eq!(migrations_digest().len(), 64);
    }

    #[test]
    fn a_checksum_does_not_depend_on_line_endings() {
        let unix = Migration {
            version: 1,
            name: "x",
            sql: "CREATE TABLE t (a int);\nCREATE TABLE u (b int);\n",
        };
        let windows = Migration {
            version: 1,
            name: "x",
            sql: "CREATE TABLE t (a int);\r\nCREATE TABLE u (b int);\r\n",
        };
        assert_eq!(checksum(&unix), checksum(&windows));
    }

    #[test]
    fn every_function_pins_its_search_path_with_pg_temp_last() {
        // Left out, pg_temp is searched first for types and tables, and a
        // caller's temporary objects shadow the built-in ones inside a
        // function that runs as the schema owner.
        let mut all = 0;
        for migration in MIGRATIONS {
            let functions = migration.sql.matches("CREATE FUNCTION").count()
                + migration.sql.matches("CREATE OR REPLACE FUNCTION").count();
            let pinned = migration
                .sql
                .matches("SET search_path = pg_catalog, pg_temp\nAS $$")
                .count();
            assert_eq!(functions, pinned, "{}", migration.name);
            all += functions;
        }
        assert!(all > 0);
    }

    #[test]
    fn every_function_a_role_may_call_holds_the_schema_first() {
        // A function that read a table before taking the schema lock could
        // deadlock with a migration that changes that table.
        let mut granted = 0;
        for migration in MIGRATIONS {
            for grant in migration
                .sql
                .lines()
                .filter_map(|line| line.strip_prefix("GRANT EXECUTE ON FUNCTION core_memory."))
            {
                let name = grant.split('(').next().unwrap_or_default();
                let definition = format!("CREATE FUNCTION core_memory.{name}(");
                let start = migration
                    .sql
                    .find(&definition)
                    .unwrap_or_else(|| panic!("{name} is granted and not defined"));
                let body = &migration.sql[start..];
                let first = body
                    .split_once("\nBEGIN\n")
                    .and_then(|(_, statements)| statements.lines().next())
                    .unwrap_or_default();
                assert_eq!(first.trim(), "PERFORM core_memory.hold_schema();", "{name}");
                granted += 1;
            }
        }
        assert!(granted >= 8, "{granted}");
    }
}
