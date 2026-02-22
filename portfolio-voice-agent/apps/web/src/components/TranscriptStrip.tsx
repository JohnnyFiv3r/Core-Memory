export function TranscriptStrip({ lines }: { lines: string[] }) {
  return (
    <section style={{ marginTop: 20 }}>
      <h3 style={{ margin: "0 0 8px 0" }}>Live Transcript</h3>
      <div
        style={{
          border: "1px solid #e4e4e7",
          borderRadius: 10,
          padding: 10,
          background: "#fafafa",
          minHeight: 88,
          maxHeight: 180,
          overflowY: "auto"
        }}
      >
        {lines.length === 0 ? (
          <small style={{ color: "#71717a" }}>No transcript yet.</small>
        ) : (
          lines.slice(-6).map((line, idx) => (
            <div key={`${idx}-${line.slice(0, 12)}`} style={{ fontSize: 13, marginBottom: 6 }}>
              {line}
            </div>
          ))
        )}
      </div>
    </section>
  );
}
