import { spawn } from "node:child_process";
import { appendFileSync } from "node:fs";

const plugin = {
  id: "core-memory-bridge",
  name: "Core Memory Bridge",
  description: "Bridge OpenClaw lifecycle hooks to Core Memory canonical write/flush surfaces",

  register(api) {
    const entryCfg = api?.config?.plugins?.entries?.[api.id];
    const cfgIn = api?.pluginConfig ?? entryCfg?.config ?? {};
    const cfg = {
      pythonBin: cfgIn?.pythonBin || process.env.CORE_MEMORY_PYTHON || "python3",
      coreMemoryRoot: cfgIn?.coreMemoryRoot || process.env.CORE_MEMORY_ROOT || ".",
      coreMemoryRepo: cfgIn?.coreMemoryRepo || process.env.CORE_MEMORY_REPO || "/home/node/.openclaw/workspace/Core-Memory",
      enableAgentEnd: cfgIn?.enableAgentEnd !== false,
      enableCompactionFlush: cfgIn?.enableCompactionFlush === true,
    };

    const debug = (line) => {
      try {
        appendFileSync('/tmp/core-memory-bridge-hook.log', `${new Date().toISOString()} ${line}\n`);
      } catch {}
    };
    debug(`register coreMemoryRoot=${cfg.coreMemoryRoot} enableAgentEnd=${cfg.enableAgentEnd} enableCompactionFlush=${cfg.enableCompactionFlush}`);

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

          // Non-blocking compaction queue drain: retries deferred compaction work without
          // holding lifecycle hook latency budget.
          runBridgeDetached("core_memory.integrations.openclaw_compaction_queue", {
            action: "drain",
            root: cfg.coreMemoryRoot,
            maxItems: 1,
          });
        } catch (err) {
          api.logger?.warn?.(`core-memory-bridge: agent_end hook error: ${String(err)}`);
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
          const out = await runBridge("core_memory.integrations.openclaw_compaction_queue", payload);
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
