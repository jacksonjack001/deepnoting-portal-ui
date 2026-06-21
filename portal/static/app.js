const state = {
  dashboard: null,
  catalog: null,
  apiKey: "",
  apiKeyMasked: "",
  period: "daily",
  metric: "tokens",
  page: "dashboardPage",
  usageTimer: null,
  usageLoading: false,
  usageRefreshMs: 30000,
  resourceStatus: null,
  resourceLoading: false,
  resourceTimer: null,
  resourceRefreshMs: 5 * 60 * 1000,
  lastTouchAt: 0,
  refundQuote: null,
  chatMessages: [],
  chatThreads: [],
  activeChatId: "",
  chatLoading: false,
  chatAttachments: [],
  docViews: {},
  docViewsLoading: false,
  news: [],
  newsLoading: false,
  agentTasks: [],
  agentLoading: false,
  agentTimer: null,
  agentRefreshMs: 5000,
};

const CHAT_HISTORY_LIMIT = 30;
const CHAT_IMAGE_MAX_BYTES = 5 * 1024 * 1024;
const CHAT_IMAGE_MAX_COUNT = 4;
const CHAT_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"]);

const $ = (id) => document.getElementById(id);

function toast(message, type = "info") {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast show ${type}`;
  setTimeout(() => (el.className = "toast"), 3200);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function maskEmail(value) {
  const email = String(value || "").trim();
  if (!email) return "-";
  const at = email.indexOf("@");
  if (at < 0) return email.length > 2 ? `${email.slice(0, 2)}***` : "***";
  const local = email.slice(0, at);
  const domain = email.slice(at + 1);
  let maskedLocal = "***";
  if (local.length === 1) maskedLocal = `${local[0]}***`;
  else if (local.length === 2) maskedLocal = `${local[0]}***${local[1]}`;
  else if (local.length > 2) maskedLocal = `${local.slice(0, 2)}***${local.at(-1)}`;
  return `${maskedLocal}@${domain}`;
}

function displayEmail(user) {
  return user?.email_masked || maskEmail(user?.email);
}

function renderInlineMarkdown(value) {
  let html = escapeHtml(value);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  return html;
}

function renderMarkdown(value) {
  const lines = String(value ?? "").replace(/\r\n/g, "\n").split("\n");
  const output = [];
  let paragraph = [];
  let list = null;

  function flushParagraph() {
    if (!paragraph.length) return;
    output.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  }

  function flushList() {
    if (!list) return;
    output.push(`<${list.type}>${list.items.map((item) => `<li>${item}</li>`).join("")}</${list.type}>`);
    list = null;
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }

    const fence = trimmed.match(/^```([A-Za-z0-9_-]+)?$/);
    if (fence) {
      flushParagraph();
      flushList();
      const code = [];
      const language = fence[1] ? ` class="language-${escapeHtml(fence[1].toLowerCase())}"` : "";
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      output.push(`<pre><code${language}>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length + 2;
      output.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      const type = unordered ? "ul" : "ol";
      if (!list || list.type !== type) {
        flushList();
        list = { type, items: [] };
      }
      list.items.push(renderInlineMarkdown((unordered || ordered)[1]));
      continue;
    }

    const quote = line.match(/^\s*>\s+(.+)$/);
    if (quote) {
      flushParagraph();
      flushList();
      output.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  flushParagraph();
  flushList();
  return output.join("") || "<p></p>";
}

function formatUsd(value) {
  const n = Number(value || 0);
  return `$${n.toFixed(n >= 1 ? 2 : 4)}`;
}

function formatCny(value) {
  return `￥${Number(value || 0).toFixed(2)}`;
}

function formatCompactUsd(value) {
  const n = Number(value || 0);
  return `$${n.toFixed(n >= 1 ? 2 : 4)}`;
}

function formatPerMillionToken(value) {
  const n = Number(value || 0) * 1_000_000;
  return `$${n.toFixed(n >= 1 ? 2 : 4)}`;
}

function formatInt(value) {
  if (value === null || value === undefined || value === "") return "-";
  return Number(value).toLocaleString("zh-CN");
}

function formatQuota(used, limit) {
  if (!limit) return used ? formatInt(used) : "-";
  return `${formatInt(used)} / ${formatInt(limit)}`;
}

function formatDiscountRate(value) {
  const n = Number(value || 0);
  return `${Math.round(n * 100)}%`;
}

function formatLocalTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `${n % 1 === 0 ? n.toFixed(0) : n.toFixed(1)}%`;
}

function percentNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0;
}

function agentStatusLabel(status) {
  const labels = {
    running: "运行中",
    tool_running: "执行工具",
    tool_done: "工具完成",
    compacting: "压缩上下文",
    subagent_running: "子任务中",
    completed: "已完成",
    failed: "失败",
    stale: "疑似中断",
  };
  return labels[status] || status || "-";
}

function statusLabel(user, usage) {
  if (usage?.limits?.blocked) return "待支付";
  return user.key_status === "active" ? "已激活" : "待支付";
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const contentType = res.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const message = typeof data === "object" ? data.detail || "请求失败" : data || "请求失败";
    const error = new Error(message);
    error.status = res.status;
    throw error;
  }
  return data;
}

function parseSseEvent(rawEvent) {
  let event = "message";
  const dataLines = [];
  rawEvent.split(/\r?\n/).forEach((line) => {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  });
  const dataText = dataLines.join("\n");
  let data = {};
  if (dataText) {
    try {
      data = JSON.parse(dataText);
    } catch (_) {
      data = { content: dataText };
    }
  }
  return { event, data };
}

async function streamApi(path, payload, onEvent) {
  const res = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const contentType = res.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await res.json() : await res.text();
    const message = typeof data === "object" ? data.detail || "请求失败" : data || "请求失败";
    const error = new Error(message);
    error.status = res.status;
    throw error;
  }
  if (!res.body) throw new Error("浏览器不支持流式响应");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const rawEvent = buffer.slice(0, boundary).trim();
      buffer = buffer.slice(boundary + 2);
      if (rawEvent) {
        const parsed = parseSseEvent(rawEvent);
        if (parsed.event === "error") throw new Error(parsed.data.detail || "模型调用失败");
        onEvent(parsed.event, parsed.data);
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
  if (buffer.trim()) {
    const parsed = parseSseEvent(buffer.trim());
    if (parsed.event === "error") throw new Error(parsed.data.detail || "模型调用失败");
    onEvent(parsed.event, parsed.data);
  }
}

function chatStorageKey() {
  const email = state.dashboard?.user?.email || "anonymous";
  return `ai-proxy.chat.${email}`;
}

function cloneChatContent(content) {
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (!part || typeof part !== "object") return null;
        if (part.type === "text") return { type: "text", text: String(part.text || "") };
        if (part.type === "image_url") {
          const imageUrl = typeof part.image_url === "string" ? { url: part.image_url } : part.image_url || {};
          return {
            type: "image_url",
            image_url: {
              url: String(imageUrl.url || ""),
              detail: imageUrl.detail || "auto",
            },
          };
        }
        return null;
      })
      .filter(Boolean);
  }
  return String(content || "");
}

function chatContentText(content) {
  if (Array.isArray(content)) {
    return content
      .filter((part) => part?.type === "text")
      .map((part) => String(part.text || ""))
      .join(" ")
      .trim();
  }
  return String(content || "").trim();
}

function chatContentHasImage(content) {
  return Array.isArray(content) && content.some((part) => part?.type === "image_url");
}

function cloneMessages(messages = []) {
  return messages.map((message) => ({
    role: message.role,
    content: cloneChatContent(message.content),
    usage: message.usage || null,
  }));
}

function normalizeChatThread(thread) {
  if (!thread || !thread.id || !Array.isArray(thread.messages)) return null;
  return {
    id: String(thread.id),
    title: thread.title || chatTitleFromMessages(thread.messages),
    model: thread.model || "",
    messages: cloneMessages(thread.messages),
    createdAt: thread.createdAt || thread.created_at || new Date().toISOString(),
    updatedAt: thread.updatedAt || thread.updated_at || new Date().toISOString(),
    lastUsage: thread.lastUsage || thread.last_usage || null,
  };
}

function chatTitleFromMessages(messages = []) {
  const firstUser = messages.find((message) => message.role === "user" && (chatContentText(message.content) || chatContentHasImage(message.content)));
  if (!firstUser) return "新对话";
  const compact = chatContentText(firstUser.content).replace(/\s+/g, " ").trim() || "图片对话";
  return compact.length > 22 ? `${compact.slice(0, 22)}...` : compact;
}

function loadChatHistory() {
  try {
    const raw = window.localStorage.getItem(chatStorageKey());
    const parsed = raw ? JSON.parse(raw) : [];
    state.chatThreads = Array.isArray(parsed)
      ? parsed
          .map((thread) => normalizeChatThread(thread))
          .filter(Boolean)
          .slice(0, CHAT_HISTORY_LIMIT)
      : [];
  } catch (_) {
    state.chatThreads = [];
  }
  const first = state.chatThreads[0];
  state.activeChatId = first?.id || "";
  state.chatMessages = first ? cloneMessages(first.messages) : [];
}

function persistChatHistory() {
  const rows = state.chatThreads
    .filter((thread) => Array.isArray(thread.messages) && thread.messages.length)
    .slice(0, CHAT_HISTORY_LIMIT);
  state.chatThreads = rows;
  try {
    window.localStorage.setItem(chatStorageKey(), JSON.stringify(rows));
  } catch (_) {
    window.localStorage.removeItem(chatStorageKey());
  }
}

function sortChatThreads() {
  state.chatThreads.sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
}

function mergeChatThreads(remoteThreads = []) {
  const map = new Map();
  [...state.chatThreads, ...remoteThreads].forEach((thread) => {
    const normalized = normalizeChatThread(thread);
    if (!normalized) return;
    const existing = map.get(normalized.id);
    if (!existing || String(normalized.updatedAt || "") > String(existing.updatedAt || "")) {
      map.set(normalized.id, normalized);
    }
  });
  state.chatThreads = Array.from(map.values());
  sortChatThreads();
  state.chatThreads = state.chatThreads.slice(0, CHAT_HISTORY_LIMIT);
  if (!state.activeChatId && state.chatThreads[0]) {
    state.activeChatId = state.chatThreads[0].id;
    state.chatMessages = cloneMessages(state.chatThreads[0].messages);
  }
  persistChatHistory();
}

async function loadServerChatHistory() {
  if (!state.dashboard) return;
  try {
    const data = await api("api/chat/threads");
    mergeChatThreads(data.threads || []);
    renderChatHistory();
    renderChatThread();
    renderChatModels();
  } catch (_) {}
}

async function saveChatThreadRemote(thread) {
  if (!state.dashboard || !thread?.messages?.length) return;
  try {
    await api(`api/chat/threads/${encodeURIComponent(thread.id)}`, {
      method: "PUT",
      body: JSON.stringify({
        title: thread.title,
        model: thread.model,
        messages: thread.messages,
        last_usage: thread.lastUsage || null,
      }),
    });
  } catch (_) {}
}

function currentChatThread() {
  return state.chatThreads.find((thread) => thread.id === state.activeChatId) || null;
}

function ensureChatThread(model) {
  let thread = currentChatThread();
  if (thread) return thread;
  thread = {
    id: `chat-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    title: "新对话",
    model,
    messages: [],
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    lastUsage: null,
  };
  state.chatThreads.unshift(thread);
  state.activeChatId = thread.id;
  return thread;
}

function saveActiveChatThread({ usage = null } = {}) {
  const thread = ensureChatThread($("chatModel")?.value || "");
  thread.model = $("chatModel")?.value || thread.model;
  thread.messages = cloneMessages(state.chatMessages);
  thread.title = chatTitleFromMessages(thread.messages);
  thread.updatedAt = new Date().toISOString();
  if (usage) thread.lastUsage = usage;
  state.chatThreads = [thread, ...state.chatThreads.filter((item) => item.id !== thread.id)];
  persistChatHistory();
  renderChatHistory();
  saveChatThreadRemote(thread);
}

function renderChatHistory() {
  const list = $("chatHistoryList");
  if (!list) return;
  if (!state.chatThreads.length) {
    list.innerHTML = '<div class="chat-history-empty">暂无历史对话</div>';
    return;
  }
  list.innerHTML = state.chatThreads
    .map((thread) => {
      const tokens = thread.lastUsage?.total_tokens ? `${formatInt(thread.lastUsage.total_tokens)} Token` : "";
      return `<button class="chat-history-item ${thread.id === state.activeChatId ? "active" : ""}" type="button" data-chat-id="${escapeHtml(thread.id)}">
        <strong>${escapeHtml(thread.title || "新对话")}</strong>
        <span>${escapeHtml(thread.model || "-")}${tokens ? ` · ${escapeHtml(tokens)}` : ""}</span>
      </button>`;
    })
    .join("");
}

function openChatThread(threadId) {
  const thread = state.chatThreads.find((item) => item.id === threadId);
  if (!thread || state.chatLoading) return;
  state.activeChatId = thread.id;
  state.chatMessages = cloneMessages(thread.messages);
  state.chatAttachments = [];
  const models = availableChatModels();
  if (models.includes(thread.model)) $("chatModel").value = thread.model;
  renderChatHistory();
  renderChatThread();
  renderChatAttachments();
  $("chatStatus").textContent = thread.lastUsage?.total_tokens
    ? `上轮 ${formatInt(thread.lastUsage.total_tokens)} Token`
    : "历史对话已恢复";
}

function clearChatHistory() {
  if (state.chatLoading) return;
  state.chatThreads = [];
  state.activeChatId = "";
  state.chatMessages = [];
  state.chatAttachments = [];
  persistChatHistory();
  api("api/chat/threads", { method: "DELETE" }).catch(() => {});
  renderChatHistory();
  renderChatThread();
  renderChatAttachments();
  $("chatStatus").textContent = "历史对话已清空";
}

function showAuth() {
  stopUsageRealtime();
  stopResourceRealtime();
  stopAgentRealtime();
  $("bootView").classList.add("hidden");
  $("authView").classList.remove("hidden");
  $("appView").classList.add("hidden");
  if (location.hash === "#news") switchAuthTab("news");
  loadNews();
}

function showApp() {
  $("bootView").classList.add("hidden");
  $("authView").classList.add("hidden");
  $("appView").classList.remove("hidden");
  startUsageRealtime();
  startResourceRealtime();
  startAgentRealtime();
}

function switchAuthTab(tab) {
  document.querySelectorAll("[data-auth-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.authTab === tab);
  });
  document.querySelectorAll("[data-auth-form]").forEach((form) => {
    form.classList.toggle("hidden", form.dataset.authForm !== tab);
  });
}

function switchPage(pageId, { replaceHash = false } = {}) {
  const target = $(pageId) || $("dashboardPage");
  state.page = target.id;
  document.querySelectorAll("[data-page-link]").forEach((link) => {
    link.classList.toggle("active", link.dataset.pageLink === target.id);
  });
  document.querySelectorAll(".page-view").forEach((page) => {
    page.classList.toggle("hidden", page.id !== target.id);
    page.classList.toggle("active", page.id === target.id);
  });
  $("pageTitle").textContent = target.dataset.title || "账户控制台";
  if (replaceHash) {
    const hash =
      target.id === "billingPage"
        ? "#billing"
        : target.id === "docsPage"
          ? "#docs"
          : target.id === "chatPage"
            ? "#chat"
            : target.id === "agentsPage"
              ? "#agents"
              : target.id === "newsPage"
                ? "#news"
                : "#dashboard";
    history.replaceState(null, "", hash);
  }
  if (target.id === "agentsPage") loadAgentTasks();
}

function pageFromHash() {
  if (location.hash === "#billing") return "billingPage";
  if (location.hash === "#docs") return "docsPage";
  if (location.hash === "#chat") return "chatPage";
  if (location.hash === "#agents") return "agentsPage";
  if (location.hash === "#news") return "newsPage";
  return "dashboardPage";
}

async function touchSession() {
  if (!state.dashboard) return;
  const now = Date.now();
  if (now - state.lastTouchAt < 5 * 60 * 1000) return;
  state.lastTouchAt = now;
  try {
    await api("api/auth/touch", { method: "POST", body: "{}" });
  } catch (error) {
    if (error.status === 401) {
      toast("登录状态已过期，请重新登录", "error");
      showAuth();
    }
  }
}

function renderPayTypes(payTypes = []) {
  const selected = $("payType").value || "alipay";
  $("payType").innerHTML = payTypes
    .map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`)
    .join("");
  if (payTypes.some((item) => item.id === selected)) {
    $("payType").value = selected;
  }
}

function newsCard(item) {
  const views = item.views === null || item.views === undefined ? "" : `<span>浏览量 ${formatInt(item.views)}</span>`;
  return `<a class="news-card" href="${escapeHtml(item.url || `news/${item.id}`)}">
    <div class="news-card-meta">
      <span>${escapeHtml(item.date)}</span>
      <span>${escapeHtml(item.tag)}</span>
      ${views}
    </div>
    <h3>${escapeHtml(item.title)}</h3>
    <p>${escapeHtml(item.summary)}</p>
    <strong>查看详情</strong>
  </a>`;
}

function renderNewsLists() {
  const html = state.news.length
    ? state.news.map((item) => newsCard(item)).join("")
    : '<div class="empty">暂无新闻更新</div>';
  ["publicNewsList", "appNewsList"].forEach((id) => {
    const el = $(id);
    if (el) el.innerHTML = html;
  });
}

async function loadNews() {
  if (state.newsLoading) return;
  state.newsLoading = true;
  try {
    const data = await api("api/news");
    state.news = data.news || [];
    renderNewsLists();
  } catch (_) {
    state.news = [];
    renderNewsLists();
  } finally {
    state.newsLoading = false;
  }
}

function renderDocViews() {
  document.querySelectorAll("[data-doc-view]").forEach((el) => {
    const views = state.docViews[el.dataset.docView];
    el.textContent = views === undefined || views === null ? "" : `浏览量 ${formatInt(views)}`;
  });
}

async function loadDocViews() {
  if (state.docViewsLoading) return;
  state.docViewsLoading = true;
  try {
    const data = await api("api/docs/views");
    state.docViews = Object.fromEntries((data.docs || []).map((item) => [item.page_key, item.views]));
    renderDocViews();
  } catch (_) {
    state.docViews = {};
    renderDocViews();
  } finally {
    state.docViewsLoading = false;
  }
}

function renderAgentTasks(data = {}) {
  const summary = data.summary || {};
  const sessions = data.sessions || [];
  state.agentTasks = sessions;
  $("agentActiveCount").textContent = formatInt(summary.active || 0);
  $("agentTotalCount").textContent = formatInt(summary.total || sessions.length);
  $("agentStaleCount").textContent = formatInt(summary.stale || 0);
  $("agentCompletedCount").textContent = formatInt(summary.completed || 0);

  const board = $("agentTaskBoard");
  if (!sessions.length) {
    board.innerHTML = '<div class="empty">暂无 Agent 任务。配置 Codex / Claude Code hooks 后会显示当前会话状态。</div>';
    return;
  }

  board.innerHTML = sessions
    .map((item) => {
      const context =
        item.context_window && item.remaining_context !== null && item.remaining_context !== undefined
          ? `${formatInt(item.remaining_context)} / ${formatInt(item.context_window)}`
          : item.context_window
          ? `未上报 / ${formatInt(item.context_window)}`
          : "-";
      const tokenText = item.used_tokens ? formatInt(item.used_tokens) : "-";
      return `<article class="agent-task-card ${escapeHtml(item.status)}">
        <div class="agent-task-head">
          <div>
            <span class="agent-client">${escapeHtml(item.client)}</span>
            <h3>${escapeHtml(item.first_prompt || "-")}</h3>
          </div>
          <div class="agent-task-actions">
            <span class="agent-status ${escapeHtml(item.status)}">${escapeHtml(agentStatusLabel(item.status))}</span>
            <a href="agent/sessions/${encodeURIComponent(item.id)}" target="_blank" rel="noreferrer">查看记录</a>
          </div>
        </div>
        <div class="agent-task-summary">
          <span>步骤：${escapeHtml(item.current_step || "-")}</span>
          <span>工具：${escapeHtml(item.current_tool || "-")}</span>
          <span>模型：${escapeHtml(item.model || "-")}</span>
          <span>Token：${tokenText}</span>
          <span>上下文：${context}</span>
          <span>事件：${formatInt(item.event_count || 0)}</span>
          <span>最近：${formatLocalTime(item.last_seen_at)}</span>
        </div>
      </article>`;
    })
    .join("");
}

async function loadAgentTasks({ silent = true } = {}) {
  if (!state.dashboard || state.agentLoading) return;
  state.agentLoading = true;
  try {
    const data = await api("api/agent/sessions");
    renderAgentTasks(data);
    if (!silent) toast("Agent 任务已刷新");
  } catch (error) {
    if (!silent) toast(error.message, "error");
  } finally {
    state.agentLoading = false;
  }
}

function setResourceLiveStatus(status, text) {
  const el = $("resourceLiveStatus");
  if (!el) return;
  el.className = `live-pill ${status || ""}`.trim();
  el.textContent = text;
}

function resourceReasonLabel(reason) {
  const text = String(reason || "").trim();
  if (!text) return "";
  const lower = text.toLowerCase();
  if (lower.includes("token_expired") || lower.includes("token expired")) return "OAuth Token 已过期";
  if (lower.includes("refresh_failed")) return "Token 刷新失败";
  if (lower.includes("disabled")) return "账号已停用";
  if (lower.includes("banned")) return "账号不可用";
  if (lower.includes("quota")) return "额度受限";
  return text;
}

function resourceHealth(service) {
  const five = service.windows?.five_hour || {};
  const seven = service.windows?.seven_day || {};
  const remainingValues = [five.remaining_percent, seven.remaining_percent]
    .map(Number)
    .filter((value) => Number.isFinite(value));
  const minRemaining = remainingValues.length ? Math.min(...remainingValues) : null;
  const noWindowCapacity = remainingValues.length >= 2 && remainingValues.every((value) => value <= 0);
  if (service.available === false || service.reachable === false || service.authenticated === false) {
    return { className: "warn", text: "暂不可用" };
  }
  if (noWindowCapacity) return { className: "warn", text: "等待恢复" };
  if (minRemaining !== null && minRemaining <= 10) return { className: "warn", text: "余量紧张" };
  return { className: "ok", text: "可用" };
}

function resourceWindowHtml(service, key, title) {
  const windowData = service.windows?.[key] || {};
  const remaining = windowData.remaining_percent;
  const width = percentNumber(remaining);
  const resetText = windowData.reset_at ? `重置 ${formatLocalTime(windowData.reset_at)}` : "暂无重置信息";
  const detailText =
    service.service === "codex"
      ? "资源池合计"
      : windowData.status === "allowed"
        ? "当前窗口可用"
        : windowData.limit_reached
          ? "当前窗口受限"
          : "窗口数据待同步";
  return `<div class="resource-window">
    <div class="resource-window-head">
      <div>
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(detailText)}</span>
      </div>
      <b>${escapeHtml(formatPercent(remaining))}</b>
    </div>
    <div class="resource-meter" aria-label="${escapeHtml(title)}">
      <div style="width: ${width}%"></div>
    </div>
    <small>${escapeHtml(resetText)}</small>
  </div>`;
}

function renderResourceStatus(data = state.resourceStatus) {
  const cards = $("resourceCards");
  if (!cards) return;
  const services = data?.services || {};
  const entries = ["claude", "codex"].map((name) => services[name]).filter(Boolean);
  $("resourceUpdatedAt").textContent = data?.generated_at ? `更新于 ${formatLocalTime(data.generated_at)}` : "-";
  if (!entries.length) {
    cards.innerHTML = '<div class="empty">暂无资源池状态</div>';
    return;
  }
  cards.innerHTML = entries
    .map((service) => {
      const health = resourceHealth(service);
      const five = service.windows?.five_hour || {};
      const seven = service.windows?.seven_day || {};
      const fiveRemaining = Number(five.remaining_percent);
      const sevenRemaining = Number(seven.remaining_percent);
      const bothEmpty = Number.isFinite(fiveRemaining) && Number.isFinite(sevenRemaining) && fiveRemaining <= 0 && sevenRemaining <= 0;
      const recovery = bothEmpty
        ? `<div class="resource-recovery">
            <span>预计有余量时间</span>
            <strong>${escapeHtml(formatLocalTime(service.next_available_at))}</strong>
          </div>`
        : "";
      const reason = resourceReasonLabel(service.state_reason);
      const reasonHtml = reason ? `<div class="resource-note">原因：${escapeHtml(reason)}</div>` : "";
      const lastModel = service.last_model ? `<span>最近模型：${escapeHtml(service.last_model)}</span>` : "";
      const serviceText = health.text;
      return `<article class="resource-card ${escapeHtml(service.service || "")} ${health.className}">
        <div class="resource-card-head">
          <div>
            <span>${escapeHtml(service.label || service.service || "-")}</span>
            <h3>${escapeHtml(service.service === "claude" ? "Claude Code / Claude API" : "Codex / OpenAI")}</h3>
          </div>
          <b class="resource-state ${health.className}">${escapeHtml(health.text)}</b>
        </div>
        <div class="resource-metrics">
          <div><span>资源状态</span><strong>${escapeHtml(serviceText)}</strong></div>
          <div><span>路由模式</span><strong>${escapeHtml(service.routing_mode || "-")}</strong></div>
          <div><span>累计请求</span><strong>${formatInt(service.request_count || 0)}</strong></div>
        </div>
        ${resourceWindowHtml(service, "five_hour", "5h 资源余量")}
        ${resourceWindowHtml(service, "seven_day", "7d 资源余量")}
        ${recovery}
        ${reasonHtml}
        <div class="resource-foot">
          <span>检查时间：${escapeHtml(formatLocalTime(service.checked_at))}</span>
          ${lastModel}
        </div>
      </article>`;
    })
    .join("");
}

async function loadResourceStatus({ silent = true } = {}) {
  if (!state.dashboard || state.resourceLoading || document.hidden) return;
  state.resourceLoading = true;
  setResourceLiveStatus("loading", "同步中");
  try {
    const data = await api("api/resource-status");
    state.resourceStatus = data;
    renderResourceStatus(data);
    setResourceLiveStatus(data.ok ? "ok" : "warn", data.ok ? "5分钟刷新" : "部分异常");
    if (!silent) toast("资源概览已刷新");
  } catch (error) {
    setResourceLiveStatus("warn", "延迟");
    if (error.status === 401) {
      toast("登录状态已过期，请重新登录", "error");
      showAuth();
      return;
    }
    if (!state.resourceStatus && $("resourceCards")) {
      $("resourceCards").innerHTML = '<div class="empty">资源状态暂时无法读取</div>';
    }
    if (!silent) toast(error.message, "error");
  } finally {
    state.resourceLoading = false;
  }
}

function renderPlans(plans = []) {
  const grid = $("plansGrid");
  if (!plans.length) {
    grid.innerHTML = '<div class="empty">暂无套餐</div>';
    return;
  }
  grid.innerHTML = plans
    .map((plan) => {
      const customPrice = plan.custom_equivalent_price_cny;
      const discount = plan.discount_cny;
      const discountRate = plan.discount_rate;
      const savingBlock =
        customPrice && discount
          ? `<div class="plan-saving">
              <span>同配自定义价 <s>${formatCny(customPrice)}</s></span>
              <strong>套餐立省 ${formatCny(discount)}（${formatDiscountRate(discountRate)}）</strong>
            </div>`
          : "";
      return `<article class="plan-card">
        <div class="plan-title">
          <h3>${escapeHtml(plan.name)}</h3>
          <strong>${formatCny(plan.price_cny)}</strong>
        </div>
        ${savingBlock}
        <p>${escapeHtml(plan.description)}</p>
        <ul>
          <li>模型：${escapeHtml(plan.models.join(" / "))}</li>
          <li>预算：${formatUsd(plan.max_budget_usd)}</li>
          <li>有效期：${escapeHtml(plan.duration)}</li>
          <li>请求数：${formatInt(plan.total_request_limit)} 次，总量 / ${formatInt(plan.daily_request_limit)} 次每日</li>
          <li>窗口：${formatInt(plan.five_hour_request_limit)} 次/5小时 / ${formatInt(plan.weekly_request_limit)} 次/周</li>
          <li>RPM：${formatInt(plan.rpm_limit)} 请求/分钟</li>
          <li>TPM：${formatInt(plan.tpm_limit)} Token/分钟</li>
        </ul>
        <div class="plan-actions">
          <button data-plan="${escapeHtml(plan.id)}">购买优惠包</button>
          <button class="secondary" type="button" data-custom-plan="${escapeHtml(plan.id)}">按同配自定义</button>
        </div>
      </article>`;
    })
    .join("");
  grid.querySelectorAll("button[data-plan]").forEach((button) => {
    button.addEventListener("click", () => createOrder(button.dataset.plan));
  });
  grid.querySelectorAll("button[data-custom-plan]").forEach((button) => {
    button.addEventListener("click", () => fillCustomFromPlan(button.dataset.customPlan));
  });
}

function selectedCustomModels() {
  return Array.from(document.querySelectorAll("input[data-custom-model]:checked")).map((item) => item.value);
}

function selectedOption(options = [], selectId) {
  const value = $(selectId)?.value;
  return options.find((item) => item.id === value) || options[0] || {};
}

function optionPrice(option) {
  return Number(option?.price_cny || 0);
}

function selectedCustomOutputLimit() {
  const models = state.catalog?.models || [];
  const selected = new Set(selectedCustomModels());
  return models.reduce((limit, model) => {
    if (!selected.has(model.id)) return limit;
    const outputLimit = Number(model.max_output_tokens || model.max_tokens || 0);
    return Math.max(limit, Number.isFinite(outputLimit) ? outputLimit : 0);
  }, 0);
}

function ensureCustomTpmOption() {
  const custom = state.catalog?.custom || {};
  const options = custom.tpm_options || [];
  const select = $("customTpmOption");
  if (!select || !options.length) return null;
  const requiredTpm = selectedCustomOutputLimit();
  if (!requiredTpm) return null;
  const current = selectedOption(options, "customTpmOption");
  if (Number(current.tpm_limit || 0) >= requiredTpm) return null;
  const target = options.find((option) => Number(option.tpm_limit || 0) >= requiredTpm) || options[options.length - 1];
  if (target?.id && target.id !== select.value) {
    select.value = target.id;
  }
  return target ? { option: target, requiredTpm } : null;
}

function customPrice() {
  const custom = state.catalog?.custom || {};
  const budget = Number($("customBudget").value || custom.default_budget_usd || 0);
  const requestOption = selectedOption(custom.request_options || [], "customRequestOption");
  const rpmOption = selectedOption(custom.rpm_options || [], "customRpmOption");
  const tpmOption = selectedOption(custom.tpm_options || [], "customTpmOption");
  const price =
    budget * Number(custom.cny_per_usd || 0) * Number(custom.price_multiplier || 1) +
    optionPrice(requestOption) +
    optionPrice(rpmOption) +
    optionPrice(tpmOption);
  return Math.max(price, Number(custom.min_price_cny || 0));
}

function updateCustomEstimate() {
  const adjustedTpm = ensureCustomTpmOption();
  $("customPrice").textContent = formatCny(customPrice());
  const custom = state.catalog?.custom || {};
  const baseNote = custom.public_note || "预算按 1 USD = 7.2 CNY 换算，请求数和限速按所选档位计费。";
  if (adjustedTpm?.option) {
    $("capacityNote").textContent = `${baseNote} 已按所选模型输出上限自动选择 ${adjustedTpm.option.name || adjustedTpm.option.id}。`;
  } else {
    $("capacityNote").textContent = baseNote;
  }
}

function setSelectValue(id, value) {
  const el = $(id);
  if (!el || !value) return;
  if (Array.from(el.options).some((option) => option.value === value)) {
    el.value = value;
  }
}

function renderOptionSelect(id, options = [], defaultId = "") {
  const el = $(id);
  el.innerHTML = options
    .map((option) => {
      const price = optionPrice(option);
      const suffix = price > 0 ? ` +${formatCny(price)}` : "";
      return `<option value="${escapeHtml(option.id)}">${escapeHtml(option.name || option.id)}${suffix}</option>`;
    })
    .join("");
  if (options.some((option) => option.id === defaultId)) {
    el.value = defaultId;
  }
}

function renderCustomBuilder(catalog) {
  const builder = $("customBuilder");
  const custom = catalog?.custom || {};
  if (!custom.enabled) {
    builder.classList.add("hidden");
    return;
  }
  builder.classList.remove("hidden");
  $("customDescription").textContent = custom.description || "自由选择模型组合和预算额度。";

  const budget = $("customBudget");
  budget.min = custom.min_budget_usd ?? 0.05;
  budget.max = custom.max_budget_usd ?? 50;
  budget.step = custom.budget_step_usd ?? 0.05;
  if (!budget.dataset.initialized) {
    budget.value = custom.default_budget_usd ?? custom.min_budget_usd ?? 1;
    budget.dataset.initialized = "1";
  }

  $("customDuration").innerHTML = (custom.duration_options || [custom.default_duration || "30d"])
    .map((duration) => `<option value="${escapeHtml(duration)}">${escapeHtml(duration)}</option>`)
    .join("");
  $("customDuration").value = custom.default_duration || $("customDuration").value;
  renderOptionSelect("customRequestOption", custom.request_options || [], custom.default_request_option);
  renderOptionSelect("customRpmOption", custom.rpm_options || [], custom.default_rpm_option);
  renderOptionSelect("customTpmOption", custom.tpm_options || [], custom.default_tpm_option);
  $("capacityNote").textContent =
    custom.public_note || "预算按 1 USD = 7.2 CNY 换算，请求数和限速按所选档位计费。";

  const models = catalog?.models || [];
  $("customModels").innerHTML = models
    .map(
      (model, index) => `<label class="model-choice">
        <input type="checkbox" value="${escapeHtml(model.id)}" data-custom-model ${index === 0 ? "checked" : ""} />
        <span>
          <strong>${escapeHtml(model.name || model.id)}</strong>
          <small>${escapeHtml(model.description || model.id)}</small>
          <span class="model-meta">入 ${formatPerMillionToken(model.input_cost_per_token)} / 出 ${formatPerMillionToken(model.output_cost_per_token)} / 百万Token</span>
          <span class="model-meta">上下文 ${formatInt(model.max_tokens)} / 输出 ${formatInt(model.max_output_tokens)}</span>
        </span>
      </label>`
    )
    .join("");
  document.querySelectorAll("input[data-custom-model]").forEach((input) => {
    input.addEventListener("change", updateCustomEstimate);
  });
  updateCustomEstimate();
}

function fillCustomFromPlan(planId) {
  const plan = (state.dashboard?.plans || []).find((item) => item.id === planId);
  if (!plan) return;
  switchPage("billingPage", { replaceHash: true });
  $("customBudget").value = plan.max_budget_usd;
  setSelectValue("customDuration", plan.duration);
  setSelectValue("customRequestOption", plan.request_tier);
  setSelectValue("customRpmOption", plan.rpm_tier);
  setSelectValue("customTpmOption", plan.tpm_tier);
  document.querySelectorAll("input[data-custom-model]").forEach((input) => {
    input.checked = plan.models.includes(input.value);
  });
  updateCustomEstimate();
  $("customBuilder").scrollIntoView({ behavior: "smooth", block: "start" });
  toast(`已按${plan.name}填充自定义配置，可对比套餐优惠`);
}

function renderOrders(orders = []) {
  const box = $("orderList");
  if (!orders.length) {
    box.innerHTML = '<div class="empty">暂无订单</div>';
    return;
  }
  const hasPendingRefund = orders.some((order) => order.status === "refund_pending");
  const refundable = orders.find((order) => order.status === "refund_pending") || orders.find((order) => order.status === "paid");
  const refundEntry = refundable
    ? `<div class="refund-entry ${refundable.status === "refund_pending" ? "pending-refund" : ""}">
        <div>
          <strong>${refundable.status === "refund_pending" ? "人工退款处理中" : "可退款订单"}</strong>
          <small>${escapeHtml(refundable.plan.name || refundable.plan.id)} · ${escapeHtml(refundable.out_trade_no)}</small>
        </div>
        <span>${refundable.status === "refund_pending" && refundable.refund?.amount_cny ? `预计可退 ${formatCny(refundable.refund.amount_cny)}` : `订单金额 ${formatCny(refundable.amount_cny)}`}</span>
        <button class="secondary" type="button" data-refund="${escapeHtml(refundable.out_trade_no)}">${refundable.status === "refund_pending" ? "查看指引" : "退款测算"}</button>
      </div>`
    : '<div class="refund-entry muted"><div><strong>暂无可退款订单</strong><small>只有已支付订单可以申请退款。</small></div></div>';
  box.innerHTML =
    refundEntry +
    orders
    .map((order) => {
      const status =
        order.status === "paid"
          ? "已完成"
          : order.status === "failed"
            ? "失败"
            : order.status === "refund_pending"
              ? "人工退款处理中"
              : "待支付";
      const actions = [];
      if (order.status === "pending") {
        actions.push(`<a class="mini-button" href="${escapeHtml(order.payment_url)}" target="_blank">继续支付</a>`);
      }
      if (order.status === "paid" && !hasPendingRefund) {
        actions.push(`<button class="secondary mini-action" type="button" data-refund="${escapeHtml(order.out_trade_no)}">退款测算</button>`);
      }
      if (order.status === "refund_pending" && order.refund?.amount_cny) {
        actions.push(`<span class="refund-mini">预计退 ${formatCny(order.refund.amount_cny)}</span>`);
        actions.push(`<button class="secondary mini-action" type="button" data-refund="${escapeHtml(order.out_trade_no)}">查看指引</button>`);
      }
      return `<div class="order-row">
        <div>
          <strong>${escapeHtml(order.plan.name || order.plan.id)}</strong>
          <small>${escapeHtml(order.out_trade_no)}</small>
        </div>
        <span>${formatCny(order.amount_cny)}</span>
        <span>${escapeHtml(order.pay_type_label || order.pay_type)}</span>
        <span class="badge ${escapeHtml(order.status)}">${status}</span>
        <div class="order-actions">${actions.join("")}</div>
      </div>`;
    })
    .join("");
  box.querySelectorAll("[data-refund]").forEach((button) => {
    button.addEventListener("click", () => openRefund(button.dataset.refund));
  });
}

function closeRefundModal() {
  state.refundQuote = null;
  $("refundModal").classList.add("hidden");
}

function renderRefundModal(data) {
  const quote = data.quote;
  state.refundQuote = quote;
  $("refundOrderNo").textContent = `订单号：${quote.out_trade_no}`;
  $("refundCopyOrder").textContent = quote.out_trade_no || "-";
  $("refundOrderAmount").textContent = formatCny(quote.amount_cny);
  $("refundChannelFee").textContent = `${formatCny(quote.channel_fee_cny)}（费率 ${Number(quote.channel_fee_rate || 0) * 100}%）`;
  $("refundTokenCost").textContent = `${formatCny(quote.token_cost_cny)}（${formatUsd(quote.token_cost_usd)}，按 1 USD = ${quote.cny_per_usd} CNY）`;
  $("refundRequests").textContent = formatInt(quote.usage_request_count);
  const deduction = quote.budget_deduction || {};
  $("refundBudgetDeduction").textContent = deduction.deducted_budget_usd
    ? `${formatUsd(deduction.deducted_budget_usd)}（${formatUsd(deduction.previous_max_budget_usd)} -> ${formatUsd(deduction.new_max_budget_usd)}）`
    : "提交后自动扣减本订单额度";
  $("refundAmount").textContent = formatCny(quote.refund_amount_cny);
  $("refundApiKeyMasked").textContent = quote.api_key_masked || state.apiKeyMasked || "-";
  $("refundContactQr").src = quote.contact_qr_url || "assets/contact-qr.png";
  $("refundSample").src = quote.receipt_sample_url || "assets/refund-sample.png";
  $("refundNote").textContent = quote.note || "确认后提交退款记录。";
  $("confirmRefund").disabled = false;
  $("confirmRefund").textContent = quote.already_requested ? "已提交申请" : "提交人工退款申请";
  $("cancelRefundRequest").classList.toggle("hidden", !quote.already_requested);
  $("refundModal").classList.remove("hidden");
}

async function openRefund(outTradeNo) {
  try {
    const data = await api(`api/orders/${encodeURIComponent(outTradeNo)}/refund`);
    renderRefundModal(data);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function confirmRefund() {
  if (!state.refundQuote?.out_trade_no) return;
  if (state.refundQuote.already_requested) {
    toast("请联系管理员，并提供订单号、API Key 和退款后台收据截图");
    return;
  }
  try {
    const data = await api(`api/orders/${encodeURIComponent(state.refundQuote.out_trade_no)}/refund`, {
      method: "POST",
      body: "{}",
    });
    if (state.dashboard?.orders) {
      state.dashboard.orders = state.dashboard.orders.map((order) =>
        order.out_trade_no === data.order.out_trade_no ? data.order : order
      );
      renderOrders(state.dashboard.orders);
    }
    if (data.user && state.dashboard) {
      state.dashboard.user = data.user;
      $("kpiStatus").textContent = statusLabel(data.user, state.dashboard.usage);
    }
    closeRefundModal();
    toast(`人工退款申请已提交，预计可退 ${formatCny(data.quote.refund_amount_cny)}`);
    refreshUsage();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function cancelRefundRequest() {
  if (!state.refundQuote?.out_trade_no) return;
  if (!confirm("确认取消退款申请并恢复账号额度？")) return;
  try {
    const data = await api(`api/orders/${encodeURIComponent(state.refundQuote.out_trade_no)}/refund/cancel`, {
      method: "POST",
      body: "{}",
    });
    if (state.dashboard?.orders) {
      state.dashboard.orders = state.dashboard.orders.map((order) =>
        order.out_trade_no === data.order.out_trade_no ? data.order : order
      );
      renderOrders(state.dashboard.orders);
    }
    if (data.user && state.dashboard) {
      state.dashboard.user = data.user;
      $("kpiStatus").textContent = statusLabel(data.user, state.dashboard.usage);
    }
    closeRefundModal();
    toast(`退款申请已取消，额度已恢复到 ${formatUsd(data.budget_restore.restored_max_budget_usd)}`);
    refreshUsage();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function copyRefundKey() {
  if (!state.dashboard?.user?.has_api_key) return;
  try {
    const data = await api("api/key/copy", { method: "POST", body: "{}" });
    await navigator.clipboard.writeText(data.api_key);
    state.apiKeyMasked = data.api_key_masked || state.apiKeyMasked;
    $("refundApiKeyMasked").textContent = state.apiKeyMasked || "-";
    $("apiKey").textContent = state.apiKeyMasked || "-";
    toast("完整 API Key 已复制");
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderBudget(usage) {
  const budget = usage?.budget || {};
  const limits = usage?.limits || {};
  const usedPercent = Math.max(0, Math.min(100, Number(budget.used_percent || 0)));
  $("kpiRemaining").textContent = formatUsd(budget.remaining_budget);
  $("kpiSpend").textContent = formatUsd(budget.spend);
  $("budgetMax").textContent = formatUsd(budget.max_budget);
  $("budgetSpend").textContent = formatUsd(budget.spend);
  $("budgetRemaining").textContent = formatUsd(budget.remaining_budget);
  $("budgetBar").style.width = `${usedPercent}%`;
  $("limitRpm").textContent = formatInt(limits.rpm_limit);
  $("limitTpm").textContent = formatInt(limits.tpm_limit);
  $("limitTotalRequests").textContent = formatQuota(limits.requests_used_total, limits.total_request_limit);
  $("limitFiveHourRequests").textContent = formatQuota(
    limits.requests_used_five_hour,
    limits.five_hour_request_limit
  );
  $("limitDailyRequests").textContent = formatQuota(limits.requests_used_today, limits.daily_request_limit);
  $("limitWeeklyRequests").textContent = formatQuota(limits.requests_used_week, limits.weekly_request_limit);
  $("limitExpires").textContent = limits.expires ? String(limits.expires).slice(0, 10) : "-";
}

function renderChart() {
  const usage = state.dashboard?.usage || {};
  const series = usage[state.period] || [];
  const data = series.slice(state.period === "daily" ? -14 : -12);
  const valueKey = state.metric === "spend" ? "spend" : "total_tokens";
  const maxValue = Math.max(...data.map((item) => Number(item[valueKey] || 0)), 1);
  const chart = $("usageChart");
  if (!data.length) {
    chart.innerHTML = '<div class="empty">暂无用量</div>';
    return;
  }
  chart.innerHTML = data
    .map((item) => {
      const value = Number(item[valueKey] || 0);
      const height = Math.max(6, Math.round((value / maxValue) * 100));
      const label = state.metric === "spend" ? formatCompactUsd(value) : formatInt(value);
      return `<div class="bar-col">
        <div class="bar-value">${label}</div>
        <div class="bar-track"><div class="bar" style="height:${height}%"></div></div>
        <span>${escapeHtml(item.label)}</span>
      </div>`;
    })
    .join("");
}

function renderModelSpend(rows = []) {
  const body = $("modelSpendRows");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty-cell">暂无用量</td></tr>';
    return;
  }
  body.innerHTML = rows
    .map(
      (row) => `<tr>
        <td>${escapeHtml(row.model)}</td>
        <td>${formatInt(row.requests)}</td>
        <td>${formatInt(row.total_tokens)}</td>
        <td>${formatUsd(row.spend)}</td>
      </tr>`
    )
    .join("");
}

function renderRecentLogs(rows = []) {
  const body = $("recentLogRows");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty-cell">暂无调用记录</td></tr>';
    return;
  }
  body.innerHTML = rows
    .slice(0, 12)
    .map(
      (row) => `<tr>
        <td>${escapeHtml(row.time)}</td>
        <td>${escapeHtml(row.model)}</td>
        <td>${formatInt(row.total_tokens)}</td>
        <td>${formatUsd(row.spend)}</td>
      </tr>`
    )
    .join("");
}

function renderModelLimits(rows = []) {
  const body = $("modelLimitRows");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="9" class="empty-cell">支付后显示可用模型</td></tr>';
    return;
  }
  body.innerHTML = rows
    .map(
      (row) => `<tr>
        <td>${escapeHtml(row.model)}</td>
        <td>${formatInt(row.max_tokens)}</td>
        <td>${formatInt(row.max_input_tokens)}</td>
        <td>${formatInt(row.max_output_tokens)}</td>
        <td>入 ${formatCompactUsd(row.input_cost_per_million_tokens)} / 出 ${formatCompactUsd(row.output_cost_per_million_tokens)}</td>
        <td>${formatInt(row.rpm_limit)} RPM / ${formatInt(row.tpm_limit)} TPM</td>
        <td>${formatInt(row.total_request_limit)} 总 / ${formatInt(row.five_hour_request_limit)} 每5小时 / ${formatInt(row.weekly_request_limit)} 每周</td>
        <td>${formatInt(row.estimated_remaining_output_tokens)}</td>
        <td>${formatInt(row.total_tokens)}</td>
      </tr>`
    )
    .join("");
}

function availableChatModels() {
  const models = state.dashboard?.usage?.models || [];
  return models.filter((row) => row.available !== false).map((row) => row.model).filter(Boolean);
}

function renderChatModels() {
  const select = $("chatModel");
  const models = availableChatModels();
  if (!models.length) {
    select.innerHTML = '<option value="">支付后可选择模型</option>';
    select.disabled = true;
    $("chatSend").disabled = true;
    $("chatStatus").textContent = "当前没有可用模型";
    return;
  }
  const activeThread = currentChatThread();
  const selected = activeThread?.model && models.includes(activeThread.model) ? activeThread.model : select.value || models[0];
  select.innerHTML = models.map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join("");
  select.value = models.includes(selected) ? selected : models[0];
  select.disabled = false;
  $("chatSend").disabled = state.chatLoading;
  if (!state.chatMessages.length) {
    $("chatStatus").textContent = `${models.length} 个可用模型`;
  }
}

function renderChatContent(content, role) {
  if (!Array.isArray(content)) {
    const text = String(content || "");
    if (role === "assistant") return `<div class="markdown-body">${renderMarkdown(text)}</div>`;
    return `<div>${escapeHtml(text).replaceAll("\n", "<br>")}</div>`;
  }
  return content
    .map((part) => {
      if (part?.type === "text") {
        const text = String(part.text || "");
        return `<div>${escapeHtml(text).replaceAll("\n", "<br>")}</div>`;
      }
      if (part?.type === "image_url") {
        const imageUrl = typeof part.image_url === "string" ? part.image_url : part.image_url?.url;
        if (!imageUrl) return "";
        return `<img class="chat-image" src="${escapeHtml(imageUrl)}" alt="用户上传的图片" />`;
      }
      return "";
    })
    .join("");
}

function renderChatAttachments() {
  const box = $("chatAttachmentPreview");
  if (!box) return;
  if (!state.chatAttachments.length) {
    box.innerHTML = "";
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.innerHTML = state.chatAttachments
    .map(
      (item, index) => `<div class="chat-attachment">
        <img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}" />
        <span>${escapeHtml(item.name)}</span>
        <button type="button" data-attachment-index="${index}" aria-label="移除图片">×</button>
      </div>`
    )
    .join("");
}

function readImageAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const type = String(file.type || "").toLowerCase();
    if (!CHAT_IMAGE_TYPES.has(type)) {
      reject(new Error("图片格式仅支持 PNG、JPG、WebP、GIF"));
      return;
    }
    if (file.size > CHAT_IMAGE_MAX_BYTES) {
      reject(new Error("单张图片不能超过 5MB"));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
}

async function addChatImages(files) {
  const incoming = Array.from(files || []);
  if (!incoming.length) return;
  if (state.chatAttachments.length + incoming.length > CHAT_IMAGE_MAX_COUNT) {
    toast("单条消息最多上传 4 张图片", "error");
    return;
  }
  try {
    const rows = [];
    for (const file of incoming) {
      const url = await readImageAsDataUrl(file);
      rows.push({ name: file.name || "image", type: file.type || "image/png", url });
    }
    state.chatAttachments.push(...rows);
    renderChatAttachments();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    $("chatImageInput").value = "";
  }
}

async function pasteChatImages(event) {
  const files = Array.from(event.clipboardData?.files || []).filter((file) =>
    String(file.type || "").toLowerCase().startsWith("image/")
  );
  if (!files.length) return;
  event.preventDefault();
  await addChatImages(files);
  const count = files.length;
  $("chatStatus").textContent = `已粘贴 ${count} 张图片`;
}

function buildUserChatContent(text, attachments = []) {
  if (!attachments.length) return text;
  const parts = [];
  if (text) parts.push({ type: "text", text });
  attachments.forEach((item) => {
    parts.push({
      type: "image_url",
      image_url: {
        url: item.url,
        detail: "auto",
      },
    });
  });
  return parts;
}

function optionalNumericField(id) {
  const raw = String($(id)?.value ?? "").trim();
  if (raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function chatRequestParams() {
  const params = {};
  const temperature = optionalNumericField("chatTemperature");
  const topP = optionalNumericField("chatTopP");
  const maxTokens = optionalNumericField("chatMaxTokens");
  const frequencyPenalty = optionalNumericField("chatFrequencyPenalty");
  const presencePenalty = optionalNumericField("chatPresencePenalty");
  if (temperature !== null) params.temperature = temperature;
  if (topP !== null && topP !== 1) params.top_p = topP;
  if (maxTokens !== null && maxTokens > 0) params.max_tokens = maxTokens;
  if (frequencyPenalty !== null && frequencyPenalty !== 0) params.frequency_penalty = frequencyPenalty;
  if (presencePenalty !== null && presencePenalty !== 0) params.presence_penalty = presencePenalty;
  return params;
}

function renderChatThread() {
  const thread = $("chatThread");
  const visibleMessages = state.chatMessages.filter((message) => message.role !== "system");
  if (!visibleMessages.length) {
    thread.innerHTML = '<div class="chat-empty">选择模型后开始对话</div>';
    return;
  }
  thread.innerHTML = visibleMessages
    .map((message) => {
      const body = renderChatContent(message.content, message.role);
      const usage =
        message.role === "assistant" && message.usage?.total_tokens
          ? `<span class="chat-token-pill">${formatInt(message.usage.total_tokens)} Token</span>`
          : "";
      return `<div class="chat-message ${escapeHtml(message.role)}">
        <strong>${message.role === "user" ? "你" : "助手"}</strong>
        ${body}
        ${usage}
      </div>`;
    })
    .join("");
  if (window.highlightCodeBlocks) window.highlightCodeBlocks(thread);
  thread.scrollTop = thread.scrollHeight;
}

function resetChat() {
  if (state.chatLoading) return;
  state.activeChatId = "";
  state.chatMessages = [];
  state.chatAttachments = [];
  renderChatHistory();
  renderChatThread();
  renderChatAttachments();
  $("chatInput").value = "";
  $("chatStatus").textContent = availableChatModels().length ? "新对话已开始" : "当前没有可用模型";
}

async function sendChatMessage() {
  const model = $("chatModel").value;
  const content = $("chatInput").value.trim();
  if (!model) {
    toast("请先购买并激活模型", "error");
    return;
  }
  if ((!content && !state.chatAttachments.length) || state.chatLoading) return;
  ensureChatThread(model);
  state.chatMessages.push({ role: "user", content: buildUserChatContent(content, state.chatAttachments) });
  const assistantMessage = { role: "assistant", content: "" };
  state.chatMessages.push(assistantMessage);
  saveActiveChatThread();
  $("chatInput").value = "";
  state.chatAttachments = [];
  renderChatAttachments();
  renderChatThread();
  state.chatLoading = true;
  $("chatSend").disabled = true;
  $("chatStatus").textContent = "模型回复中...";
  let finalUsage = null;
  try {
    await streamApi(
      "api/chat/stream",
      {
        model,
        messages: state.chatMessages.slice(-40),
        ...chatRequestParams(),
      },
      (event, data) => {
        if (event === "delta") {
          assistantMessage.content += data.content || "";
          renderChatThread();
        }
        if (event === "usage") {
          finalUsage = data.usage || finalUsage;
        }
        if (event === "done") {
          finalUsage = data.usage || finalUsage;
        }
      }
    );
    if (!assistantMessage.content) {
      assistantMessage.content = "模型没有返回内容。";
    }
    if (finalUsage?.total_tokens) {
      assistantMessage.usage = finalUsage;
    }
    saveActiveChatThread({ usage: finalUsage });
    renderChatThread();
    const usage = finalUsage || {};
    $("chatStatus").textContent = usage.total_tokens ? `本轮 ${formatInt(usage.total_tokens)} Token` : "回复完成";
    window.setTimeout(() => refreshUsage(), 1000);
  } catch (error) {
    state.chatMessages = state.chatMessages.slice(0, -2);
    saveActiveChatThread();
    renderChatThread();
    $("chatStatus").textContent = "发送失败";
    toast(error.message, "error");
  } finally {
    state.chatLoading = false;
    $("chatSend").disabled = !availableChatModels().length;
  }
}

function setLiveStatus(status, text) {
  const el = $("liveStatus");
  if (!el) return;
  el.className = `live-pill ${status || ""}`.trim();
  el.textContent = text;
}

function hasUsageErrors(usage) {
  return Array.isArray(usage?.errors) && usage.errors.length > 0;
}

function renderUsage(usage, user = state.dashboard?.user) {
  if (!state.dashboard) return;
  state.dashboard.usage = usage || {};
  if (user) state.dashboard.user = user;

  $("kpiStatus").textContent = statusLabel(state.dashboard.user, state.dashboard.usage);
  $("kpiTokens").textContent = formatInt(state.dashboard.usage.totals?.total_tokens);
  $("usageUpdatedAt").textContent = state.dashboard.usage.refreshed_at
    ? `更新于 ${state.dashboard.usage.refreshed_at}`
    : "-";

  renderBudget(state.dashboard.usage);
  renderChart();
  renderModelSpend(state.dashboard.usage.by_model || []);
  renderRecentLogs(state.dashboard.usage.recent_logs || []);
  renderModelLimits(state.dashboard.usage.models || []);
  renderChatModels();
}

function renderDashboard(data) {
  state.dashboard = data;
  state.catalog = data.catalog || { plans: data.plans || [], models: [], custom: {} };
  state.apiKey = "";
  state.apiKeyMasked = data.user.api_key_masked || data.user.api_key || "";
  loadChatHistory();
  showApp();

  const usage = data.usage || {};
  $("userEmail").textContent = displayEmail(data.user);
  $("summaryBase").textContent = data.user.base_url;
  $("apiKey").textContent = state.apiKeyMasked || "-";
  $("gatewayStatus").textContent = data.zpay_configured ? "支付服务已配置" : "支付服务未配置";
  $("gatewayStatus").className = `status-pill ${data.zpay_configured ? "ok" : "warn"}`;

  renderPayTypes(data.pay_types || []);
  renderPlans(data.plans || []);
  renderCustomBuilder(state.catalog);
  renderOrders(data.orders || []);
  renderChatModels();
  renderChatHistory();
  renderChatThread();
  loadDocViews();
  loadNews();
  loadAgentTasks();
  loadResourceStatus();
  loadServerChatHistory();
  const starter = (data.plans || []).find((plan) => plan.id === "starter") || (data.plans || [])[0];
  if (starter) $("trialPrice").textContent = formatCny(starter.price_cny);
  renderUsage(usage, data.user);
  setLiveStatus(
    usage.refreshed_at ? (hasUsageErrors(usage) ? "warn" : "ok") : "loading",
    usage.refreshed_at ? (hasUsageErrors(usage) ? "部分延迟" : "实时") : "同步中"
  );
}

async function loadDashboard() {
  const data = await api("api/dashboard");
  renderDashboard(data);
}

async function refreshUsage({ silent = true } = {}) {
  if (!state.dashboard || state.usageLoading || document.hidden) return;
  state.usageLoading = true;
  setLiveStatus("loading", "同步中");
  try {
    const data = await api(silent ? "api/usage" : "api/usage?activity=1");
    renderUsage(data.usage, data.user);
    setLiveStatus(hasUsageErrors(data.usage) ? "warn" : "ok", hasUsageErrors(data.usage) ? "部分延迟" : "实时");
    if (!silent) toast("用量已刷新");
  } catch (error) {
    if (error.status === 401) {
      setLiveStatus("warn", "过期");
      toast("登录状态已过期，请重新登录", "error");
      showAuth();
      return;
    }
    setLiveStatus("warn", "延迟");
    if (!silent) toast(error.message, "error");
  } finally {
    state.usageLoading = false;
  }
}

function startUsageRealtime() {
  if (state.usageTimer) return;
  state.usageTimer = window.setInterval(() => refreshUsage(), state.usageRefreshMs);
}

function stopUsageRealtime() {
  if (!state.usageTimer) return;
  window.clearInterval(state.usageTimer);
  state.usageTimer = null;
}

function startResourceRealtime() {
  if (state.resourceTimer) return;
  state.resourceTimer = window.setInterval(() => loadResourceStatus(), state.resourceRefreshMs);
}

function stopResourceRealtime() {
  if (!state.resourceTimer) return;
  window.clearInterval(state.resourceTimer);
  state.resourceTimer = null;
}

function startAgentRealtime() {
  if (state.agentTimer) return;
  state.agentTimer = window.setInterval(() => {
    if (state.page === "agentsPage") loadAgentTasks();
  }, state.agentRefreshMs);
}

function stopAgentRealtime() {
  if (!state.agentTimer) return;
  window.clearInterval(state.agentTimer);
  state.agentTimer = null;
}

async function createOrder(planId, custom = null) {
  try {
    const data = await api("api/orders", {
      method: "POST",
      body: JSON.stringify({
        plan_id: planId,
        pay_type: $("payType").value,
        custom,
      }),
    });
    renderOrders([data.order, ...(state.dashboard?.orders || [])]);
    window.open(data.order.payment_url, "_blank");
    toast("订单已创建");
  } catch (error) {
    toast(error.message, "error");
  }
}

document.querySelectorAll("[data-auth-tab]").forEach((button) => {
  button.addEventListener("click", () => switchAuthTab(button.dataset.authTab));
});

document.querySelectorAll("[data-page-link]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    switchPage(link.dataset.pageLink, { replaceHash: true });
    touchSession();
  });
});

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        email: $("loginEmail").value,
        password: $("loginPassword").value,
      }),
    });
    renderDashboard(data);
    switchPage(pageFromHash());
    toast("已登录");
    refreshUsage();
  } catch (error) {
    toast(error.message, "error");
  }
});

$("keyLoginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("api/auth/key-login", {
      method: "POST",
      body: JSON.stringify({
        api_key: $("loginApiKey").value,
      }),
    });
    $("loginApiKey").value = "";
    renderDashboard(data);
    switchPage(pageFromHash());
    toast("已登录");
    refreshUsage();
  } catch (error) {
    toast(error.message, "error");
  }
});

$("registerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        email: $("registerEmail").value,
        password: $("registerPassword").value,
      }),
    });
    renderDashboard(data);
    switchPage("billingPage", { replaceHash: true });
    const mailSent = String(data.registration_email_status || "").startsWith("sent:");
    toast(mailSent ? "注册成功，Key 邮件已发送" : "注册成功");
    refreshUsage();
  } catch (error) {
    toast(error.message, "error");
  }
});

$("forgotForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("api/auth/forgot", {
      method: "POST",
      body: JSON.stringify({ email: $("forgotEmail").value }),
    });
    toast("重置邮件已发送");
    switchAuthTab("login");
  } catch (error) {
    toast(error.message, "error");
  }
});

$("logoutBtn").addEventListener("click", async () => {
  try {
    await api("api/auth/logout", { method: "POST", body: "{}" });
  } catch (_) {}
  state.dashboard = null;
  state.apiKey = "";
  state.apiKeyMasked = "";
  state.resourceStatus = null;
  state.chatThreads = [];
  state.activeChatId = "";
  state.chatMessages = [];
  state.chatAttachments = [];
  showAuth();
});

$("refreshBtn").addEventListener("click", async () => {
  try {
    await refreshUsage({ silent: false });
    await loadResourceStatus();
  } catch (error) {
    toast(error.message, "error");
  }
});

$("refreshAgents").addEventListener("click", () => loadAgentTasks({ silent: false }));

$("chatForm").addEventListener("submit", (event) => {
  event.preventDefault();
  sendChatMessage();
});

$("chatInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendChatMessage();
  }
});
$("chatInput").addEventListener("paste", pasteChatImages);

$("chatAttachImage").addEventListener("click", () => $("chatImageInput").click());
$("chatImageInput").addEventListener("change", (event) => addChatImages(event.target.files));
$("chatAttachmentPreview").addEventListener("click", (event) => {
  const button = event.target.closest("[data-attachment-index]");
  if (!button) return;
  const index = Number(button.dataset.attachmentIndex);
  if (Number.isInteger(index)) {
    state.chatAttachments.splice(index, 1);
    renderChatAttachments();
  }
});

$("newChat").addEventListener("click", resetChat);
$("chatHistoryList").addEventListener("click", (event) => {
  const item = event.target.closest("[data-chat-id]");
  if (item) openChatThread(item.dataset.chatId);
});
$("clearChatHistory").addEventListener("click", () => {
  if (window.confirm("确认清空当前浏览器里的聊天历史？")) {
    clearChatHistory();
  }
});

$("closeRefund").addEventListener("click", closeRefundModal);
$("cancelRefund").addEventListener("click", closeRefundModal);
$("confirmRefund").addEventListener("click", confirmRefund);
$("cancelRefundRequest").addEventListener("click", cancelRefundRequest);
$("copyRefundKey").addEventListener("click", copyRefundKey);
$("refundModal").addEventListener("click", (event) => {
  if (event.target === $("refundModal")) closeRefundModal();
});

$("customBudget").addEventListener("input", updateCustomEstimate);
$("customDuration").addEventListener("change", updateCustomEstimate);
$("customRequestOption").addEventListener("change", updateCustomEstimate);
$("customRpmOption").addEventListener("change", updateCustomEstimate);
$("customTpmOption").addEventListener("change", updateCustomEstimate);
$("createCustomOrder").addEventListener("click", () => {
  const custom = state.catalog?.custom || {};
  const modelIds = selectedCustomModels();
  const minModels = Number(custom.min_models || 1);
  if (modelIds.length < minModels) {
    toast(`至少选择 ${minModels} 个模型`, "error");
    return;
  }
  createOrder("custom", {
    model_ids: modelIds,
    budget_usd: Number($("customBudget").value || custom.default_budget_usd || 0),
    duration: $("customDuration").value,
    request_option: $("customRequestOption").value,
    rpm_option: $("customRpmOption").value,
    tpm_option: $("customTpmOption").value,
  });
});

$("copyKey").addEventListener("click", async () => {
  if (!state.dashboard?.user?.has_api_key) return;
  try {
    const data = await api("api/key/copy", { method: "POST", body: "{}" });
    await navigator.clipboard.writeText(data.api_key);
    state.apiKeyMasked = data.api_key_masked || state.apiKeyMasked;
    $("apiKey").textContent = state.apiKeyMasked || "-";
    toast("完整 Key 已复制");
  } catch (error) {
    toast(error.message, "error");
  }
});

document.querySelectorAll("[data-period]").forEach((button) => {
  button.addEventListener("click", () => {
    state.period = button.dataset.period;
    document.querySelectorAll("[data-period]").forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    renderChart();
  });
});

document.querySelectorAll("[data-metric]").forEach((button) => {
  button.addEventListener("click", () => {
    state.metric = button.dataset.metric;
    document.querySelectorAll("[data-metric]").forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    renderChart();
  });
});

(async function boot() {
  try {
    const data = await api("api/session");
    renderDashboard(data);
    switchPage(pageFromHash());
    refreshUsage();
  } catch (_) {
    showAuth();
  }
})();

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshUsage();
    loadResourceStatus();
  }
});

["click", "keydown", "pointerdown"].forEach((eventName) => {
  document.addEventListener(eventName, () => {
    if (!document.hidden) touchSession();
  });
});
