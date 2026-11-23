//! Test support: one fresh database per test, cloned from a migrated
//! template. Enabled by the `testing` feature.

// A test helper that cannot set up its database should stop the test.
#![allow(clippy::unwrap_used)]

use std::hash::{BuildHasher, RandomState};
use std::sync::OnceLock;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use postgres::{Client, NoTls};

use crate::connect::ConnectConfig;
use crate::migrate::{migrate, migrations_digest};
use crate::store::PostgresStore;

/// The environment variable that names the server tests may create
/// databases on, as a URL ending in a database name.
pub const TEST_DATABASE_URL: &str = "CORE_MEMORY_TEST_DATABASE_URL";

/// The application name of every connection these helpers open as the
/// database owner, so a test can tell its own sessions from the engine's
/// whatever application name the test URL gives.
pub const ADMIN_APPLICATION: &str = "cm_test_admin";

static COUNTER: AtomicU64 = AtomicU64::new(0);
static TEMPLATE: OnceLock<String> = OnceLock::new();
static RUN: OnceLock<String> = OnceLock::new();

/// PostgreSQL's longest identifier, in bytes.
const MAX_IDENTIFIER: usize = 63;

/// This process's run ID: 16 hex digits that no other test run on the server
/// shares, even one with the same process ID in another PID namespace.
/// `RandomState` is seeded from the operating system's random source.
fn run_id() -> &'static str {
    RUN.get_or_init(|| {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let id = RandomState::new().hash_one((std::process::id(), nanos));
        format!("{id:016x}")
    })
}

/// `{prefix}_{run}_{n}`, checked to fit PostgreSQL's identifier limit.
fn name_for(prefix: &str, run: &str, n: u64) -> String {
    let name = format!("{prefix}_{run}_{n}");
    assert!(
        name.len() <= MAX_IDENTIFIER,
        "test object name {name} is longer than PostgreSQL's {MAX_IDENTIFIER} bytes"
    );
    name
}

/// A database or role name that no other test, in this run or in any other run
/// on the same server, uses. `prefix` must be a lower-case SQL identifier, such
/// as `cm_test` or `cm_t_reader_engine`, so the name needs no quoting.
pub fn unique_name(prefix: &str) -> String {
    name_for(prefix, run_id(), COUNTER.fetch_add(1, Ordering::SeqCst))
}

/// A database that exists for one test.
#[derive(Debug)]
pub struct TestDb {
    admin_url: String,
    name: String,
    /// Connection URL of the test database.
    pub url: String,
}

fn admin_url() -> String {
    std::env::var(TEST_DATABASE_URL).unwrap_or_else(|_| {
        panic!(
            "set {TEST_DATABASE_URL} to a PostgreSQL URL the tests may create databases on, \
             for example postgresql://postgres:secret@127.0.0.1:5432/postgres"
        )
    })
}

/// `url` with its database name replaced.
fn with_database(url: &str, name: &str) -> String {
    let (base, query) = match url.split_once('?') {
        Some((base, query)) => (base, Some(query)),
        None => (url, None),
    };
    let authority_end = base.find("://").map_or(0, |i| i + 3);
    let path_start = base[authority_end..]
        .find('/')
        .map_or(base.len(), |i| authority_end + i);
    let mut out = format!("{}/{name}", &base[..path_start]);
    if let Some(query) = query {
        out.push('?');
        out.push_str(query);
    }
    out
}

fn admin(url: &str) -> Client {
    let mut config: postgres::Config = url
        .parse()
        .unwrap_or_else(|e| panic!("cannot read {TEST_DATABASE_URL}: {e}"));
    config.application_name(ADMIN_APPLICATION);
    config
        .connect(NoTls)
        .unwrap_or_else(|e| panic!("cannot connect to {TEST_DATABASE_URL}: {e}"))
}

/// Creates the migrated template once per schema, and returns its name.
fn template(admin_url: &str) -> &'static str {
    TEMPLATE.get_or_init(|| {
        let name = format!("cm_template_{}", &migrations_digest()[..12]);
        let mut client = admin(admin_url);
        // Other test binaries may be building the same template.
        client
            .execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", &[&name])
            .unwrap();
        let exists: bool = client
            .query_one(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)",
                &[&name],
            )
            .unwrap()
            .get(0);
        if !exists {
            let building = format!("{name}_building");
            // Each statement goes alone: neither may run in a transaction block.
            client
                .batch_execute(&format!("DROP DATABASE IF EXISTS {building} WITH (FORCE)"))
                .unwrap();
            client
                .batch_execute(&format!("CREATE DATABASE {building}"))
                .unwrap();
            {
                let mut db = admin(&with_database(admin_url, &building));
                migrate(&mut db).unwrap();
            }
            client
                .batch_execute(&format!("ALTER DATABASE {building} RENAME TO {name}"))
                .unwrap();
        }
        client
            .execute(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
                &[&name],
            )
            .unwrap();
        name
    })
}

/// Creates `name`, and panics if it exists. A database that is already there
/// is another run's, so it is never dropped to make room.
fn create_database(admin_url: &str, name: &str, options: &str) {
    admin(admin_url)
        .batch_execute(&format!("CREATE DATABASE {name} {options}"))
        .unwrap();
}

impl TestDb {
    /// A migrated, empty database. Panics with instructions if
    /// [`TEST_DATABASE_URL`] is not set.
    pub fn create() -> Self {
        let admin_url = admin_url();
        let template = template(&admin_url);
        let name = unique_name("cm_test");
        create_database(&admin_url, &name, &format!("TEMPLATE {template}"));
        Self {
            url: with_database(&admin_url, &name),
            admin_url,
            name,
        }
    }

    /// A database with no Core Memory schema.
    pub fn create_empty() -> Self {
        let admin_url = admin_url();
        let name = unique_name("cm_test");
        create_database(&admin_url, &name, "");
        Self {
            url: with_database(&admin_url, &name),
            admin_url,
            name,
        }
    }

    pub fn config(&self) -> ConnectConfig {
        ConnectConfig::new(self.url.clone())
    }

    /// A new store on this database.
    pub fn store(&self) -> PostgresStore {
        PostgresStore::new(&self.config()).unwrap()
    }

    /// A plain connection, for inspecting tables as the database owner.
    pub fn connect(&self) -> Client {
        admin(&self.url)
    }

    /// This database's URL with `database` in place of its name, every query
    /// option kept as it was.
    pub fn url_for(&self, database: &str) -> String {
        with_database(&self.url, database)
    }
}

impl Drop for TestDb {
    fn drop(&mut self) {
        if let Ok(mut client) = Client::connect(&self.admin_url, NoTls) {
            let _ = client.batch_execute(&format!(
                "DROP DATABASE IF EXISTS {} WITH (FORCE)",
                self.name
            ));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{MAX_IDENTIFIER, name_for, unique_name, with_database};

    #[test]
    fn a_slash_or_an_escape_in_an_option_is_kept() {
        assert_eq!(
            with_database(
                "postgresql://u:p@h:5432/postgres?sslmode=disable&application_name=qa/path",
                "t1"
            ),
            "postgresql://u:p@h:5432/t1?sslmode=disable&application_name=qa/path"
        );
        assert_eq!(
            with_database(
                "postgresql://u:p@h:5432/postgres?application_name=qa114%2Fpath%20x",
                "t1"
            ),
            "postgresql://u:p@h:5432/t1?application_name=qa114%2Fpath%20x"
        );
    }

    #[test]
    fn the_database_name_is_replaced_and_the_rest_kept() {
        assert_eq!(
            with_database("postgresql://u:p@127.0.0.1:5432/postgres", "t1"),
            "postgresql://u:p@127.0.0.1:5432/t1"
        );
        assert_eq!(
            with_database("postgresql://u:p@host/postgres?sslmode=disable", "t1"),
            "postgresql://u:p@host/t1?sslmode=disable"
        );
        assert_eq!(
            with_database("postgresql://host", "t1"),
            "postgresql://host/t1"
        );
    }

    #[test]
    fn test_names_in_one_run_never_repeat() {
        let (first, second) = (unique_name("cm_test"), unique_name("cm_test"));
        assert_ne!(first, second);
        for name in [first, second] {
            let rest = name.strip_prefix("cm_test_").unwrap();
            let (run, _) = rest.split_once('_').unwrap();
            assert_eq!(run.len(), 16);
            assert!(
                run.bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            );
        }
    }

    #[test]
    fn the_longest_test_object_name_fits_postgres_identifiers() {
        // `cm_t_verdict_reader` is the longest prefix the suite uses.
        let name = name_for("cm_t_verdict_reader", &"f".repeat(16), u64::MAX);
        assert!(name.len() <= MAX_IDENTIFIER, "{} bytes", name.len());
    }

    #[test]
    fn a_name_of_exactly_the_limit_is_accepted() {
        let prefix = "p".repeat(MAX_IDENTIFIER - 16 - 2 - 1);
        assert_eq!(name_for(&prefix, &"0".repeat(16), 7).len(), MAX_IDENTIFIER);
    }

    #[test]
    #[should_panic(expected = "longer than PostgreSQL")]
    fn a_name_over_the_limit_is_refused() {
        let prefix = "p".repeat(MAX_IDENTIFIER - 16 - 2);
        name_for(&prefix, &"0".repeat(16), 7);
    }

    #[cfg(feature = "postgres-tests")]
    #[test]
    fn a_database_already_at_a_name_is_left_alone() {
        let admin_url = super::admin_url();
        let name = unique_name("cm_test");
        super::create_database(&admin_url, &name, "");
        let oid = |name: &str| -> u32 {
            super::admin(&admin_url)
                .query_one("SELECT oid FROM pg_database WHERE datname = $1", &[&name])
                .unwrap()
                .get(0)
        };
        let before = oid(&name);
        let refused = std::panic::catch_unwind(|| super::create_database(&admin_url, &name, ""));
        let after = oid(&name);
        super::admin(&admin_url)
            .batch_execute(&format!("DROP DATABASE {name} WITH (FORCE)"))
            .unwrap();
        assert!(refused.is_err(), "a second CREATE DATABASE went through");
        assert_eq!(before, after, "the database was replaced");
    }
}
