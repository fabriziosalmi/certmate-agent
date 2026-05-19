export function parseSseBlock(block, callbacks) {
    let event = "message";
    let dataRaw = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7).trim();
      else if (line.startsWith("data: ")) dataRaw += line.slice(6);
  }
    let data = {};
    try {
      data = JSON.parse(dataRaw);
    } catch {
      return;
    }

    if (event === "session") {
      // Server-issued HMAC token bound to our session_id. Cache it so
      // subsequent /conversations/{id} reads + deletes carry proof of
      // ownership (X-Session-Token header).
      if (data.token && data.session_id === callbacks.getSessionId()) {
        callbacks.setSessionToken(data.token);
        try {
          localStorage.setItem(callbacks.getSessionKey() + ":token", data.token);
        } catch {}
      }
    } else if (event === "token") {
      callbacks.clearStatus();
      callbacks.appendStreamToken(data.text || "");
    } else if (event === "status") {
      callbacks.setStatus(data.message || "");
    } else if (event === "tool_call") {
      callbacks.clearStatus();
      // Tool calls interrupt streaming: finalize whatever we had, then
      // render the tool entry. The next iteration may stream a fresh bubble.
      if (callbacks.getStreamingEl() && callbacks.getStreamingText()) callbacks.finalizeStream();
      else if (callbacks.getStreamingEl()) {
        callbacks.abortStream();
      }
      callbacks.addTool(data.name, data.args, undefined, true);
    } else if (event === "tool_result") {
      callbacks.clearStatus();
      callbacks.addTool(data.name, {}, data.preview, data.ok !== false);
    } else if (event === "pending_confirm") {
      callbacks.clearStatus();
      callbacks.addConfirm(data);
    } else if (event === "message") {
      callbacks.clearStatus();
      // Server sends a final message event even when streaming, so prefer
      // the streamed text we already have (avoids double-rendering).
      const text = callbacks.finalizeStream(data.content);
      callbacks.onFinalMessage(text);
    } else if (event === "error") {
      callbacks.clearStatus();
      if (callbacks.getStreamingEl()) {
        callbacks.abortStream();
      }
      callbacks.addError(data.message);
    } else if (event === "done") {
      callbacks.clearStatus();
    }
  }
