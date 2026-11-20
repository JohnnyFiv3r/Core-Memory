//! A check that a JSON document means exactly one thing, to every reader.
//!
//! `serde_json` keeps the last of two equal keys and reads every number it
//! can. A document that relies on either is ambiguous: two readers may see
//! two values, and two different inputs may hash the same once canonical
//! JSON (RFC 8785) has rounded their numbers. The engine refuses such
//! input instead of choosing a reading.
//!
//! It also refuses a document nested deeper than [`MAX_DEPTH`]. Parsers
//! stop at some depth, `serde_json` at 128, and a value that was accepted
//! is later read back from inside the records that carry it.

use std::collections::HashSet;
use std::fmt;

use serde::de::{self, DeserializeSeed, Deserializer, MapAccess, SeqAccess, Visitor};

/// Largest integer magnitude a canonical JSON number holds exactly: 2^53.
/// RFC 8785 writes numbers as IEEE doubles.
pub const MAX_EXACT_INTEGER: u64 = 1 << 53;

/// Deepest nesting of arrays and objects an accepted document may have,
/// counted from the outermost value a caller sends.
pub const MAX_DEPTH: usize = 64;

/// Which integers a document may hold.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Integers {
    /// Any integer. For values parsed into typed fields and never written
    /// as canonical JSON.
    Any,
    /// Only integers a canonical JSON number holds exactly. For values that
    /// are hashed or stored in canonical form.
    Exact,
}

/// Refuses duplicate object keys at any depth, nesting deeper than
/// [`MAX_DEPTH`] and, with [`Integers::Exact`], integers that canonical
/// JSON would round.
pub fn check(json: &str, integers: Integers) -> Result<(), serde_json::Error> {
    check_inside(json, integers, 0)
}

/// As [`check`], for a value that the caller found `inside` that many
/// arrays and objects of a larger document. The depth limit counts them.
pub fn check_inside(
    json: &str,
    integers: Integers,
    inside: usize,
) -> Result<(), serde_json::Error> {
    let mut de = serde_json::Deserializer::from_str(json);
    Strict {
        integers,
        depth: inside,
    }
    .deserialize(&mut de)?;
    de.end()
}

/// As [`check_inside`], for a value that is already parsed, as one built
/// in code is. Such a value has no duplicate keys left to find; its
/// nesting and its integers are checked as a document's are.
pub fn check_value_inside(
    value: &serde_json::Value,
    integers: Integers,
    inside: usize,
) -> Result<(), serde_json::Error> {
    Strict {
        integers,
        depth: inside,
    }
    .deserialize(value)
}

#[derive(Clone, Copy)]
struct Strict {
    integers: Integers,
    /// How many arrays and objects enclose the value being read.
    depth: usize,
}

impl Strict {
    fn inexact<E: de::Error>(self, number: impl fmt::Display) -> E {
        E::custom(format!(
            "number {number} cannot be represented exactly; send it as a string"
        ))
    }

    /// The checker for what an array or object at this depth holds.
    fn within<E: de::Error>(self) -> Result<Self, E> {
        if self.depth == MAX_DEPTH {
            return Err(E::custom(format!(
                "nested more than {MAX_DEPTH} levels deep"
            )));
        }
        Ok(Self {
            depth: self.depth + 1,
            ..self
        })
    }
}

impl<'de> DeserializeSeed<'de> for Strict {
    type Value = ();

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
        deserializer.deserialize_any(self)
    }
}

impl<'de> Visitor<'de> for Strict {
    type Value = ();

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("any JSON value")
    }

    fn visit_bool<E>(self, _: bool) -> Result<(), E> {
        Ok(())
    }

    fn visit_i64<E: de::Error>(self, v: i64) -> Result<(), E> {
        if self.integers == Integers::Exact && v.unsigned_abs() > MAX_EXACT_INTEGER {
            return Err(self.inexact(v));
        }
        Ok(())
    }

    fn visit_u64<E: de::Error>(self, v: u64) -> Result<(), E> {
        if self.integers == Integers::Exact && v > MAX_EXACT_INTEGER {
            return Err(self.inexact(v));
        }
        Ok(())
    }

    fn visit_f64<E: de::Error>(self, v: f64) -> Result<(), E> {
        // Integers beyond u64 and i64 arrive here as floats, and canonical
        // form would print a rounded value. Decimals beyond double
        // precision are a documented RFC 8785 limit.
        if self.integers == Integers::Exact
            && v.fract() == 0.0
            && v.abs() > MAX_EXACT_INTEGER as f64
        {
            return Err(self.inexact(v));
        }
        Ok(())
    }

    fn visit_str<E>(self, _: &str) -> Result<(), E> {
        Ok(())
    }

    fn visit_unit<E>(self) -> Result<(), E> {
        Ok(())
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<(), A::Error> {
        let within = self.within()?;
        while seq.next_element_seed(within)?.is_some() {}
        Ok(())
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
        let within = self.within()?;
        let mut seen = HashSet::new();
        while let Some(key) = map.next_key::<String>()? {
            if seen.contains(&key) {
                return Err(de::Error::custom(format!("duplicate key `{key}`")));
            }
            seen.insert(key);
            map.next_value_seed(within)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duplicate_keys_are_refused_at_any_depth() {
        for integers in [Integers::Any, Integers::Exact] {
            assert!(check(r#"{"a": 1, "b": [{"c": 1}, {"c": 2}]}"#, integers).is_ok());
            let err = check(r#"{"a": [1, {"k": 1, "k": 2}]}"#, integers).unwrap_err();
            assert!(err.to_string().contains("duplicate key `k`"), "{err}");
            assert!(check(r#"{"a": 1, "a": 1}"#, integers).is_err());
        }
    }

    #[test]
    fn inexact_integers_are_refused_only_when_asked() {
        for big in [
            "9007199254740993",
            "-9007199254740993",
            // Larger than u64: parsed as a float.
            "18446744073709551617",
            "1e300",
        ] {
            let doc = format!(r#"{{"id": [{big}]}}"#);
            assert!(check(&doc, Integers::Exact).is_err(), "{big}");
            assert!(check(&doc, Integers::Any).is_ok(), "{big}");
        }
        for fine in ["9007199254740992", "-9007199254740992", "0.25", "1.5e-7"] {
            assert!(
                check(&format!("[{fine}]"), Integers::Exact).is_ok(),
                "{fine}"
            );
        }
    }

    fn nested(levels: usize) -> String {
        format!("{}0{}", "[".repeat(levels), "]".repeat(levels))
    }

    #[test]
    fn nesting_is_bounded_well_inside_what_parsers_read() {
        for integers in [Integers::Any, Integers::Exact] {
            assert!(check("0", integers).is_ok());
            assert!(check(&nested(MAX_DEPTH), integers).is_ok());
            let err = check(&nested(MAX_DEPTH + 1), integers).unwrap_err();
            assert!(err.to_string().contains("nested more than 64"), "{err}");
            // Objects count as arrays do, and so does what encloses the value.
            let objects = format!(
                "{}0{}",
                r#"{"a":"#.repeat(MAX_DEPTH + 1),
                "}".repeat(MAX_DEPTH + 1)
            );
            assert!(check(&objects, integers).is_err());
            assert!(check_inside(&nested(MAX_DEPTH - 2), integers, 2).is_ok());
            assert!(check_inside(&nested(MAX_DEPTH - 1), integers, 2).is_err());
            // Far past any limit, the answer is still an error.
            assert!(check(&nested(100_000), integers).is_err());
        }
        // What is accepted reads back when it is stored two levels down.
        let stored = format!(r#"{{"extra": {{"field": {}}}}}"#, nested(MAX_DEPTH));
        assert!(serde_json::from_str::<serde_json::Value>(&stored).is_ok());
    }

    #[test]
    fn a_parsed_value_is_held_to_the_same_limits() {
        let parsed = |json: &str| serde_json::from_str::<serde_json::Value>(json).unwrap();
        for integers in [Integers::Any, Integers::Exact] {
            assert!(check_value_inside(&parsed(&nested(MAX_DEPTH)), integers, 0).is_ok());
            assert!(check_value_inside(&parsed(&nested(MAX_DEPTH - 1)), integers, 1).is_ok());
            let err = check_value_inside(&parsed(&nested(MAX_DEPTH)), integers, 1).unwrap_err();
            assert!(err.to_string().contains("nested more than 64"), "{err}");
        }
        // A value built in code can be deeper than any parser would read.
        let mut deep = serde_json::json!(0);
        for _ in 0..1_000 {
            deep = serde_json::json!([deep]);
        }
        assert!(check_value_inside(&deep, Integers::Any, 0).is_err());
        // Dropping a value this deep one level at a time needs no deep stack.
        while let serde_json::Value::Array(mut inner) = deep {
            deep = inner.pop().unwrap_or_default();
        }

        for big in ["9007199254740993", "-9007199254740993", "1e300"] {
            let value = parsed(&format!(r#"{{"id": [{big}]}}"#));
            assert!(
                check_value_inside(&value, Integers::Exact, 0).is_err(),
                "{big}"
            );
            assert!(
                check_value_inside(&value, Integers::Any, 0).is_ok(),
                "{big}"
            );
        }
        let fine = parsed(r#"{"id": [9007199254740992, -9007199254740992, 0.25]}"#);
        assert!(check_value_inside(&fine, Integers::Exact, 0).is_ok());
    }

    #[test]
    fn only_one_complete_document_is_accepted() {
        assert!(check("{} {}", Integers::Any).is_err());
        assert!(check("{", Integers::Any).is_err());
        assert!(check("", Integers::Any).is_err());
        assert!(check(" null ", Integers::Any).is_ok());
    }
}
