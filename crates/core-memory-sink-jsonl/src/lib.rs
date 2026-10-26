//! Appends records to a JSON Lines file, one record per line.
//!
//! Delivery is at least once: after a crash between a write and the cursor
//! update, records are appended again. Readers deduplicate by `record_key`.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use core_memory_core::sink::{Sink, SinkCaps, SinkError, WriteOutcome};
use core_memory_core::store::OutboxEntry;

const RETRY_AFTER_MS: u64 = 1_000;

#[derive(Debug)]
pub struct JsonlSink {
    id: String,
    path: PathBuf,
    file: File,
    max_record_bytes: u64,
}

impl JsonlSink {
    /// Opens `path` for appending, creating it if needed.
    pub fn open(
        id: impl Into<String>,
        path: impl AsRef<Path>,
        max_record_bytes: u64,
    ) -> std::io::Result<Self> {
        let path = path.as_ref().to_path_buf();
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        Ok(Self {
            id: id.into(),
            path,
            file,
            max_record_bytes,
        })
    }

    fn error(&self, e: std::io::Error) -> SinkError {
        SinkError {
            sink: self.id.clone(),
            message: format!("{}: {e}", self.path.display()),
            retry_after_ms: RETRY_AFTER_MS,
        }
    }
}

impl Sink for JsonlSink {
    fn id(&self) -> &str {
        &self.id
    }

    fn caps(&self) -> SinkCaps {
        SinkCaps::accepts_all_v1(self.max_record_bytes)
    }

    fn write(&mut self, batch: &[OutboxEntry]) -> Result<Vec<WriteOutcome>, SinkError> {
        let mut buf = Vec::new();
        for entry in batch {
            buf.extend_from_slice(entry.record_json.as_bytes());
            buf.push(b'\n');
        }
        self.file.write_all(&buf).map_err(|e| self.error(e))?;
        self.file.sync_data().map_err(|e| self.error(e))?;
        Ok(vec![WriteOutcome::Accepted; batch.len()])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn appends_one_line_per_record() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("out.jsonl");
        let mut sink = JsonlSink::open("out", &path, 1 << 20).unwrap();
        let entries: Vec<_> = (1..=2)
            .map(|seq| OutboxEntry {
                seq,
                record_key: format!("k{seq}"),
                record_json: format!(r#"{{"n":{seq}}}"#),
            })
            .collect();
        assert_eq!(
            sink.write(&entries).unwrap(),
            vec![WriteOutcome::Accepted; 2]
        );
        sink.write(&entries[..1]).unwrap();
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            "{\"n\":1}\n{\"n\":2}\n{\"n\":1}\n"
        );
    }
}
