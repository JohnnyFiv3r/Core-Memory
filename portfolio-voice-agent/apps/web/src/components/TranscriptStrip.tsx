export function TranscriptStrip({ lines }: { lines: string[] }) {
  return (
    <section>
      <h3 className="section-title">Live Transcript</h3>
      <div className="transcript">
        {lines.length === 0 ? (
          <small className="mini">No transcript yet.</small>
        ) : (
          lines.slice(-10).map((line, idx) => (
            <div key={`${idx}-${line.slice(0, 12)}`} className="transcript-line">
              {line}
            </div>
          ))
        )}
      </div>
    </section>
  );
}
