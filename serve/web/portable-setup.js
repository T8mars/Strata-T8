"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="strata-setup-token"]').content;
let initialized = false, redirect = new URLSearchParams(location.search).get("enter") === "1", state = null, polling = false, choosing = false, preparedPath = null;
let updateStartedAt = 0;
async function api(path, body) {
  const options = {headers: {"X-Strata-Setup-Token": token}, cache: "no-store"};
  if (body !== undefined) { options.method = "POST"; options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  const controller = ['/api/status', '/api/identity'].includes(path) ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), 5000) : null;
  if (controller) options.signal = controller.signal;
  try {
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "操作未完成，请重试。");
    return result;
  } finally { if (timer !== null) clearTimeout(timer); }
}
function fail(error) { $("error-detail").textContent = error.message || String(error); $("error-box").hidden = false; }
function clearError() { $("error-box").hidden = true; }
function markSelectedModel() {
  for (const card of $("candidates").querySelectorAll(".candidate")) {
    const selected = card.dataset.path === $("model-path").value.trim();
    card.classList.toggle("selected", selected); card.querySelector('.candidate-select').setAttribute("aria-pressed", String(selected));
  }
}
function renderModelIds() {
  for (const card of $('candidates').querySelectorAll('.candidate')) {
    const live = !$('api-connection').hidden && state?.model?.path?.toLowerCase() === card.dataset.path.toLowerCase();
    const field = card.querySelector('.candidate-api-id');
    if (!field) continue;
    const value = live ? $('api-model-id').value : card.dataset.apiModelId;
    if (field.value !== value) { card.querySelector('.candidate-copy-status').textContent = ''; field.value = value; }
    field.title = value;
    card.querySelector('.candidate-api-label').textContent = live ? '当前 API 模型 ID' : '配置后的 API 模型 ID';
  }
}
function localChatURL(value) {
  try { const u = new URL(value); if (u.protocol === "http:" && u.hostname === "127.0.0.1" && u.port && u.pathname === "/" && !u.username && !u.password && !u.search && !u.hash) return u.href; } catch (e) {}
  return null;
}
function renderApiConnection(current, chat) {
  const info = current.api_connection;
  const model = info?.model_id;
  const base = chat ? chat + 'v1' : null;
  const visible = !!(current.running && !choosing && base && info?.base_url === base &&
    typeof model === 'string' && model.trim() && model.length <= 200 && !/[\x00-\x1f\x7f]/.test(model));
  const nextModel = visible ? model : '';
  if ($('api-model-id').value !== nextModel || $('api-base-url').value !== (visible ? base : '')) $('api-copy-status').textContent = '';
  $('api-connection').hidden = !visible;
  if ($('api-model-id').value !== nextModel) $('api-model-id').value = nextModel;
  if ($('api-base-url').value !== (visible ? base : '')) $('api-base-url').value = visible ? base : '';
  $('copy-api-model').disabled = !visible; $('copy-api-base').disabled = !visible;
  $('api-key-hint').textContent = visible ? (info.api_key_required ? 'API Key：填写你为此服务设置的密钥。' : '当前服务未启用 API Key；客户端要求填写时，可填本地占位值 strata。') : '';
  $('api-models-link').hidden = !visible;
  if (visible) $('api-models-link').href = base + '/models'; else $('api-models-link').removeAttribute('href');
}
async function copyModelField(field, status, available = () => true) {
  const value = field.value;
  if (!available() || !value) return;
  try {
    if (!globalThis.navigator?.clipboard?.writeText) throw new Error('clipboard unavailable');
    await navigator.clipboard.writeText(value);
    if (available() && field.value === value) status.textContent = '已复制。';
  } catch (e) {
    if (available() && field.value === value) {
      field.focus(); field.select();
      let copied = false;
      try { copied = !!document.execCommand?.('copy'); } catch (e) {}
      status.textContent = copied ? '已复制。' : '请按 Ctrl+C 复制已选中的内容。';
    }
  }
}
async function copyApiValue(id) {
  await copyModelField($(id), $('api-copy-status'), () => !$('api-connection').hidden);
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
  const busy = ["preparing", "configuring", "starting", "stopping", "updating"].includes(current.job.state);
  const blocked = busy || choosing;
  for (const id of ["model-path", "browse", "check", "scan", "port", "context", "backend", "vision", "native-video", "save"]) $(id).disabled = blocked;
  for (const id of ["download-path", "download-consent", "prepare"]) $(id).disabled = blocked || current.running;
  for (const button of $("candidates").querySelectorAll(".candidate-select")) button.disabled = blocked;
  $("running-notice").hidden = !current.running;
  $("save").textContent = current.running ? "保存并切换模型" : "保存并进入";
  markSelectedModel();
  $("start").hidden = !current.configured || current.running; $("start").disabled = busy;
  $("stop").hidden = !current.running; $("stop").disabled = busy;
  const badges = {idle: "待设置", preparing: "准备模型", prepared: "模型已准备", configuring: "保存配置", starting: "正在启动", stopping: "正在停止", updating: "正在更新", ready: "已就绪", error: "需要处理"};
  $("status-badge").textContent = badges[current.job.state] || "待设置"; $("status-badge").dataset.state = current.job.state;
  $("status-message").textContent = current.job.message;
  const update = current.update;
  $("update-message").textContent = current.job.state === 'updating' ? current.job.message :
    update ? update.message + (!update.supported ? ' 源码仓库请使用 Git 更新。' : '') : '重启此启动器后可检查版本更新。';
  $("update-check").disabled = busy || update?.state === 'checking' || !update;
  $("update-start").disabled = busy || !update?.supported || update.state !== 'ready' || !update.available;
  if (current.job.state === 'updating') { redirect = false; if (!updateStartedAt) updateStartedAt = Date.now(); }
  else updateStartedAt = 0;
  $("current-model").textContent = current.model ? current.model.label + " · " + current.model.path : "尚未连接模型";
  const chat = current.job.state === "ready" ? localChatURL(current.chat_url) : null;
  renderApiConnection(current, chat);
  renderModelIds();
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
  if (chat && redirect && !choosing) { redirect = false; window.location.assign(chat); }
}
async function poll() {
  if (polling) return;
  polling = true;
  try { const current = await api("/api/status"); render(current); }
  catch (e) {
    if (state?.job.state === 'updating') await reconnectAfterUpdate();
    else fail(e);
  }
  finally { polling = false; }
  setTimeout(poll, state && ["preparing", "configuring", "starting", "stopping"].includes(state.job.state) ? 600 : 2500);
}
async function reconnectAfterUpdate() {
  $('update-message').textContent = '更新时启动器会暂时断开。请查看更新窗口或 logs/update-helper.log；服务恢复后本页会自动刷新。';
  try {
    const identity = await api('/api/identity');
    const sameApp = identity.service === 'strata-portable-ui' && state.app_root && identity.app_root === state.app_root;
    const restarted = identity.instance_id && identity.instance_id !== state.instance_id;
    if (sameApp && (restarted || identity.version !== state.version)) { window.location.replace('/'); return; }
  } catch (_) { /* The helper is still working, or it failed before a restart. */ }
  if (updateStartedAt && Date.now() - updateStartedAt > 120000) {
    fail(new Error('尚未连接到更新后的启动器。下载较慢时请查看更新窗口和 logs/update-helper.log；若窗口已报错，请关闭后运行 UPDATE-PORTABLE.bat，或用 START-HERE.bat 重新打开设置页。'));
  }
}
async function scan() {
  clearError();
  try {
    const result = await api("/api/models"); const container = $("candidates"); container.replaceChildren(); container.hidden = false;
    if (!result.models.length) { container.textContent = "未在程序附近发现完整模型，请选择模型所在文件夹。"; return; }
    let index = 0;
    for (const model of result.models) {
      const card = document.createElement('div'); card.className = 'candidate'; card.dataset.path = model.path;
      const button = document.createElement("button"); button.type = "button"; button.className = "candidate-select";
      const title = document.createElement("strong"), path = document.createElement("span");
      title.textContent = model.label + (model.bytes ? " · " + (model.bytes / 1e9).toFixed(1) + " GB" : ""); path.textContent = model.path;
      button.append(title, path); card.append(button);
      if (model.api_model_id) {
        card.dataset.apiModelId = model.api_model_id;
        const label = document.createElement('label'), row = document.createElement('div'), field = document.createElement('input');
        const copy = document.createElement('button'), status = document.createElement('span');
        field.id = 'candidate-api-'+index++; field.type = 'text'; field.readOnly = true; field.spellcheck = false;
        field.className = 'candidate-api-id'; field.onfocus = field.onclick = () => field.select();
        label.className = 'candidate-api-label'; label.htmlFor = field.id;
        row.className = 'candidate-id-row'; copy.type = 'button'; copy.className = 'button secondary candidate-copy'; copy.textContent = '复制 ID';
        copy.setAttribute('aria-label', '复制 '+model.label+' 的 API 模型 ID');
        status.className = 'candidate-copy-status'; status.setAttribute('role', 'status');
        copy.onclick = () => copyModelField(field, status);
        row.append(field, copy); card.append(label, row, status);
      }
      button.disabled = choosing || !!(state && ['preparing', 'configuring', 'starting', 'stopping', 'updating'].includes(state.job.state));
      button.onclick = async () => {
        if (choosing || (state && ["preparing", "configuring", "starting", "stopping"].includes(state.job.state))) return;
        $("model-path").value = model.path; markSelectedModel();
        if (state?.running && state.model?.path?.toLowerCase() === model.path.toLowerCase()) return;
        await importSelectedModel();
      };
      container.append(card);
    }
    markSelectedModel();
    renderModelIds();
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
$("browse").onclick = async () => {
  clearError(); choosing = true; if (state) render(state); let selected = false;
  try { const result = await api('/api/browse', {}); if (result.path) { $("model-path").value = result.path; await check(); selected = $("error-box").hidden; } }
  catch (e) { fail(e); }
  finally { choosing = false; if (state) render(state); }
  if (selected) await importSelectedModel();
};
$("backend").onchange = () => { if ($("backend").value === "hip") { $("vision").value = "no"; $("native-video").checked = false; } };
$("vision").onchange = () => { if ($("vision").value === "no") $("native-video").checked = false; };
async function importSelectedModel() {
  if (choosing || !$("model-form").reportValidity()) return;
  const payload = {path: $("model-path").value.trim(), port: Number($("port").value), context: Number($("context").value), vision: $("vision").value, backend: $("backend").value, native_video: $("native-video").checked};
  clearError(); redirect = true; choosing = true; if (state) render(state);
  try {
    try { await api("/api/import", payload); }
    catch (error) {
      // Older local launchers validate the model, then require a separate stop.
      // Keep this transition automatic until that launcher is restarted.
      if (!String(error.message).includes('请先停止当前服务，再更换模型')) throw error;
      await api('/api/stop', {});
      const deadline = Date.now() + 35000;
      let stopped = false;
      while (Date.now() < deadline) {
        const current = await api('/api/status'); render(current);
        if (current.job.state === 'error') throw new Error(current.job.error || current.job.message);
        if (!current.running && current.job.state === 'idle') { stopped = true; break; }
        await new Promise(resolve => setTimeout(resolve, 500));
      }
      if (!stopped) throw new Error('停止当前模型超时，请查看服务状态后重试。');
      await api('/api/import', payload);
    }
    render(await api('/api/status'));
  } catch (error) { redirect = false; fail(error); }
  finally { choosing = false; if (state) render(state); }
}
$("model-form").onsubmit = async e => { e.preventDefault(); await importSelectedModel(); };
$("start").onclick = async () => { clearError(); redirect = true; try { await api("/api/start", {}); } catch (e) { redirect = false; fail(e); } };
$("stop").onclick = async () => { clearError(); redirect = false; try { await api("/api/stop", {}); } catch (e) { fail(e); } };
$("prepare").onclick = async () => { clearError(); redirect = false; try { await api("/api/prepare", {path: $("download-path").value.trim(), accept_download: $("download-consent").checked}); } catch (e) { fail(e); } };
$("update-check").onclick = async () => { clearError(); try { await api('/api/update/check', {}); render(await api('/api/status')); } catch (e) { fail(e); } };
$("update-start").onclick = async () => {
  clearError(); redirect = false; $("update-start").disabled = true;
  try {
    await api('/api/update/start', {});
    if (state) render({...state, job:{state:'updating',message:'正在确认更新程序是否就绪，随后会自动停止服务。进度见更新窗口和 logs/update-helper.log。'}});
  } catch (e) { fail(e); if(state) render(state); }
};
if ($('backup-list')) $('backup-list').onclick=async()=>{
  const list=$('backup-versions');list.replaceChildren();clearError();
  try {
    const result=await api('/api/update/backups',{});
    if(!result.backups.length){list.textContent='尚无成功升级后的备份。';return;}
    for(const backup of result.backups) {
      const row=document.createElement('p'),text=document.createElement('span'),button=document.createElement('button');
      text.textContent=`${backup.to_version} → ${backup.from_version} `;button.type='button';button.className='text-button';
      button.textContent=backup.available?'回退到此版本':'当前版本不匹配';button.disabled=!backup.available;
      button.onclick=async()=>{clearError();button.disabled=true;redirect=false;
        try{await api('/api/update/restore',{id:backup.id});if(state)render({...state,job:{state:'updating',message:'正在确认回退程序就绪，进度见更新窗口和 logs/update-helper.log。'}});}
        catch(error){fail(error);button.disabled=false;}};
      row.append(text,button);list.append(row);
    }
  }catch(error){fail(error);}
};
$('copy-api-model').onclick = () => copyApiValue('api-model-id');
$('copy-api-base').onclick = () => copyApiValue('api-base-url');
for (const id of ['api-model-id', 'api-base-url']) $(id).onclick = () => $(id).select();
const asrFields = ['model_dir','vad_dir','quantization','device','language','context','audio_track','channel','offset_ms'];
const asrElement = key => $('asr-'+key.replaceAll('_','-'));
function showAsrSettings(result) {
  for (const key of asrFields) asrElement(key).value = result.settings[key];
  $('asr-settings-status').textContent = result.capabilities.supported ? '语音组件与模型路径已就绪；视频处勾选后才转录。' : '尚需准备：'+result.capabilities.missing.join('、')+'。普通视频分析仍可使用。';
}
async function loadAsrSettings() {
  try { showAsrSettings(await api('/api/asr/settings')); }
  catch(e) { $('asr-settings-status').textContent = '语音设置尚不可用，请重启更新后的启动器。'; }
}
$('asr-form').onsubmit = async e => {
  e.preventDefault(); $('asr-save').disabled = true;
  const settings = Object.fromEntries(asrFields.map(key => [key, ['audio_track','offset_ms'].includes(key) ? Number(asrElement(key).value) : asrElement(key).value.trim()]));
  try { showAsrSettings(await api('/api/asr/settings',settings)); } catch(e) { $('asr-settings-status').textContent = e.message; }
  finally { $('asr-save').disabled = false; }
};
$('asr-browse').onclick = async () => {
  $('asr-browse').disabled = true;
  try { const result = await api('/api/browse',{}); if(result.path) asrElement('model_dir').value=result.path; }
  catch(e) { $('asr-settings-status').textContent = e.message; }
  finally { $('asr-browse').disabled=false; }
};
loadAsrSettings();
poll();
scan();
