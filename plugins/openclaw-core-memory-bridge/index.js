import { spawn } from "node:child_process";
import { appendFileSync, readFileSync } from "node:fs";

const AGENT_END_MODULE = "core_memory.integrations.openclaw.agent_end_bridge";
const READ_BRIDGE_MODULE = "core_memory.integrations.openclaw.read_bridge";
const COMPACTION_QUEUE_MODULE = "core_memory.integrations.openclaw.compaction_queue";
const BRIDGE_MODULES = [AGENT_END_MODULE, READ_BRIDGE_MODULE, COMPACTION_QUEUE_MODULE];
const BENIGN_SKIP_REASONS = new Set(["deduped", "memory_trigger_skip", "memory_origin_skip"]);

const plugin = {
  id: "core-memory-bridge",
  name: "Core Memory Bridge",
  description: "Bridge OpenClaw lifecycle hooks to Core Memory canonical write/read/flush surfaces",

  register(api) {
    const entryCfg = api?.config?.plugins?.entries?.[api.id];
    const cfgIn = api?.pluginConfig ?? entryCfg?.config ?? {};
    const cfg = {
      pythonBin: cfgIn?.pythonBin || process.env.CORE_MEMORY_PYTHON || "python3",
      coreMemoryRoot: cfgIn?.coreMemoryRoot || process.env.CORE_MEMORY_ROOT || ".",
      coreMemoryRepo: cfgIn?.coreMemoryRepo || process.env.CORE_MEMORY_REPO || process.cwd(),
      enableAgentEnd: cfgIn?.enableAgentEnd !== false,
      enableMemorySearch: cfgIn?.enableMemorySearch !== false,
      enableCompactionFlush: cfgIn?.enableCompactionFlush === true,
    };

    const debug = (line) => {
      try {
        appendFileSync('/tmp/core-memory-bridge-hook.log', `${new Date().toISOString()} ${line}\n`);
      } catch {}
    };
    debug(`register coreMemoryRoot=${cfg.coreMemoryRoot} coreMemoryRepo=${cfg.coreMemoryRepo} pythonBin=${cfg.pythonBin} enableAgentEnd=${cfg.enableAgentEnd} enableMemorySearch=${cfg.enableMemorySearch} enableCompactionFlush=${cfg.enableCompactionFlush}`);

    const loadSkillInstructions = () => {
      try {
        const url = new URL("../../docs/integrations/openclaw/core-memory-skill-instructions.md", import.meta.url);
        return readFileSync(url, "utf8").trim();
      } catch (err) {
        api.logger?.warn?.(`core-memory-bridge: failed to load skill instructions: ${String(err)}`);
        return "";
      }
    };

    if (typeof api.registerMemoryPromptSupplement === "function") {
      const instructions = loadSkillInstructions();
      if (instructions) {
        api.registerMemoryPromptSupplement(() => [
          "## Core Memory Bridge Instructions",
          instructions,
          "",
        ]);
      }
    }

    const runPythonCheck = (moduleName) =>
      new Promise((resolve) => {
        const script = `import importlib; importlib.import_module(${JSON.stringify(moduleName)})`;
        const child = spawn(cfg.pythonBin, ["-c", script], {
          stdio: ["ignore", "ignore", "pipe"],
          cwd: cfg.coreMemoryRepo,
          env: { ...process.env, PYTHONPATH: `${cfg.coreMemoryRepo}:${process.env.PYTHONPATH || ""}` },
        });
        let stderr = "";
        child.stderr.on("data", (d) => {
          stderr += String(d || "");
        });
        child.on("error", (err) => {
          resolve({ ok: false, moduleName, error: `spawn_error:${String(err)}`, stderr });
        });
        child.on("close", (code) => {
          resolve({ ok: code === 0, moduleName, code, stderr });
        });
      });

    for (const moduleName of BRIDGE_MODULES) {
      runPythonCheck(moduleName).then((result) => {
        debug(`module_check module=${moduleName} ok=${String(result.ok)} code=${String(result.code ?? "")} stderr=${String((result.stderr || result.error || "").slice(0, 180))}`);
        if (!result.ok) {
          api.logger?.warn?.(`core-memory-bridge: module import failed for ${moduleName}: ${String(result.stderr || result.error || "").slice(0, 500)}`);
        }
      }).catch((err) => {
        debug(`module_check module=${moduleName} ok=false error=${String(err).slice(0, 180)}`);
        api.logger?.warn?.(`core-memory-bridge: module import check error for ${moduleName}: ${String(err)}`);
      });
    }

    const runBridge = (moduleName, payload, opts = {}) =>
      new Promise((resolve) => {
        const timeoutMs = Number(opts?.timeoutMs) > 0 ? Number(opts.timeoutMs) : 12000;
        let settled = false;
        let timeoutHandle = null;
        const done = (result) => {
          if (settled) return;
          settled = true;
          if (timeoutHandle) clearTimeout(timeoutHandle);
          resolve(result);
        };

        const child = spawn(cfg.pythonBin, ["-m", moduleName], {
          stdio: ["pipe", "pipe", "pipe"],
          cwd: cfg.coreMemoryRepo,
          env: { ...process.env, PYTHONPATH: `${cfg.coreMemoryRepo}:${process.env.PYTHONPATH || ""}` },
        });

        let stdout = "";
        let stderr = "";

        timeoutHandle = setTimeout(() => {
          try {
            child.kill("SIGKILL");
          } catch {}
          done({ code: -1, parsed: { ok: false, error: `bridge_timeout:${moduleName}:${timeoutMs}` }, stderr });
        }, timeoutMs);

        child.stdout.on("data", (d) => {
          stdout += String(d || "");
        });
        child.stderr.on("data", (d) => {
          stderr += String(d || "");
        });

        child.on("error", (err) => {
          done({ code: -1, parsed: { ok: false, error: `spawn_error:${String(err)}` }, stderr });
        });

        child.stdin.on("error", (err) => {
          // Prevent uncaught EPIPE from crashing gateway when child exits early.
          stderr += `\nstdin_error:${String(err)}`;
        });

        child.on("close", (code) => {
          let parsed = null;
          try {
            parsed = stdout ? JSON.parse(stdout) : null;
          } catch {
            parsed = { ok: false, error: "invalid_json", stdout };
          }
          done({ code, parsed, stderr });
        });

        try {
          child.stdin.end(JSON.stringify(payload));
        } catch (err) {
          stderr += `\nstdin_end_error:${String(err)}`;
          done({ code: -1, parsed: { ok: false, error: "stdin_write_failed" }, stderr });
        }
      });

    const runBridgeDetached = (moduleName, payload) => {
      try {
        const child = spawn(cfg.pythonBin, ["-m", moduleName], {
          stdio: ["pipe", "ignore", "ignore"],
          cwd: cfg.coreMemoryRepo,
          env: { ...process.env, PYTHONPATH: `${cfg.coreMemoryRepo}:${process.env.PYTHONPATH || ""}` },
          detached: true,
        });
        try {
          child.stdin.end(JSON.stringify(payload));
        } catch {}
        child.unref();
      } catch (err) {
        api.logger?.warn?.(`core-memory-bridge: detached run failed: ${String(err)}`);
      }
    };

    const bridgeResultSummary = (out) => {
      const parsed = out?.parsed || {};
      return `ok=${String(parsed.ok)} emitted=${String(parsed.emitted)} reason=${String(parsed.reason || "")} event_id=${String(parsed.event_id || "")} processed=${String(parsed.processed ?? "")} failed=${String(parsed.failed ?? "")} code=${String(out?.code)} stderr=${String((out?.stderr || "").slice(0, 180))}`;
    };

    if (cfg.enableAgentEnd) {
      api.on("agent_end", async (event) => {
        try {
          debug(`agent_end session=${event?.sessionKey || event?.session_id || event?.sessionId || ''} run=${event?.runId || event?.run_id || ''}`);
          const payload = {
            event,
            ctx: {
              sessionId: event?.sessionKey || event?.sessionId || event?.session_id,
              sessionKey: event?.sessionKey || event?.sessionId || event?.session_id,
              agentId: event?.agentId || event?.agent_id,
              trigger: event?.trigger,
              runId: event?.runId || event?.run_id,
            },
            root: cfg.coreMemoryRoot,
          };
          const out = await runBridge(AGENT_END_MODULE, payload);
          const parsed = out?.parsed || {};
          debug(`agent_end result ${bridgeResultSummary(out)}`);
          if (!out?.parsed?.ok) {
            api.logger?.warn?.(`core-memory-bridge: agent_end emit failed: ${JSON.stringify(out?.parsed || {})}`);
          } else if (parsed.emitted === false && parsed.reason && !BENIGN_SKIP_REASONS.has(String(parsed.reason))) {
            api.logger?.warn?.(`core-memory-bridge: agent_end skipped without bead write: ${JSON.stringify(parsed)}`);
          }

          // Non-blocking compaction queue drain: retries deferred compaction work without
          // holding lifecycle hook latency budget.
          runBridgeDetached(COMPACTION_QUEUE_MODULE, {
            action: "drain",
            root: cfg.coreMemoryRoot,
            maxItems: 1,
          });
        } catch (err) {
          api.logger?.warn?.(`core-memory-bridge: agent_end hook error: ${String(err)}`);
        }
      });
    }

    if (cfg.enableMemorySearch) {
      api.on("memory_search", async (event) => {
        try {
          const query = event?.query || event?.queryText || event?.query_text || "";
          debug(`memory_search query=${String(query).slice(0, 120)}`);
          const payload = {
            action: "execute",
            query,
            request: event?.request || null,
            root: cfg.coreMemoryRoot,
            explain: false,
            k: event?.k || 8,
          };
          const out = await runBridge(READ_BRIDGE_MODULE, payload);
          debug(`memory_search result ok=${String(out?.parsed?.ok)} code=${String(out?.code)} stderr=${String((out?.stderr || '').slice(0, 180))}`);
          if (!out?.parsed?.ok) {
            api.logger?.warn?.(`core-memory-bridge: memory_search failed: ${JSON.stringify(out?.parsed || {})}`);
            return null;
          }
          return out.parsed;
        } catch (err) {
          api.logger?.warn?.(`core-memory-bridge: memory_search hook error: ${String(err)}`);
          return null;
        }
      });
    }

    if (cfg.enableCompactionFlush) {
      const onCompaction = async (event) => {
        try {
          debug(`compaction_hook_enqueue session=${event?.sessionKey || ''} run=${event?.runId || ''}`);
          const payload = {
            action: "enqueue",
            event,
            ctx: {
              sessionId: event?.sessionKey,
              sessionKey: event?.sessionKey,
              agentId: event?.agentId,
              runId: event?.runId,
            },
            root: cfg.coreMemoryRoot,
          };
          const out = await runBridge(COMPACTION_QUEUE_MODULE, payload);
          debug(`compaction enqueue ok=${String(out?.parsed?.ok)} code=${String(out?.code)}`);
          if (!out?.parsed?.ok) {
            api.logger?.warn?.(`core-memory-bridge: compaction enqueue failed: ${JSON.stringify(out?.parsed || {})}`);
          }
        } catch (err) {
          api.logger?.warn?.(`core-memory-bridge: compaction hook enqueue error: ${String(err)}`);
        }
      };

      // Queue from after_compaction only; heavy processing happens asynchronously from agent_end drain.
      api.on("after_compaction", onCompaction);
    }
  },
};

export default plugin;
