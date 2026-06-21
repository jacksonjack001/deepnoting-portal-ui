(() => {
  const LABELS = {
    bash: "Bash",
    batch: "CMD",
    json: "JSON",
    javascript: "JavaScript",
    powershell: "PowerShell",
    python: "Python",
    text: "Text",
    toml: "TOML",
  };

  const ALIASES = {
    bat: "batch",
    cmd: "batch",
    js: "javascript",
    ps1: "powershell",
    shell: "bash",
    sh: "bash",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function span(type, value) {
    return `<span class="tok-${type}">${value}</span>`;
  }

  function createStash() {
    const stored = [];
    return {
      save(html, pattern, formatter) {
        return html.replace(pattern, (...args) => {
          const match = args[0];
          const offset = args[args.length - 2];
          const replacement =
            typeof formatter === "function" ? formatter(match, offset, html, args) : span(formatter, match);
          const token = `\uE000${String.fromCharCode(0xe100 + stored.length)}\uE001`;
          stored.push(replacement);
          return token;
        });
      },
      restore(html) {
        return html.replace(/\uE000([\uE100-\uF8FF])\uE001/g, (_, marker) => {
          const index = marker.charCodeAt(0) - 0xe100;
          return stored[index] ?? "";
        });
      },
    };
  }

  function normalizeLanguage(value) {
    const lang = String(value || "").toLowerCase().replace(/^language-/, "").replace(/^lang-/, "");
    return ALIASES[lang] || lang;
  }

  function explicitLanguage(code) {
    if (code.dataset.lang) return normalizeLanguage(code.dataset.lang);
    for (const className of code.classList) {
      const lang = normalizeLanguage(className);
      if (LABELS[lang]) return lang;
    }
    return "";
  }

  function headingHint(pre) {
    let node = pre.previousElementSibling;
    while (node) {
      if (/^H[1-4]$/.test(node.tagName)) return node.textContent || "";
      node = node.previousElementSibling;
    }
    return pre.parentElement?.querySelector("h1,h2,h3,h4")?.textContent || "";
  }

  function inferLanguage(text, hint = "") {
    const source = text.trim();
    const lower = `${hint}\n${source}`.toLowerCase();
    if (!source) return "text";
    if (/^https?:\/\/\S+$/.test(source)) return "text";
    if (lower.includes("config.toml") || /^model_provider\s*=/.test(source) || /\n\[[\w.-]+\]/.test(source)) {
      return "toml";
    }
    if (lower.includes("powershell") || source.includes("[Environment]::") || source.includes("Invoke-RestMethod")) {
      return "powershell";
    }
    if (lower.includes("windows cmd") || /^::/m.test(source) || /\bsetx\s+/i.test(source)) return "batch";
    if (
      source.startsWith("#!/usr/bin/env python") ||
      /\bfrom\s+openai\s+import\b/.test(source) ||
      /\bimport\s+(json|os|sys|urllib|openai)\b/.test(source) ||
      /\bdef\s+\w+\s*\(/.test(source)
    ) {
      return "python";
    }
    if (
      /\bimport\s+OpenAI\s+from\b/.test(source) ||
      /\bconst\s+\w+/.test(source) ||
      /\bawait\s+\w+/.test(source) ||
      /\bconsole\.log\b/.test(source)
    ) {
      return "javascript";
    }
    if (/^[\[{]/.test(source)) return "json";
    if (
      /\bcurl\s+/.test(source) ||
      /\bexport\s+\w+=/.test(source) ||
      /\bmkdir\s+-p\b/.test(source) ||
      /\bchmod\s+/.test(source) ||
      /\b(npm|brew|winget|codex|claude|python3)\b/.test(source)
    ) {
      return "bash";
    }
    return "text";
  }

  function highlightJson(text) {
    let html = escapeHtml(text);
    const stash = createStash();
    html = stash.save(html, /&quot;[^\n]*?&quot;(?=\s*:)/g, "key");
    html = stash.save(html, /&quot;[^\n]*?&quot;/g, "string");
    html = html.replace(/\b(true|false|null)\b/g, (match) => span("literal", match));
    html = html.replace(/-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g, (match) => span("number", match));
    return stash.restore(html);
  }

  function highlightToml(text) {
    let html = escapeHtml(text);
    const stash = createStash();
    html = stash.save(html, /&quot;[^\n]*?&quot;|&#039;[^\n]*?&#039;/g, "string");
    html = html.replace(/(^|\s)(#.*)$/gm, (_, lead, comment) => `${lead}${span("comment", comment)}`);
    html = html.replace(/^(\s*)(\[[^\]\n]+\])/gm, (_, lead, section) => `${lead}${span("section", section)}`);
    html = html.replace(/^(\s*)([A-Za-z0-9_.-]+)(\s*=)/gm, (_, lead, key, eq) => `${lead}${span("key", key)}${eq}`);
    html = html.replace(/\b(true|false)\b/g, (match) => span("literal", match));
    html = html.replace(/-?\b\d+(?:\.\d+)?\b/g, (match) => span("number", match));
    return stash.restore(html);
  }

  function highlightShell(text) {
    let html = escapeHtml(text);
    const stash = createStash();
    html = stash.save(html, /&quot;[^\n]*?&quot;|&#039;[^\n]*?&#039;/g, "string");
    html = html.replace(/(^|\s)(#.*)$/gm, (_, lead, comment) => `${lead}${span("comment", comment)}`);
    html = html.replace(/(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)/g, (match) => span("variable", match));
    html = html.replace(/(^|\s)(--?[A-Za-z0-9][A-Za-z0-9_-]*)/g, (_, lead, flag) => `${lead}${span("flag", flag)}`);
    html = html.replace(
      /\b(curl|export|mkdir|nano|chmod|python3|codex|claude|npm|brew|winget|powershell|irm|iex|notepad|del|setx)\b/g,
      (match) => span("keyword", match),
    );
    return stash.restore(html);
  }

  function highlightPowerShell(text) {
    let html = escapeHtml(text);
    const stash = createStash();
    html = stash.save(html, /&quot;[^\n]*?&quot;|&#039;[^\n]*?&#039;/g, "string");
    html = html.replace(/(^|\s)(#.*)$/gm, (_, lead, comment) => `${lead}${span("comment", comment)}`);
    html = html.replace(/(\$env:[A-Za-z_][A-Za-z0-9_]*|\$[A-Za-z_][A-Za-z0-9_]*)/g, (match) => span("variable", match));
    html = html.replace(/(\[[A-Za-z.]+\])/g, (match) => span("type", match));
    html = html.replace(/\b([A-Z][A-Za-z]+-[A-Za-z]+|Invoke-RestMethod|ConvertTo-Json)\b/g, (match) =>
      span("keyword", match),
    );
    html = html.replace(/(^|\s)(-[A-Za-z][A-Za-z0-9]*)/g, (_, lead, flag) => `${lead}${span("flag", flag)}`);
    return stash.restore(html);
  }

  function highlightBatch(text) {
    let html = escapeHtml(text);
    const stash = createStash();
    html = stash.save(html, /&quot;[^\n]*?&quot;/g, "string");
    html = html.replace(/^(\s*)(::.*|REM\b.*)$/gim, (_, lead, comment) => `${lead}${span("comment", comment)}`);
    html = html.replace(/(%[A-Za-z_][A-Za-z0-9_]*%)/g, (match) => span("variable", match));
    html = html.replace(/\b(setx|curl|del|cmd|powershell|install)\b/gi, (match) => span("keyword", match));
    return stash.restore(html);
  }

  function highlightPython(text) {
    let html = escapeHtml(text);
    const stash = createStash();
    html = stash.save(
      html,
      /(?:[rubf]{0,2})?(&quot;&quot;&quot;[\s\S]*?&quot;&quot;&quot;|&#039;&#039;&#039;[\s\S]*?&#039;&#039;&#039;|&quot;[^\n]*?&quot;|&#039;[^\n]*?&#039;)/gi,
      "string",
    );
    html = html.replace(/(^|\s)(#.*)$/gm, (_, lead, comment) => `${lead}${span("comment", comment)}`);
    html = html.replace(/^(\s*)(@\w+)/gm, (_, lead, decorator) => `${lead}${span("decorator", decorator)}`);
    html = html.replace(
      /\b(and|as|assert|async|await|break|class|continue|def|elif|else|except|False|finally|for|from|if|import|in|is|lambda|None|not|or|pass|raise|return|True|try|while|with|yield)\b/g,
      (match) => span("keyword", match),
    );
    html = html.replace(/\b(print|len|range|int|str|dict|list|max|min|open|json|Exception|ValueError)\b/g, (match) =>
      span("builtin", match),
    );
    html = html.replace(/-?\b\d+(?:\.\d+)?\b/g, (match) => span("number", match));
    return stash.restore(html);
  }

  function highlightJavaScript(text) {
    let html = escapeHtml(text);
    const stash = createStash();
    html = stash.save(html, /`(?:\\.|[^`\\])*`|&quot;[^\n]*?&quot;|&#039;[^\n]*?&#039;/g, "string");
    html = html.replace(/(\/\/.*$|\/\*[\s\S]*?\*\/)/gm, (match) => span("comment", match));
    html = html.replace(
      /\b(await|async|break|case|catch|class|const|continue|default|else|export|false|finally|for|from|function|if|import|let|new|null|return|switch|throw|true|try|undefined|while)\b/g,
      (match) => span("keyword", match),
    );
    html = html.replace(/\b(console|process|OpenAI|JSON|Promise)\b/g, (match) => span("builtin", match));
    html = html.replace(/-?\b\d+(?:\.\d+)?\b/g, (match) => span("number", match));
    return stash.restore(html);
  }

  function highlight(text, language) {
    if (language === "json") return highlightJson(text);
    if (language === "toml") return highlightToml(text);
    if (language === "powershell") return highlightPowerShell(text);
    if (language === "batch") return highlightBatch(text);
    if (language === "python") return highlightPython(text);
    if (language === "javascript") return highlightJavaScript(text);
    if (language === "bash") return highlightShell(text);
    return escapeHtml(text);
  }

  function fallbackCopy(text) {
    if (!document.createElement || !document.body) return false;
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.top = "0";
    document.body.appendChild(textarea);
    textarea.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_) {
      ok = false;
    }
    textarea.remove();
    return ok;
  }

  async function copyText(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    return fallbackCopy(text);
  }

  function ensureCopyButton(pre, code) {
    if (!document.createElement) return;
    const existing = Array.from(pre.children).find((child) => child.classList?.contains("code-copy"));
    if (existing) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "code-copy";
    button.textContent = "复制";
    button.setAttribute("aria-label", "复制代码");
    button.addEventListener("click", async () => {
      const originalLabel = button.textContent;
      try {
        const copied = await copyText(code.textContent || "");
        button.textContent = copied ? "已复制" : "复制失败";
        button.classList.toggle("copied", Boolean(copied));
        button.classList.toggle("failed", !copied);
      } catch (_) {
        button.textContent = "复制失败";
        button.classList.add("failed");
      }
      setTimeout(() => {
        button.textContent = originalLabel || "复制";
        button.classList.remove("copied", "failed");
      }, 1400);
    });
    pre.appendChild(button);
  }

  function highlightCodeBlocks(root = document) {
    const blocks = root.querySelectorAll("pre > code:not([data-highlighted])");
    blocks.forEach((code) => {
      const pre = code.closest("pre");
      const text = code.textContent || "";
      const language = explicitLanguage(code) || inferLanguage(text, headingHint(pre));
      code.innerHTML = highlight(text, language);
      code.dataset.highlighted = "true";
      code.classList.add(`language-${language}`);
      pre.classList.add("code-block");
      pre.dataset.lang = LABELS[language] || "Code";
      ensureCopyButton(pre, code);
    });
  }

  window.highlightCodeBlocks = highlightCodeBlocks;
  document.addEventListener("DOMContentLoaded", () => highlightCodeBlocks(document));
})();
