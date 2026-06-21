const $ = (id) => document.getElementById(id);

function toast(message, type = "info") {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast show ${type}`;
  setTimeout(() => (el.className = "toast"), 3200);
}

$("resetForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = $("resetForm").dataset.token;
  if (!token) {
    toast("重置链接无效或已过期", "error");
    return;
  }
  try {
    const res = await fetch("../api/auth/reset", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token,
        password: $("newPassword").value,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "重置失败");
    window.location.href = "../";
  } catch (error) {
    toast(error.message, "error");
  }
});

