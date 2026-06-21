const $ = (id) => document.getElementById(id);

function toast(message, type = "info") {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast show ${type}`;
  setTimeout(() => (el.className = "toast"), 3200);
}

function token() {
  return $("adminToken").value.trim() || localStorage.getItem("portalAdminToken") || "";
}

async function adminApi(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": token(),
      ...(options.headers || {}),
    },
    ...options,
  });
  const data = (res.headers.get("content-type") || "").includes("application/json")
    ? await res.json()
    : await res.text();
  if (!res.ok) {
    const message = typeof data === "object" ? data.detail || "请求失败" : data || "请求失败";
    throw new Error(message);
  }
  return data;
}

async function loadCatalog() {
  if ($("adminToken").value.trim()) {
    localStorage.setItem("portalAdminToken", $("adminToken").value.trim());
  }
  const data = await adminApi("api/admin/catalog");
  $("catalogEditor").value = JSON.stringify(data.catalog, null, 2);
  toast("已加载配置");
}

async function saveCatalog() {
  const raw = $("catalogEditor").value.trim();
  const catalog = JSON.parse(raw);
  const data = await adminApi("api/admin/catalog", {
    method: "PUT",
    body: JSON.stringify({ catalog }),
  });
  $("catalogEditor").value = JSON.stringify(data.catalog, null, 2);
  toast("已保存配置");
}

async function syncCatalog() {
  const data = await adminApi("api/admin/catalog/sync", {
    method: "POST",
    body: "{}",
  });
  $("catalogEditor").value = JSON.stringify(data.catalog, null, 2);
  toast("已同步模型配置");
}

$("adminToken").value = localStorage.getItem("portalAdminToken") || "";
$("loadCatalog").addEventListener("click", () => loadCatalog().catch((error) => toast(error.message, "error")));
$("saveCatalog").addEventListener("click", () => saveCatalog().catch((error) => toast(error.message, "error")));
$("syncCatalog").addEventListener("click", () => syncCatalog().catch((error) => toast(error.message, "error")));

if ($("adminToken").value) {
  loadCatalog().catch(() => {});
}
