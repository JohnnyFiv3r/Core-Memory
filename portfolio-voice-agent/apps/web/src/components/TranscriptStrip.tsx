import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function TranscriptStrip({ lines }: { lines: string[] }) {
  return (
    <Card className="border-border/80 bg-card/40">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm uppercase tracking-widest text-muted-foreground">Live Transcript</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="max-h-60 min-h-32 space-y-2 overflow-y-auto rounded-md border border-border/70 bg-background/60 p-3 text-sm">
          {lines.length === 0 ? (
            <small className="text-muted-foreground">No transcript yet.</small>
          ) : (
            lines.slice(-10).map((line, idx) => (
              <div key={`${idx}-${line.slice(0, 12)}`} className="text-foreground/90">
                {line}
              </div>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
}
