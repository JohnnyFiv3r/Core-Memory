import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export function TranscriptStrip({ lines }) {
    return (_jsxs("section", { children: [_jsx("h3", { className: "section-title", children: "Live Transcript" }), _jsx("div", { className: "transcript", children: lines.length === 0 ? (_jsx("small", { className: "mini", children: "No transcript yet." })) : (lines.slice(-10).map((line, idx) => (_jsx("div", { className: "transcript-line", children: line }, `${idx}-${line.slice(0, 12)}`)))) })] }));
}
