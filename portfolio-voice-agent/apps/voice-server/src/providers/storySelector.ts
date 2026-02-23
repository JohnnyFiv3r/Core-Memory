import fs from "node:fs";
import path from "node:path";

type Story = {
  id: string;
  title: string;
  tags: string[];
  summary_30s: string;
  proof_points: string[];
};

type StoryToolResult = {
  story_id: string | "none";
  confidence: number;
  reason: string;
  title?: string;
  summary_30s?: string;
  proof_points?: string[];
};

const STORY_PATH = path.resolve(process.cwd(), "apps/voice-server/context/story-bank.json");

let storyCache: Story[] | null = null;

function loadStories(): Story[] {
  if (storyCache) return storyCache;
  try {
    const raw = fs.readFileSync(STORY_PATH, "utf8");
    storyCache = JSON.parse(raw) as Story[];
  } catch {
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

function tokenize(input: string): string[] {
  return input
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

export function selectStory(userQuery: string): StoryToolResult {
  const query = (userQuery ?? "").trim();
  if (!query) {
    return { story_id: "none", confidence: 0, reason: "empty_query" };
  }

  const lowered = query.toLowerCase();
  const asksForStory = STORY_INTENT_HINTS.some((k) => lowered.includes(k));

  const stories = loadStories();
  if (!stories.length) {
    return { story_id: "none", confidence: 0, reason: "story_bank_unavailable" };
  }

  const tokens = new Set(tokenize(query));
  let best: { story: Story; score: number; matches: string[] } | null = null;

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
