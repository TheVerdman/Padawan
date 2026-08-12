const state = {
  csrf: null,
  targets: [],
  sessions: [],
  currentSession: null,
  conversation: null,
  temporary: false,
  temporaryHistory: [],
  selectedTrace: null,
  branchParentMessageId: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (state.csrf && (options.method || "GET") !== "GET") headers["X-Padawan-CSRF"] = state.csrf;
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(detail.detail || response.statusText);
  }
  return response;
}

async function bootstrap() {
  try {
    const response = await api("/api/bootstrap");
    const data = await response.json();
    state.csrf = data.csrf_token;
    state.targets = data.targets;
    state.sessions = data.sessions;
    $("login").classList.add("hidden");
    $("workspace").classList.remove("hidden");
    renderTargets();
    renderSessions();
    if (state.sessions.length) await selectSession(state.sessions[0].session_id);
  } catch (_) {
    sessionStorage.removeItem("padawan-lab-authenticated");
    $("login").classList.remove("hidden");
    $("workspace").classList.add("hidden");
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    const response = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ access_token: $("access-token").value }),
    });
    state.csrf = (await response.json()).csrf_token;
    sessionStorage.setItem("padawan-lab-authenticated", "true");
    $("access-token").value = "";
    await bootstrap();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});

$("logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  state.csrf = null;
  sessionStorage.removeItem("padawan-lab-authenticated");
  location.reload();
});

function renderTargets() {
  const select = $("target-select");
  select.replaceChildren();
  for (const entry of state.targets) {
    const option = document.createElement("option");
    option.value = entry.target_id;
    option.textContent = entry.display_name;
    select.append(option);
  }
  updateSendState();
  if (!state.targets.length) $("readiness").textContent = "No student targets configured";
}

function updateSendState(forceDisabled = false) {
  $("send").disabled = forceDisabled || state.targets.length === 0 || (!state.temporary && !state.currentSession);
}

function renderSessions() {
  const list = $("session-list");
  list.replaceChildren();
  for (const session of state.sessions) {
    const button = document.createElement("button");
    button.className = `session-item ${state.currentSession === session.session_id ? "active" : ""}`;
    button.textContent = session.title;
    button.addEventListener("click", () => selectSession(session.session_id));
    list.append(button);
  }
}

async function selectSession(sessionId) {
  const response = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
  state.currentSession = sessionId;
  state.conversation = await response.json();
  state.temporary = false;
  state.temporaryHistory = [];
  state.branchParentMessageId = null;
  clearTrace();
  $("temporary-warning").classList.add("hidden");
  $("consent-control").classList.remove("hidden");
  $("delete-session").classList.remove("hidden");
  $("conversation-title").textContent = state.conversation.session.title;
  $("research-consent").checked = state.conversation.session.current_consent.research_trace_consent;
  updateLaneBadge();
  updateSendState();
  renderSessions();
  renderTranscript();
}

$("new-session").addEventListener("click", async () => {
  const title = prompt("Conversation title", "New Padawan conversation");
  if (!title) return;
  const consent = confirm("Retain generated traces as immutable research evidence? Cancel keeps them personal and deletable.");
  const response = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ title, research_trace_consent: consent }),
  });
  const session = await response.json();
  state.sessions.unshift(session);
  await selectSession(session.session_id);
});

$("temporary-session").addEventListener("click", () => {
  state.currentSession = null;
  state.conversation = null;
  state.temporary = true;
  state.temporaryHistory = [];
  state.branchParentMessageId = null;
  clearTrace();
  $("conversation-title").textContent = "Temporary chat";
  $("temporary-warning").classList.remove("hidden");
  $("consent-control").classList.add("hidden");
  $("delete-session").classList.add("hidden");
  updateLaneBadge();
  updateSendState();
  renderSessions();
  renderTranscript();
});

$("research-consent").addEventListener("change", async () => {
  if (!state.currentSession) return;
  const enabled = $("research-consent").checked;
  const disclosure = "Future turns will retain the complete rendered request—including " +
    "selected earlier messages—as immutable research evidence. Existing traces are unchanged. Continue?";
  if (enabled && !confirm(disclosure)) {
    $("research-consent").checked = false;
    return;
  }
  const response = await api(`/api/sessions/${encodeURIComponent(state.currentSession)}/consent`, {
    method: "POST",
    body: JSON.stringify({ research_trace_consent: enabled }),
  });
  state.conversation.session.current_consent = await response.json();
  updateLaneBadge();
});

function updateLaneBadge() {
  const badge = $("lane-badge");
  if (state.temporary) {
    badge.textContent = "Temporary · no storage";
    badge.className = "badge temporary";
  } else if (state.conversation?.session.current_consent.research_trace_consent) {
    badge.textContent = "Research evidence · immutable";
    badge.className = "badge research";
  } else {
    badge.textContent = "Personal · deletable";
    badge.className = "badge personal";
  }
}

function renderTranscript() {
  const transcript = $("transcript");
  transcript.replaceChildren();
  const messages = state.temporary ? state.temporaryHistory : (state.conversation?.messages || []);
  for (const message of messages) transcript.append(messageNode(message));
  transcript.scrollTop = transcript.scrollHeight;
}

function messageNode(message) {
  const article = document.createElement("article");
  article.className = `message ${message.role}`;
  const role = document.createElement("div");
  role.className = "message-role";
  role.textContent = message.role === "user" ? "YOU" : "STUDENT";
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = message.content;
  article.append(role);
  if (!state.temporary && message.role === "assistant") {
    const turn = state.conversation.turns.find((item) => item.assistant_message_id === message.message_id);
    if (turn) {
      article.append(reasoningDisclosure(turn.trace_id), body);
      const actions = document.createElement("div");
      actions.className = "message-actions";
      actions.append(
        actionButton("Trace", () => showTrace(turn.trace_id)),
        actionButton("Retry", () => replayTurn(turn.turn_id)),
        actionButton("Replay one axis", () => replayVariant(turn.turn_id)),
        actionButton("Branch here", () => { state.branchParentMessageId = message.message_id; $("prompt").focus(); }),
        actionButton("Useful", () => sendFeedback(turn.turn_id, "positive")),
        actionButton("Incorrect", () => correction(turn.turn_id)),
      );
      article.append(actions);
      return article;
    }
  }
  article.append(body);
  return article;
}

function reasoningDisclosure(traceId) {
  const details = document.createElement("details");
  details.className = "reasoning-disclosure";
  const summary = document.createElement("summary");
  summary.textContent = "Reasoning";
  const content = document.createElement("div");
  content.className = "reasoning-content";
  content.textContent = "Open to load restricted reasoning.";
  details.append(summary, content);
  details.addEventListener("toggle", async () => {
    if (!details.open || details.dataset.loaded === "true" || details.dataset.loading === "true") return;
    details.dataset.loading = "true";
    content.textContent = "Loading reasoning…";
    try {
      const reasoning = await fetchPrivateReasoning(traceId);
      content.textContent = reasoning || "No private reasoning was emitted.";
      details.dataset.loaded = "true";
    } catch (error) {
      content.textContent = `Unable to load reasoning: ${error.message}`;
    } finally {
      delete details.dataset.loading;
    }
  });
  return details;
}

function actionButton(label, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

async function sendFeedback(turnId, kind, body = null) {
  await api(`/api/turns/${encodeURIComponent(turnId)}/feedback`, {
    method: "POST",
    body: JSON.stringify({ kind, body }),
  });
}

async function correction(turnId) {
  const body = prompt("What should Padawan understand or correct?");
  if (body) await sendFeedback(turnId, "correction", body);
}

function sampling() {
  return {
    temperature: Number($("temperature").value),
    top_p: 0.95,
    max_output_tokens: Number($("max-tokens").value),
    seed: null,
    top_logprobs: null,
    stop: [],
  };
}

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = $("prompt").value.trim();
  if (!content || !$("target-select").value) return;
  $("prompt").value = "";
  if (state.temporary) {
    state.temporaryHistory.push({ role: "user", content });
    renderTranscript();
    await runStream("/api/temporary/generations", {
      target_id: $("target-select").value,
      history: state.temporaryHistory,
      sampling: sampling(),
    }, true);
    return;
  }
  if (!state.currentSession) return;
  const assistants = state.conversation.messages.filter((item) => item.role === "assistant");
  const parent = state.branchParentMessageId || assistants.at(-1)?.message_id || null;
  state.branchParentMessageId = null;
  await runStream(`/api/sessions/${encodeURIComponent(state.currentSession)}/generations`, {
    target_id: $("target-select").value,
    content,
    parent_message_id: parent,
    mode: parent && parent !== assistants.at(-1)?.message_id ? "branch" : "message",
    source_turn_id: null,
    sampling: sampling(),
    declared_axis_changes: [],
  }, false);
});

async function replayTurn(turnId) {
  const sourceTurn = state.conversation.turns.find((item) => item.turn_id === turnId);
  if (!sourceTurn) return;
  const sourceResponse = await api(`/api/traces/${encodeURIComponent(sourceTurn.trace_id)}`);
  const sourceTrace = await sourceResponse.json();
  await runStream(`/api/sessions/${encodeURIComponent(state.currentSession)}/generations`, {
    target_id: sourceTrace.manifest.target.target_id,
    content: null,
    parent_message_id: null,
    mode: "retry",
    source_turn_id: turnId,
    sampling: sourceTrace.manifest.sampling,
    declared_axis_changes: [],
  }, false);
}

async function replayVariant(turnId) {
  const sourceTurn = state.conversation.turns.find((item) => item.turn_id === turnId);
  if (!sourceTurn) return;
  const sourceResponse = await api(`/api/traces/${encodeURIComponent(sourceTurn.trace_id)}`);
  const sourceTrace = await sourceResponse.json();
  const targetId = $("target-select").value;
  const desiredSampling = sampling();
  const changedAxes = [];
  if (targetId !== sourceTrace.manifest.target.target_id) changedAxes.push("student_target");
  if (JSON.stringify(desiredSampling) !== JSON.stringify(sourceTrace.manifest.sampling)) {
    changedAxes.push("sampling");
  }
  if (changedAxes.length !== 1) {
    alert("Change exactly one axis: select another student target or change sampling controls.");
    return;
  }
  await runStream(`/api/sessions/${encodeURIComponent(state.currentSession)}/generations`, {
    target_id: targetId,
    content: null,
    parent_message_id: null,
    mode: "replay",
    source_turn_id: turnId,
    sampling: desiredSampling,
    declared_axis_changes: changedAxes,
  }, false);
}

async function runStream(path, payload, temporary) {
  updateSendState(true);
  const placeholder = { role: "assistant", content: "" };
  if (temporary) state.temporaryHistory.push(placeholder);
  const liveNode = messageNode(placeholder);
  $("transcript").append(liveNode);
  const bodyNode = liveNode.querySelector(".message-body");
  let failed = false;
  try {
    const response = await api(path, { method: "POST", body: JSON.stringify(payload) });
    await consumeSSE(response, (event, data) => {
      if (event === "delta") {
        placeholder.content += data.text;
        bodyNode.textContent = placeholder.content;
      } else if (event === "error") {
        failed = true;
        bodyNode.textContent += `\n[Error: ${data.message}]`;
      }
    });
    if (!temporary) await selectSession(state.currentSession);
  } catch (error) {
    failed = true;
    bodyNode.textContent += `\n[Error: ${error.message}]`;
  } finally {
    if (temporary && failed) {
      state.temporaryHistory = state.temporaryHistory.filter((item) => item !== placeholder);
    }
    updateSendState();
  }
}

async function consumeSSE(response, handler) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop();
    for (const block of blocks) {
      let event = "message";
      const data = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }
      if (data.length) handler(event, JSON.parse(data.join("\n")));
    }
    if (done) break;
  }
}

$("check-readiness").addEventListener("click", async () => {
  const targetId = $("target-select").value;
  if (!targetId) return;
  $("readiness").textContent = "Checking…";
  const response = await api(`/api/targets/${encodeURIComponent(targetId)}/readiness`, { method: "POST" });
  const data = await response.json();
  $("readiness").textContent = data.status;
  $("readiness").className = `status-dot ${data.status}`;
  $("readiness").title = data.detail;
});

async function fetchRestrictedTrace(traceId) {
  const response = await api(`/api/traces/${encodeURIComponent(traceId)}?include_restricted=true`, {
    headers: { "X-Padawan-Research-Trace": "reveal" },
  });
  const trace = await response.json();
  return trace.restricted_research_view || {};
}

async function fetchPrivateReasoning(traceId) {
  const response = await api(`/api/traces/${encodeURIComponent(traceId)}/private-reasoning`, {
    headers: { "X-Padawan-Research-Trace": "reveal" },
  });
  return (await response.json()).private_reasoning;
}

async function showTrace(traceId) {
  const response = await api(`/api/traces/${encodeURIComponent(traceId)}`);
  const trace = await response.json();
  state.selectedTrace = traceId;
  $("trace-empty").classList.add("hidden");
  $("trace-content").classList.remove("hidden");
  resetRestrictedTrace();
  $("trace-summary").replaceChildren(
    summaryRow("Status", trace.status),
    summaryRow("Evidence", trace.evidence_class),
    summaryRow("Retention", trace.retention_classification),
    summaryRow("Benchmark", String(trace.controlled_benchmark_eligible)),
    summaryRow("Model", trace.response_metadata.model_id || "pending"),
  );
  const publicTrace = { ...trace };
  delete publicTrace.restricted_research_view;
  $("trace-json").textContent = JSON.stringify(publicTrace, null, 2);
}

function resetRestrictedTrace() {
  const disclosure = $("restricted-trace");
  disclosure.open = false;
  delete disclosure.dataset.loaded;
  delete disclosure.dataset.loading;
  $("restricted-json").textContent = "Open to load raw restricted artifacts.";
}

function clearTrace() {
  state.selectedTrace = null;
  $("trace-empty").classList.remove("hidden");
  $("trace-content").classList.add("hidden");
  $("trace-json").textContent = "";
  resetRestrictedTrace();
}

function summaryRow(label, value) {
  const row = document.createElement("div");
  const key = document.createElement("span");
  const val = document.createElement("strong");
  key.textContent = label;
  val.textContent = value;
  row.append(key, val);
  return row;
}

$("restricted-trace").addEventListener("toggle", async () => {
  const disclosure = $("restricted-trace");
  if (!disclosure.open || disclosure.dataset.loaded === "true" || disclosure.dataset.loading === "true") return;
  if (!state.selectedTrace) return;
  const traceId = state.selectedTrace;
  disclosure.dataset.loading = "true";
  $("restricted-json").textContent = "Loading raw restricted artifacts…";
  try {
    const restricted = await fetchRestrictedTrace(traceId);
    if (state.selectedTrace !== traceId) return;
    $("restricted-json").textContent = JSON.stringify(restricted, null, 2);
    disclosure.dataset.loaded = "true";
  } catch (error) {
    $("restricted-json").textContent = `Unable to load restricted artifacts: ${error.message}`;
  } finally {
    delete disclosure.dataset.loading;
  }
});

$("delete-session").addEventListener("click", async () => {
  const confirmed = confirm("Delete this personal conversation? Consented research evidence remains immutable.");
  if (!state.currentSession || !confirmed) return;
  const response = await api(`/api/sessions/${encodeURIComponent(state.currentSession)}`, { method: "DELETE" });
  const result = await response.json();
  const retained = result.retained_research_trace_ids.length +
    result.retained_research_feedback_ids.length;
  if (retained) alert(`${retained} consented research evidence record(s) were retained.`);
  state.sessions = state.sessions.filter((item) => item.session_id !== state.currentSession);
  state.currentSession = null;
  state.conversation = null;
  clearTrace();
  updateSendState();
  renderSessions();
  renderTranscript();
  $("conversation-title").textContent = "Choose or create a conversation";
});

if (sessionStorage.getItem("padawan-lab-authenticated") === "true") bootstrap();
