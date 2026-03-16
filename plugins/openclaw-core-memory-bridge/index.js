import { spawn } from "node:child_process";
import { appendFileSync } from "node:fs";

const plugin = {
  id: "core-memory-bridge",
  name: "Core Memory Bridge",
  description: "Bridge OpenClaw lifecycle hooks to Core Memory canonical write/flush surfaces",

  register(api) {
    const cfg = {
      pythonBin: api?.config?.pythonBin || process.env.CORE_MEMORY_PYTHON || "python3",
      coreMemoryRoot: api?.config?.coreMemoryRoot || process.env.CORE_MEMORY_ROOT || ".",
      coreMemoryRepo: api?.config?.coreMemoryRepo || process.env.CORE_MEMORY_REPO || "/home/node/.openclaw/workspace/Core-Memory",
      enableAgentEnd: api?.config?.enableAgentEnd !== false,
      enableCompactionFlush: api?.config?.enableCompactionFlush !== false,
    };

    const debug = (line) => {
      try {
        appendFileSync('/tmp/core-memory-bridge-hook.log', `${new Date().toISOString()} ${line}\n`);
      } catch {}
    };
    debug(`register coreMemoryRoot=${cfg.coreMemoryRoot} enableAgentEnd=${cfg.enableAgentEnd} enableCompactionFlush=${cfg.enableCompactionFlush}`);

    const runBridge = (moduleName, payload) =>
      new Promise((resolve) => {
        let settled = false;
        const done = (result) => {
          if (settled) return;
          settled = true;
          resolve(result);
        };

        const child = spawn(cfg.pythonBin, ["-m", moduleName], {
          stdio: ["pipe", "pipe", "pipe"],
          cwd: cfg.coreMemoryRepo,
          env: { ...process.env, PYTHONPATH: `${cfg.coreMemoryRepo}:${process.env.PYTHONPATH || ""}` },
        });

        let stdout = "";
        let stderr = "";

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
          const out = await runBridge("core_memory.integrations.openclaw_agent_end_bridge", payload);
          debug(`agent_end result ok=${String(out?.parsed?.ok)} code=${String(out?.code)} stderr=${String((out?.stderr||'').slice(0,180))}`);
          if (!out?.parsed?.ok) {
            api.logger?.warn?.(`core-memory-bridge: agent_end emit failed: ${JSON.stringify(out?.parsed || {})}`);
          }
        } catch (err) {
          api.logger?.warn?.(`core-memory-bridge: agent_end hook error: ${String(err)}`);
        }
      });
    }

    if (cfg.enableCompactionFlush) {
      const onCompaction = async (event) => {
        try {
          debug(`compaction_hook session=${event?.sessionKey || ''} run=${event?.runId || ''}`);
          const payload = {
            event,
            ctx: {
              sessionId: event?.sessionKey,
              sessionKey: event?.sessionKey,
              agentId: event?.agentId,
              runId: event?.runId,
            },
            root: cfg.coreMemoryRoot,
          };
          const out = await runBridge("core_memory.integrations.openclaw_compaction_bridge", payload);
          debug(`compaction result ok=${String(out?.parsed?.ok)} code=${String(out?.code)}`);
          if (!out?.parsed?.ok) {
            api.logger?.warn?.(`core-memory-bridge: compaction flush failed: ${JSON.stringify(out?.parsed || {})}`);
          }
        } catch (err) {
          api.logger?.warn?.(`core-memory-bridge: compaction hook error: ${String(err)}`);
        }
      };

      api.on("before_compaction", onCompaction);
      api.on("after_compaction", onCompaction);
    }
  },
};

export default plugin;
