//! Injected time (ARC-04).

use std::fmt;
use std::sync::atomic::{AtomicI64, Ordering};

use core_memory_contract::Timestamp;
use time::OffsetDateTime;

/// A source of the current time. The engine never reads the system clock
/// directly, so tests and replays can control it.
pub trait Clock: Send + Sync + fmt::Debug {
    fn now(&self) -> Timestamp;
}

/// Wall-clock time in UTC.
#[derive(Debug, Default, Clone, Copy)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> Timestamp {
        Timestamp(OffsetDateTime::now_utc())
    }
}

/// A clock that only moves when told to. For tests and deterministic replay.
#[derive(Debug)]
pub struct ManualClock {
    unix_millis: AtomicI64,
}

impl ManualClock {
    pub fn at_unix_millis(millis: i64) -> Self {
        Self {
            unix_millis: AtomicI64::new(millis),
        }
    }

    pub fn advance_millis(&self, millis: i64) {
        self.unix_millis.fetch_add(millis, Ordering::SeqCst);
    }
}

impl Clock for ManualClock {
    fn now(&self) -> Timestamp {
        Timestamp::from_unix_millis(self.unix_millis.load(Ordering::SeqCst))
    }
}
