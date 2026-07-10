import { spawn, spawnSync } from "node:child_process";
import { appendFileSync, readFileSync } from "node:fs";
import { join } from "node:path";

const AGENT_END_MODULE = "core_memory.integrations.openclaw.agent_end_bridge";
const HOSTED_CAPTURE_MODULE = "core_memory.integrations.openclaw.hosted_capture_bridge";
const READ_BRIDGE_MODULE = "core_memory.integrations.openclaw.read_bridge";
const COMPACTION_QUEUE_MODULE = "core_memory.integrations.openclaw.compaction_queue";
const BRIDGE_MODULES = [AGENT_END_MODULE, HOSTED_CAPTURE_MODULE, READ_BRIDGE_MODULE, COMPACTION_QUEUE_MODULE];
const BENIGN_SKIP_REASONS = new Set(["deduped", "memory_trigger_skip", "memory_origin_skip"]);
const DEFAULT_MESSAGE_TURN_FALLBACK_DELAY_MS = 4000;
const MESSAGE_TURN_FALLBACK_TTL_MS = 15 * 60 * 1000;

const plugin = {
  id: "core-memory-bridge",
  name: "Core Memory Bridge",
  description: "Bridge OpenClaw lifecycle hooks to Core Memory canonical write/read/flush surfaces",

  register(api) {
    const entryCfg = api?.config?.plugins?.entries?.[api.id];
    const cfgIn = api?.pluginConfig ?? entryCfg?.config ?? {};
    const fallbackDelayRaw = Number(cfgIn?.messageTurnFallbackDelayMs ?? process.env.CORE_MEMORY_MESSAGE_TURN_FALLBACK_DELAY_MS ?? DEFAULT_MESSAGE_TURN_FALLBACK_DELAY_MS);
    const hostedCoreMemoryUrl = cfgIn?.hostedCoreMemoryUrl || process.env.CORE_MEMORY_HOSTED_TURN_FINALIZED_URL || process.env.CORE_MEMORY_HOSTED_API_BASE_URL || "";
    const hostedCoreMemoryToken = cfgIn?.hostedCoreMemoryToken || process.env.CORE_MEMORY_HOSTED_HTTP_TOKEN || "";
    const hostedTimeoutRaw = Number(cfgIn?.hostedCoreMemoryTimeoutMs ?? process.env.CORE_MEMORY_HOSTED_HTTP_TIMEOUT_MS ?? 12000);
    const localWriteEnv = String(process.env.CORE_MEMORY_BRIDGE_ENABLE_LOCAL_WRITE ?? "").trim().toLowerCase();
    const cfg = {
      pythonBin: cfgIn?.pythonBin || process.env.CORE_MEMORY_PYTHON || "python3",
      coreMemoryRoot: cfgIn?.coreMemoryRoot || process.env.CORE_MEMORY_ROOT || ".",
      coreMemoryRepo: cfgIn?.coreMemoryRepo || process.env.CORE_MEMORY_REPO || process.cwd(),
      enableAgentEnd: cfgIn?.enableAgentEnd !== false,
      enableHostedCoreMemoryClone: cfgIn?.enableHostedCoreMemoryClone === false ? false : (cfgIn?.enableHostedCoreMemoryClone === true || Boolean(hostedCoreMemoryUrl)),
      hostedCoreMemoryUrl,
      hostedCoreMemoryToken,
      hostedCoreMemoryTenantId: cfgIn?.hostedCoreMemoryTenantId || process.env.CORE_MEMORY_HOSTED_TENANT_ID || "",
      hostedCoreMemoryTimeoutMs: Math.max(1000, Number.isFinite(hostedTimeoutRaw) ? hostedTimeoutRaw : 12000),
      enableLocalCoreMemoryWrite: cfgIn?.enableLocalCoreMemoryWrite !== false && !["0", "false", "no", "off"].includes(localWriteEnv),
      enableMemorySearch: cfgIn?.enableMemorySearch !== false,
      enableCompactionFlush: cfgIn?.enableCompactionFlush === true,
      enableMessageTurnFallback: cfgIn?.enableMessageTurnFallback !== false,
      messageTurnFallbackDelayMs: Math.max(0, Number.isFinite(fallbackDelayRaw) ? fallbackDelayRaw : DEFAULT_MESSAGE_TURN_FALLBACK_DELAY_MS),
    };

    const debug = (line) => {
      try {
        appendFileSync('/tmp/core-memory-bridge-hook.log', `${new Date().toISOString()} ${line}\n`);
      } catch {}
    };
    debug(`register coreMemoryRoot=${cfg.coreMemoryRoot} coreMemoryRepo=${cfg.coreMemoryRepo} pythonBin=${cfg.pythonBin} enableAgentEnd=${cfg.enableAgentEnd} enableHostedCoreMemoryClone=${cfg.enableHostedCoreMemoryClone} hostedCoreMemoryUrl=${cfg.hostedCoreMemoryUrl ? "configured" : "missing"} hostedCoreMemoryToken=${cfg.hostedCoreMemoryToken ? "configured" : "missing"} enableLocalCoreMemoryWrite=${cfg.enableLocalCoreMemoryWrite} enableMemorySearch=${cfg.enableMemorySearch} enableCompactionFlush=${cfg.enableCompactionFlush} enableMessageTurnFallback=${cfg.enableMessageTurnFallback} messageTurnFallbackDelayMs=${cfg.messageTurnFallbackDelayMs}`);

    const loadSkillInstructions = () => {
      try {
        const path = join(cfg.coreMemoryRepo, "docs", "integrations", "openclaw", "core-memory-skill-instructions.md");
        return readFileSync(path, "utf8").trim();
      } catch (err) {
        api.logger?.warn?.(`core-memory-bridge: failed to load skill instructions: ${String(err)}`);
        return "";
      }
    };

    const loadAgentAuthoringSpec = () => {
      const script = "from core_memory.schema.agent_authoring_spec import BEAD_AUTHORING_SPEC; print(BEAD_AUTHORING_SPEC)";
      const result = spawnSync(cfg.pythonBin, ["-c", script], {
        cwd: cfg.coreMemoryRepo,
        encoding: "utf8",
        maxBuffer: 1024 * 1024,
        env: { ...process.env, PYTHONPATH: `${cfg.coreMemoryRepo}:${process.env.PYTHONPATH || ""}` },
      });
      if (result.status === 0 && String(result.stdout || "").trim()) {
        return String(result.stdout).trim();
      }
      api.logger?.warn?.(`core-memory-bridge: failed to load generated authoring spec: ${String(result.stderr || "")}`);
      return "";
    };

    if (typeof api.registerMemoryPromptSupplement === "function") {
      const instructions = loadSkillInstructions();
      const authoringSpec = loadAgentAuthoringSpec();
      if (instructions || authoringSpec) {
        api.registerMemoryPromptSupplement(() => [
          "## Core Memory Bridge Instructions",
          instructions,
          "",
          "## Agent-Authored Turn Memory Contract",
          authoringSpec,
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
      return `ok=${String(parsed.ok)} emitted=${String(parsed.emitted)} reason=${String(parsed.reason || "")} event_id=${String(parsed.event_id || "")} processed=${String(parsed.processed ?? "")} failed=${String(parsed.failed ?? "")} http_status=${String(parsed.http_status ?? "")} code=${String(out?.code)} stderr=${String((out?.stderr || "").slice(0, 180))}`;
    };

    const hostedBridgePayload = (payload) => ({
      ...payload,
      hosted: {
        url: cfg.hostedCoreMemoryUrl,
        token: cfg.hostedCoreMemoryToken,
        tenantId: cfg.hostedCoreMemoryTenantId,
        timeout: Math.max(1, Math.ceil(cfg.hostedCoreMemoryTimeoutMs / 1000)),
      },
    });

    const maybeEmitHostedClone = async (hookName, payload) => {
      if (!cfg.enableHostedCoreMemoryClone) {
        debug(`${hookName} hosted_clone skipped reason=disabled`);
        return null;
      }

      const out = await runBridge(HOSTED_CAPTURE_MODULE, hostedBridgePayload(payload), {
        timeoutMs: cfg.hostedCoreMemoryTimeoutMs + 1000,
      });
      const parsed = out?.parsed || {};
      debug(`${hookName} hosted_clone ${bridgeResultSummary(out)}`);
      if (!parsed.ok) {
        api.logger?.warn?.(`core-memory-bridge: ${hookName} hosted clone failed: ${JSON.stringify(parsed)}`);
      } else if (parsed.emitted === false && parsed.reason && !BENIGN_SKIP_REASONS.has(String(parsed.reason))) {
        api.logger?.warn?.(`core-memory-bridge: ${hookName} hosted clone skipped: ${JSON.stringify(parsed)}`);
      }
      return out;
    };

    const extractText = (value) => {
      if (typeof value === "string") return value.trim();
      if (Array.isArray(value)) {
        return value.map((item) => extractText(item)).filter(Boolean).join("\n").trim();
      }
      if (value && typeof value === "object") {
        for (const key of ["text", "content", "message", "body", "output", "response", "reply", "final"]) {
          const text = extractText(value[key]);
          if (text) return text;
        }
      }
      return "";
    };

    const firstText = (...values) => {
      for (const value of values) {
        const text = extractText(value);
        if (text) return text;
      }
      return "";
    };

    const firstString = (...values) => {
      for (const value of values) {
        if (value === null || value === undefined) continue;
        const text = String(value).trim();
        if (text) return text;
      }
      return "";
    };

    const sessionFrom = (event = {}, ctx = {}) => {
      const sessionObj = event?.session && typeof event.session === "object" ? event.session : {};
      const contextObj = event?.context && typeof event.context === "object" ? event.context : {};
      const messageObj = event?.message && typeof event.message === "object" ? event.message : {};
      const chatObj = event?.chat && typeof event.chat === "object" ? event.chat : {};
      const sourceObj = event?.source && typeof event.source === "object" ? event.source : {};
      const sessionKey = firstString(
        ctx?.sessionKey,
        ctx?.sessionId,
        event?.sessionKey,
        event?.sessionId,
        event?.session_id,
        sessionObj.key,
        sessionObj.id,
        contextObj.sessionKey,
        contextObj.sessionId,
        messageObj.sessionKey,
        messageObj.sessionId,
        event?.threadId,
        event?.thread_id,
        event?.conversationId,
        event?.conversation_id,
        event?.chatId,
        event?.chat_id,
        chatObj.id,
        sourceObj.threadId,
        sourceObj.chatId,
        "main",
      );
      return {
        sessionId: sessionKey,
        sessionKey,
        agentId: firstString(ctx?.agentId, event?.agentId, event?.agent_id, contextObj.agentId, "main"),
        runId: firstString(ctx?.runId, event?.runId, event?.run_id, contextObj.runId),
      };
    };

    const messageIdFrom = (event = {}) => {
      const messageObj = event?.message && typeof event.message === "object" ? event.message : {};
      const payloadObj = event?.payload && typeof event.payload === "object" ? event.payload : {};
      return firstString(
        event?.messageId,
        event?.message_id,
        event?.sourceMessageId,
        event?.source_message_id,
        event?.telegramMessageId,
        event?.id,
        messageObj.messageId,
        messageObj.message_id,
        messageObj.id,
        payloadObj.messageId,
        payloadObj.message_id,
        payloadObj.id,
      );
    };

    const threadIdFrom = (event = {}, ctx = {}) => {
      const messageObj = event?.message && typeof event.message === "object" ? event.message : {};
      const chatObj = event?.chat && typeof event.chat === "object" ? event.chat : {};
      const sourceObj = event?.source && typeof event.source === "object" ? event.source : {};
      const contextObj = event?.context && typeof event.context === "object" ? event.context : {};
      return firstString(
        ctx?.threadId,
        ctx?.chatId,
        event?.threadId,
        event?.thread_id,
        event?.sourceThreadId,
        event?.source_thread_id,
        event?.conversationId,
        event?.conversation_id,
        event?.chatId,
        event?.chat_id,
        messageObj.threadId,
        messageObj.chatId,
        chatObj.id,
        sourceObj.threadId,
        sourceObj.chatId,
        contextObj.threadId,
        contextObj.chatId,
      );
    };

    const channelFrom = (event = {}) => {
      const messageObj = event?.message && typeof event.message === "object" ? event.message : {};
      const sourceObj = event?.source && typeof event.source === "object" ? event.source : {};
      const sourceText = typeof event?.source === "string" ? event.source : "";
      return firstString(event?.channel, event?.transport, event?.provider, sourceText, messageObj.channel, sourceObj.channel, sourceObj.provider);
    };

    const fallbackKeysFrom = (event = {}, ctx = {}) => {
      const session = sessionFrom(event, ctx);
      const channel = channelFrom(event);
      const threadId = threadIdFrom(event, ctx);
      const keys = [
        session.sessionKey,
        session.sessionId,
        threadId,
        channel && threadId ? `${channel}:${threadId}` : "",
      ].filter(Boolean);
      return [...new Set(keys)];
    };

    const userTextFrom = (event = {}) =>
      firstText(
        event?.text,
        event?.content,
        event?.body,
        event?.input,
        event?.query,
        event?.message,
        event?.payload,
        event?.data,
        event?.normalized,
      );

    const assistantTextFrom = (event = {}) =>
      firstText(
        event?.text,
        event?.content,
        event?.body,
        event?.output,
        event?.response,
        event?.reply,
        event?.final,
        event?.message,
        event?.payload,
        event?.data,
        event?.normalized,
        event?.dispatch,
        event?.result,
        event?.params,
      );

    const pendingInboundByKey = new Map();
    const agentEndSeenByKey = new Map();
    const fallbackEmittedKeys = new Set();

    const pruneMessageFallbackState = () => {
      const cutoff = Date.now() - MESSAGE_TURN_FALLBACK_TTL_MS;
      for (const [key, pending] of pendingInboundByKey.entries()) {
        if ((pending?.ts || 0) < cutoff) pendingInboundByKey.delete(key);
      }
      for (const [key, ts] of agentEndSeenByKey.entries()) {
        if ((ts || 0) < cutoff) agentEndSeenByKey.delete(key);
      }
      if (fallbackEmittedKeys.size > 500) fallbackEmittedKeys.clear();
    };

    const markAgentEndSeen = (event = {}, ctx = {}) => {
      const session = sessionFrom(event, ctx);
      for (const key of fallbackKeysFrom(event, ctx)) {
        agentEndSeenByKey.set(key, Date.now());
        if (session.runId) agentEndSeenByKey.set(`${key}:${session.runId}`, Date.now());
      }
    };

    const noteInboundMessage = (event = {}, ctx = {}) => {
      pruneMessageFallbackState();
      const userText = userTextFrom(event);
      if (!userText) {
        debug("message_received skipped reason=missing_user_text");
        return;
      }
      const session = sessionFrom(event, ctx);
      const channel = channelFrom(event);
      const messageId = messageIdFrom(event);
      const keys = fallbackKeysFrom(event, ctx);
      const pending = {
        ...session,
        userText,
        channel,
        inboundMessageId: messageId,
        keys,
        ts: Date.now(),
      };
      for (const key of keys) pendingInboundByKey.set(key, pending);
      debug(`message_received captured session=${session.sessionKey} run=${session.runId || ""} channel=${channel || ""} keys=${keys.join(",")} message_id=${messageId || ""} chars=${userText.length}`);
    };

    const emitFallbackTurn = async (pending, event = {}, ctx = {}, hookName = "message_sent") => {
      const assistantText = assistantTextFrom(event);
      if (!assistantText) {
        debug(`${hookName} fallback skipped session=${pending?.sessionKey || ""} reason=missing_assistant_text`);
        return;
      }
      const session = sessionFrom(event, ctx);
      const sessionKey = session.sessionKey || pending.sessionKey;
      const runId = firstString(session.runId, pending.runId);
      const outboundMessageId = messageIdFrom(event);
      const fallbackKey = `${sessionKey}:${runId || pending.inboundMessageId || ""}:${outboundMessageId || assistantText.slice(0, 80)}`;
      if (fallbackEmittedKeys.has(fallbackKey)) {
        debug(`${hookName} fallback skipped session=${sessionKey} reason=deduped_fallback key=${fallbackKey}`);
        return;
      }
      const correlationKeys = [...new Set([sessionKey, ...(pending.keys || []), ...fallbackKeysFrom(event, ctx)].filter(Boolean))];
      if (correlationKeys.some((key) => agentEndSeenByKey.has(key) || (runId && agentEndSeenByKey.has(`${key}:${runId}`)))) {
        debug(`${hookName} fallback skipped session=${sessionKey} run=${runId || ""} reason=agent_end_seen`);
        return;
      }
      fallbackEmittedKeys.add(fallbackKey);
      for (const key of pending.keys || [pending.sessionKey]) pendingInboundByKey.delete(key);
      const payload = {
        event: {
          messages: [
            { role: "user", content: pending.userText },
            { role: "assistant", content: assistantText },
          ],
          success: event?.success !== false && event?.ok !== false,
          runId: runId || `message-fallback:${pending.inboundMessageId || "inbound"}:${outboundMessageId || "outbound"}`,
          source: "openclaw_message_turn_fallback",
          trigger: "message_turn_fallback",
          metadata: {
            bridge_hook: hookName,
            channel: firstString(channelFrom(event), pending.channel),
            inboundMessageId: pending.inboundMessageId,
            outboundMessageId,
          },
        },
        ctx: {
          sessionId: sessionKey,
          sessionKey,
          agentId: firstString(session.agentId, pending.agentId),
          trigger: "message_turn_fallback",
          runId: runId || `message-fallback:${pending.inboundMessageId || "inbound"}:${outboundMessageId || "outbound"}`,
        },
        root: cfg.coreMemoryRoot,
      };
      debug(`${hookName} fallback_emit session=${sessionKey} run=${payload.ctx.runId} inbound=${pending.inboundMessageId || ""} outbound=${outboundMessageId || ""} user_chars=${pending.userText.length} assistant_chars=${assistantText.length}`);
      await maybeEmitHostedClone(`${hookName} fallback`, payload);
      if (cfg.enableLocalCoreMemoryWrite) {
        const out = await runBridge(AGENT_END_MODULE, payload);
        const parsed = out?.parsed || {};
        debug(`${hookName} fallback_result ${bridgeResultSummary(out)}`);
        if (!parsed.ok) {
          api.logger?.warn?.(`core-memory-bridge: ${hookName} fallback emit failed: ${JSON.stringify(parsed)}`);
        } else if (parsed.emitted === false && parsed.reason && !BENIGN_SKIP_REASONS.has(String(parsed.reason))) {
          api.logger?.warn?.(`core-memory-bridge: ${hookName} fallback skipped without bead write: ${JSON.stringify(parsed)}`);
        }
        runBridgeDetached(COMPACTION_QUEUE_MODULE, {
          action: "drain",
          root: cfg.coreMemoryRoot,
          maxItems: 1,
        });
      } else {
        debug(`${hookName} fallback_result skipped reason=local_write_disabled`);
      }
    };

    const scheduleOutboundFallback = (event = {}, ctx = {}, hookName = "message_sent") => {
      const session = sessionFrom(event, ctx);
      const keys = fallbackKeysFrom(event, ctx);
      const pending = keys.map((key) => pendingInboundByKey.get(key)).find(Boolean);
      const assistantText = assistantTextFrom(event);
      debug(`${hookName} observed session=${session.sessionKey} run=${session.runId || ""} keys=${keys.join(",")} hasPending=${String(Boolean(pending))} assistant_chars=${assistantText.length}`);
      if (!pending) return;
      if (!assistantText) return;
      if (pending.fallbackScheduled) {
        debug(`${hookName} fallback already scheduled session=${session.sessionKey}`);
        return;
      }
      pending.fallbackScheduled = true;
      setTimeout(() => {
        emitFallbackTurn(pending, event, ctx, hookName).catch((err) => {
          api.logger?.warn?.(`core-memory-bridge: ${hookName} fallback hook error: ${String(err)}`);
        });
      }, cfg.messageTurnFallbackDelayMs);
    };

    if (cfg.enableAgentEnd) {
      api.on("agent_end", async (event, ctx = {}) => {
        try {
          markAgentEndSeen(event, ctx);
          debug(`agent_end session=${event?.sessionKey || event?.session_id || event?.sessionId || ''} run=${event?.runId || event?.run_id || ''}`);
          const payload = {
            event,
            ctx: {
              sessionId: event?.sessionKey || event?.sessionId || event?.session_id || ctx?.sessionKey || ctx?.sessionId,
              sessionKey: event?.sessionKey || event?.sessionId || event?.session_id || ctx?.sessionKey || ctx?.sessionId,
              agentId: event?.agentId || event?.agent_id || ctx?.agentId,
              trigger: event?.trigger,
              runId: event?.runId || event?.run_id || ctx?.runId,
            },
            root: cfg.coreMemoryRoot,
          };
          await maybeEmitHostedClone("agent_end", payload);
          if (cfg.enableLocalCoreMemoryWrite) {
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
          } else {
            debug("agent_end result skipped reason=local_write_disabled");
          }
        } catch (err) {
          api.logger?.warn?.(`core-memory-bridge: agent_end hook error: ${String(err)}`);
        }
      });
    }

    if (cfg.enableMessageTurnFallback) {
      api.on("message_received", async (event, ctx = {}) => {
        try {
          noteInboundMessage(event, ctx);
        } catch (err) {
          api.logger?.warn?.(`core-memory-bridge: message_received fallback hook error: ${String(err)}`);
        }
      });

      api.on("message_sent", async (event, ctx = {}) => {
        try {
          scheduleOutboundFallback(event, ctx, "message_sent");
        } catch (err) {
          api.logger?.warn?.(`core-memory-bridge: message_sent fallback hook error: ${String(err)}`);
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
