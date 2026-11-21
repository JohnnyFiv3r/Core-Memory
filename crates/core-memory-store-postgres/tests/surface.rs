//! What a host sees of the `core_memory` schema, compared with a
//! checked-in file: every table and column, who may do what to it, its
//! row security, its triggers and indexes, and every function with who may
//! call it.
//!
//! A host reads these tables and calls these functions from its own
//! database, so a difference here is a change to what a host depends on.
//! Before 2.0.0 the first migration still changes and this file changes
//! with it. After that a difference means a new migration, and the file
//! shows what the migration does to hosts. If the change is intended,
//! regenerate the file and read the difference:
//!
//! ```bash
//! CORE_MEMORY_UPDATE_GOLDEN=1 cargo test -p core-memory-store-postgres \
//!     --features postgres-tests --test surface
//! ```

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

use core_memory_store_postgres::testing::TestDb;
use postgres::Client;

const GOLDEN: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/golden/schema.txt");
const ROLES: [(&str, &str); 2] = [
    ("engine", "core_memory_engine"),
    ("governor", "core_memory_governor"),
];

/// The rows of a query as lines of text, one column after another.
fn lines(client: &mut Client, sql: &str) -> Vec<Vec<String>> {
    client
        .query(sql, &[])
        .unwrap()
        .iter()
        .map(|row| {
            (0..row.len())
                .map(|column| row.get::<_, Option<String>>(column).unwrap_or_default())
                .collect()
        })
        .collect()
}

/// What each role may do with a table or a function, as words.
fn rights(client: &mut Client, check: &str, object: &str, privileges: &[&str]) -> String {
    let mut said = Vec::new();
    for (name, role) in ROLES.into_iter().chain([("public", "public")]) {
        let held: Vec<&str> = privileges
            .iter()
            .copied()
            .filter(|privilege| {
                client
                    .query_one(
                        &format!("SELECT {check}($1, $2::text::{}, $3)", {
                            if check == "has_table_privilege" {
                                "regclass"
                            } else {
                                "regprocedure"
                            }
                        }),
                        &[&role, &object, privilege],
                    )
                    .unwrap()
                    .get::<_, bool>(0)
            })
            .collect();
        // A function has one thing a role may do with it, and the role's
        // name says it.
        if privileges == ["EXECUTE"] && !held.is_empty() {
            said.push(name.to_string());
        } else if !held.is_empty() {
            said.push(format!("{name} {}", held.join(" ").to_lowercase()));
        }
    }
    if said.is_empty() {
        "no role".to_string()
    } else {
        said.join("; ")
    }
}

#[test]
fn the_schema_a_host_sees_matches_the_golden_file() {
    let db = TestDb::create();
    let mut client = db.connect();
    let mut out: Vec<String> = Vec::new();

    let tables = lines(
        &mut client,
        "SELECT c.relname::text, c.relrowsecurity::text
           FROM pg_catalog.pg_class AS c
           JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
          WHERE n.nspname = 'core_memory' AND c.relkind = 'r'
          ORDER BY c.relname",
    );
    for table in &tables {
        let (name, row_security) = (&table[0], &table[1]);
        out.push(format!("table {name}"));
        for column in lines(
            &mut client,
            &format!(
                "SELECT a.attname::text, pg_catalog.format_type(a.atttypid, a.atttypmod),
                        CASE WHEN a.attnotnull THEN 'not null' ELSE 'null' END
                   FROM pg_catalog.pg_attribute AS a
                  WHERE a.attrelid = 'core_memory.{name}'::regclass AND a.attnum > 0
                    AND NOT a.attisdropped
                  ORDER BY a.attnum"
            ),
        ) {
            out.push(format!("  {} {} {}", column[0], column[1], column[2]));
        }
        out.push(format!(
            "  may: {}",
            rights(
                &mut client,
                "has_table_privilege",
                &format!("core_memory.{name}"),
                &["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"],
            )
        ));
        let policies = lines(
            &mut client,
            &format!(
                "SELECT p.policyname::text, p.cmd, array_to_string(p.roles, ',')
                   FROM pg_catalog.pg_policies AS p
                  WHERE p.schemaname = 'core_memory' AND p.tablename = '{name}'
                  ORDER BY p.policyname"
            ),
        );
        let policies: Vec<String> = policies
            .iter()
            .map(|p| format!("{} ({} to {})", p[0], p[1].to_lowercase(), p[2]))
            .collect();
        out.push(format!(
            "  row security: {}; policies: {}",
            if row_security == "true" { "on" } else { "OFF" },
            if policies.is_empty() {
                "none".to_string()
            } else {
                policies.join(", ")
            }
        ));
        // A trigger as PostgreSQL writes it back: when it fires, on what,
        // and the function it calls.
        for trigger in lines(
            &mut client,
            &format!(
                "SELECT pg_catalog.pg_get_triggerdef(t.oid)
                   FROM pg_catalog.pg_trigger AS t
                  WHERE t.tgrelid = 'core_memory.{name}'::regclass AND NOT t.tgisinternal
                  ORDER BY t.tgname"
            ),
        ) {
            let written = trigger[0]
                .trim_start_matches("CREATE TRIGGER ")
                .replace(&format!(" ON core_memory.{name}"), "");
            out.push(format!("  trigger {written}"));
        }
        for index in lines(
            &mut client,
            &format!(
                "SELECT i.indexrelid::regclass::text,
                        CASE WHEN i.indisprimary THEN 'primary key'
                             WHEN i.indisunique THEN 'unique' ELSE 'index' END,
                        array_to_string(ARRAY(
                            SELECT pg_catalog.pg_get_indexdef(i.indexrelid, k, true)
                              FROM generate_series(1, i.indnkeyatts) AS k), ', '),
                        CASE WHEN i.indpred IS NULL THEN '' ELSE 'partial' END
                   FROM pg_catalog.pg_index AS i
                  WHERE i.indrelid = 'core_memory.{name}'::regclass
                  ORDER BY 1"
            ),
        ) {
            out.push(format!(
                "  {} {} ({}){}",
                index[1],
                index[0].trim_start_matches("core_memory."),
                index[2],
                if index[3].is_empty() { "" } else { " partial" }
            ));
        }
        out.push(String::new());
    }

    // A view runs with its reader's rights or its owner's: with its
    // owner's, it would show rows past the reader's own policies.
    for view in lines(
        &mut client,
        "SELECT c.relname::text,
                CASE WHEN 'security_invoker=true' = ANY (coalesce(c.reloptions, '{}'))
                     THEN 'as its reader' ELSE 'AS ITS OWNER' END
           FROM pg_catalog.pg_class AS c
           JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
          WHERE n.nspname = 'core_memory' AND c.relkind IN ('v', 'm')
          ORDER BY c.relname",
    ) {
        let name = &view[0];
        out.push(format!("view {name} (reads {})", view[1]));
        for column in lines(
            &mut client,
            &format!(
                "SELECT a.attname::text, pg_catalog.format_type(a.atttypid, a.atttypmod)
                   FROM pg_catalog.pg_attribute AS a
                  WHERE a.attrelid = 'core_memory.{name}'::regclass AND a.attnum > 0
                    AND NOT a.attisdropped
                  ORDER BY a.attnum"
            ),
        ) {
            out.push(format!("  {} {}", column[0], column[1]));
        }
        out.push(format!(
            "  may: {}",
            rights(
                &mut client,
                "has_table_privilege",
                &format!("core_memory.{name}"),
                &["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"],
            )
        ));
        out.push(String::new());
    }

    for function in lines(
        &mut client,
        "SELECT p.oid::regprocedure::text, p.proname::text,
                pg_catalog.pg_get_function_identity_arguments(p.oid),
                pg_catalog.pg_get_function_result(p.oid),
                CASE WHEN p.prosecdef THEN 'as its owner' ELSE 'as its caller' END,
                array_to_string(p.proconfig, ' ')
           FROM pg_catalog.pg_proc AS p
           JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
          WHERE n.nspname = 'core_memory'
          ORDER BY p.proname, 3",
    ) {
        out.push(format!(
            "function {}({}) returns {}",
            function[1], function[2], function[3]
        ));
        out.push(format!("  runs {}; {}", function[4], function[5]));
        out.push(format!(
            "  may call: {}",
            rights(
                &mut client,
                "has_function_privilege",
                &function[0],
                &["EXECUTE"]
            )
        ));
    }
    out.push(String::new());

    let text = out.join("\n");
    if std::env::var_os("CORE_MEMORY_UPDATE_GOLDEN").is_some() {
        std::fs::write(GOLDEN, &text).unwrap();
    }
    let golden = std::fs::read_to_string(GOLDEN).unwrap();
    for (n, (got, want)) in text.lines().zip(golden.lines()).enumerate() {
        assert_eq!(got, want, "line {} differs from {GOLDEN}", n + 1);
    }
    assert_eq!(
        text.lines().count(),
        golden.lines().count(),
        "the number of lines changed"
    );
}
