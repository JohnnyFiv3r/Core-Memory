import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const STORY_PATH_CANDIDATES = [
    path.resolve(process.cwd(), "apps/voice-server/context/story-bank.json"),
    path.resolve(process.cwd(), "context/story-bank.json"),
    path.resolve(__dirname, "../../context/story-bank.json")
];
let storyCache = null;
function resolveStoryPath() {
    for (const p of STORY_PATH_CANDIDATES) {
        if (fs.existsSync(p))
            return p;
    }
    return null;
}
function loadStories() {
    if (storyCache)
        return storyCache;
    try {
        const storyPath = resolveStoryPath();
        if (!storyPath) {
            storyCache = [];
            return storyCache;
        }
        const raw = fs.readFileSync(storyPath, "utf8");
        storyCache = JSON.parse(raw);
    }
    catch {
        storyCache = [];
    }
    return storyCache;
}
const STORY_INTENT_HINTS = [
    "example",
    "story",
    "project",
    "experience",
    "background",
    "worked on",
    "case",
    "tell me about",
    "walk me through",
    "how did you",
    "what did you build",
    "biggest challenge"
];
function tokenize(input) {
    return input
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, " ")
        .split(/\s+/)
        .filter(Boolean);
}
export function selectStory(userQuery) {
    const query = (userQuery ?? "").trim();
    if (!query) {
        return { story_id: "none", confidence: 0, reason: "empty_query" };
    }
    const lowered = query.toLowerCase();
    const asksForStory = STORY_INTENT_HINTS.some((k) => lowered.includes(k));
    const stories = loadStories();
    if (!stories.length) {
        return {
            story_id: "none",
            confidence: 0,
            reason: `story_bank_unavailable cwd=${process.cwd()}`
        };
    }
    const tokens = new Set(tokenize(query));
    let best = null;
    for (const story of stories) {
        const tags = story.tags.map((t) => t.toLowerCase());
        const titleTokens = tokenize(story.title);
        const candidates = [...tags, ...titleTokens, story.id.toLowerCase()];
        const matches = candidates.filter((c) => tokens.has(c) || lowered.includes(c));
        const score = matches.length;
        if (!best || score > best.score) {
            best = { story, score, matches };
        }
    }
    if (!best) {
        return { story_id: "none", confidence: 0, reason: "no_story_match" };
    }
    const confidence = Math.min(1, best.score / 4);
    if (!asksForStory && confidence < 0.75) {
        return {
            story_id: "none",
            confidence,
            reason: "user_did_not_request_story_and_match_not_strong"
        };
    }
    if (confidence < 0.35) {
        return { story_id: "none", confidence, reason: "low_confidence_match" };
    }
    return {
        story_id: best.story.id,
        confidence,
        reason: `matched:${best.matches.slice(0, 5).join(",") || "weak"}`,
        title: best.story.title,
        summary_30s: best.story.summary_30s,
        proof_points: best.story.proof_points
    };
}
