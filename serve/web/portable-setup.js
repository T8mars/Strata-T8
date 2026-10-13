"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="strata-setup-token"]').content;
let initialized = false, redirect = new URLSearchParams(location.search).get("enter") === "1", state = null, polling = false, choosing = false, preparedPath = null;
async function api(path, body) {
  const options = {headers: {"X-Strata-Setup-Token": token}, cache: "no-store"};
  if (body !== undefined) { options.method = "POST"; options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作未完成，请重试。");
  return result;
}
function fail(error) { $("error-detail").textContent = error.message || String(error); $("error-box").hidden = false; }
function clearError() { $("error-box").hidden = true; }
function markSelectedModel() {
  for (const button of $("candidates").querySelectorAll("button")) {
    const selected = button.dataset.path === $("model-path").value.trim();
    button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", String(selected));
  }
}
function localChatURL(value) {
  try { const u = new URL(value); if (u.protocol === "http:" && u.hostname === "127.0.0.1" && u.port && u.pathname === "/" && !u.username && !u.password && !u.search && !u.hash) return u.href; } catch (e) {}
  return null;
}
function render(current) {
  state = current;
  const first = !initialized;
  if (first) {
    const d = current.defaults;
    $("model-path").value = d.path || ""; $("port").value = d.port; $("context").value = String(d.context);
    $("backend").value = d.backend; $("vision").value = d.vision; $("native-video").checked = d.native_video;
    $("browse").hidden = !current.folder_picker;
    $("download-path").value = d.path || "";
    for (const option of $("vision").options) if (["gpu", "cpu"].includes(option.value)) option.disabled = !current.vision_available;
    initialized = true;
  }
  const busy = ["preparing", "configuring", "starting", "stopping"].includes(current.job.state);
  const blocked = busy || choosing;
  for (const id of ["model-path", "browse", "check", "scan", "port", "context", "backend", "vision", "native-video", "save"]) $(id).disabled = blocked;
  for (const id of ["download-path", "download-consent", "prepare"]) $(id).disabled = blocked || current.running;
  for (const button of $("candidates").querySelectorAll("button")) button.disabled = blocked;
  $("running-notice").hidden = !current.running;
  $("save").textContent = current.running ? "保存并切换模型" : "保存并进入";
  markSelectedModel();
  $("start").hidden = !current.configured || current.running; $("start").disabled = busy;
  $("stop").hidden = !current.running; $("stop").disabled = busy;
  const badges = {idle: "待设置", preparing: "准备模型", prepared: "模型已准备", configuring: "保存配置", starting: "正在启动", stopping: "正在停止", ready: "已就绪", error: "需要处理"};
  $("status-badge").textContent = badges[current.job.state] || "待设置"; $("status-badge").dataset.state = current.job.state;
  $("status-message").textContent = current.job.message;
  $("current-model").textContent = current.model ? current.model.label + " · " + current.model.path : "尚未连接模型";
  const chat = current.job.state === "ready" ? localChatURL(current.chat_url) : null;
  $("enter-chat").hidden = !chat; if (chat) $("enter-chat").href = chat;
  if (current.job.error) fail(current.job.error);
  $("log-box").hidden = !current.log_tail; $("log-detail").textContent = current.log_tail || "";
  if (current.prepared_model && current.prepared_model.path !== preparedPath) {
    preparedPath = current.prepared_model.path; $("model-path").value = preparedPath; $("check-result").hidden = false;
    $("check-result").textContent = "模型已准备好，可保存设置并进入。";
  }
  if (first && redirect && current.configured && !current.running && current.job.state === "idle") {
    api("/api/start", {}).catch(e => { redirect = false; fail(e); });
  }
  if (chat && redirect) { redirect = false; window.location.assign(chat); }
}
async function poll() {
  if (polling) return;
  polling = true;
  try { const current = await api("/api/status"); render(current); } catch (e) { fail(e); }
  finally { polling = false; }
  setTimeout(poll, state && ["preparing", "configuring", "starting", "stopping"].includes(state.job.state) ? 600 : 2500);
}
async function scan() {
  clearError();
  try {
    const result = await api("/api/models"); const container = $("candidates"); container.replaceChildren(); container.hidden = false;
    if (!result.models.length) { container.textContent = "未在程序附近发现完整模型，请选择模型所在文件夹。"; return; }
    for (const model of result.models) {
      const button = document.createElement("button"); button.type = "button"; button.className = "candidate";
      button.dataset.path = model.path;
      const title = document.createElement("strong"), path = document.createElement("span");
      title.textContent = model.label + (model.bytes ? " · " + (model.bytes / 1e9).toFixed(1) + " GB" : ""); path.textContent = model.path;
      button.append(title, path); button.onclick = () => { if (choosing || (state && ["preparing", "configuring", "starting", "stopping"].includes(state.job.state))) return; $("model-path").value = model.path; markSelectedModel(); check(); };
      container.append(button);
    }
    markSelectedModel();
  } catch (e) { fail(e); }
}
async function check() {
  clearError();
  const requested = $("model-path").value.trim();
  try { const result = await api("/api/check", {path: requested}); if ($("model-path").value.trim() !== requested) return; $("model-path").value = result.model.path; markSelectedModel(); $("check-result").textContent = "检查通过 · " + result.model.label + " · 模型与辅助文件完整"; $("check-result").hidden = false; }
  catch (e) { if ($("model-path").value.trim() !== requested) return; $("check-result").hidden = true; fail(e); }
}
$("model-path").oninput = () => { $("check-result").hidden = true; markSelectedModel(); clearError(); };
$("scan").onclick = scan; $("check").onclick = check;
$("browse").onclick = async () => { clearError(); choosing = true; if (state) render(state); try { const result = await api("/api/browse", {}); if (result.path) { $("model-path").value = result.path; await check(); } } catch (e) { fail(e); } finally { choosing = false; if (state) render(state); } };
$("backend").onchange = () => { if ($("backend").value === "hip") { $("vision").value = "no"; $("native-video").checked = false; } };
$("vision").onchange = () => { if ($("vision").value === "no") $("native-video").checked = false; };
$("model-form").onsubmit = async e => {
  e.preventDefault(); clearError(); redirect = true; $("save").disabled = true;
  try { await api("/api/import", {path: $("model-path").value.trim(), port: Number($("port").value), context: Number($("context").value), vision: $("vision").value, backend: $("backend").value, native_video: $("native-video").checked}); }
  catch (error) { redirect = false; fail(error); $("save").disabled = false; }
};
$("start").onclick = async () => { clearError(); redirect = true; try { await api("/api/start", {}); } catch (e) { redirect = false; fail(e); } };
$("stop").onclick = async () => { clearError(); redirect = false; try { await api("/api/stop", {}); } catch (e) { fail(e); } };
$("prepare").onclick = async () => { clearError(); redirect = false; try { await api("/api/prepare", {path: $("download-path").value.trim(), accept_download: $("download-consent").checked}); } catch (e) { fail(e); } };
poll();
scan();
