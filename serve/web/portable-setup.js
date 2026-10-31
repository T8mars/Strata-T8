"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="strata-setup-token"]').content;
let initialized = false, redirect = new URLSearchParams(location.search).get("enter") === "1", state = null, polling = false, choosing = false, preparedPath = null;
let updateStartedAt = 0;
let selectedFamily = null, qwenSettings = null;
let confirmedPort = null;
async function api(path, body) {
  const options = {headers: {"X-Strata-Setup-Token": token}, cache: "no-store"};
  if (body !== undefined) { options.method = "POST"; options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  const statusRequest = ['/api/status', '/api/identity'].includes(path);
  const controller = statusRequest || ['/api/update/check','/api/update/start','/api/update/restore'].includes(path) ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), statusRequest ? 5000 : 15000) : null;
  if (controller) options.signal = controller.signal;
  try {
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok) { const error = new Error(result.error || "操作未完成，请重试。"); error.responseReceived = true; throw error; }
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
  const lan = visible && info.api_key_required ? (info.lan_base_urls || []).filter(value => validLanBase(value, new URL(base).port)) : [];
  $('lan-connection').hidden = !lan.length;
  if ($('lan-base-url').value !== (lan[0] || '')) $('api-copy-status').textContent = '';
  $('lan-base-url').value = lan[0] || '';
  $('lan-alt-addresses').textContent = lan.length > 1 ? '其他网卡地址：' + lan.slice(1).join('；') : '';
  $('copy-lan-base').disabled = !lan.length;
}
function validLanBase(value, port) {
  try {
    const u = new URL(value), a = u.hostname.split('.').map(Number);
    return u.protocol === 'http:' && u.port === port && u.pathname === '/v1' && !u.search && !u.hash && !u.username && !u.password &&
      /^\d+\.\d+\.\d+\.\d+$/.test(u.hostname) && a.every(x => x >= 0 && x <= 255) &&
      (a[0] === 10 || (a[0] === 192 && a[1] === 168) || (a[0] === 172 && a[1] >= 16 && a[1] <= 31));
  } catch(e) { return false; }
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
    confirmedPort = d.port;
    $("backend").value = d.backend; $("vision").value = d.vision; $("native-video").checked = d.native_video;
    $('deepseek-speed').value = d.speed || 'full';
    if (current.model) applyModelCapabilities(current.model);
    $("browse").hidden = !current.folder_picker;
    $("download-path").value = d.path || "";
    $('api-scope').value = current.network?.scope || 'local';
    for (const option of $("vision").options) if (["gpu", "cpu"].includes(option.value)) option.disabled = !current.vision_available;
    initialized = true;
  }
  if (current.running && current.job.state === 'ready') {
    const actualPort = current.defaults?.port;
    if (Number.isInteger(actualPort) && actualPort >= 1 && actualPort <= 65535) {
      // Follow a completed restart's port fallback, preserving unsaved edits.
      if (Number($("port").value) === confirmedPort) $("port").value = String(actualPort);
      confirmedPort = actualPort;
    }
  }
  const busy = ["preparing", "configuring", "starting", "stopping", "updating"].includes(current.job.state);
  const blocked = busy || choosing;
  for (const id of ['api-scope','network-key','generate-network-key','copy-network-key','clear-network-key','save-network']) $(id).disabled = blocked;
  $('clear-network-key').disabled = blocked || $('api-scope').value === 'lan';
  $('network-key-status').textContent = current.network?.api_key_configured ? '已有密钥；页面不回显。留空保留，填写新值则替换。' : '尚未设置密钥。局域网模式必须先设置。';
  for (const id of ["model-path", "browse", "check", "scan", "port", "context", "backend", "vision", "native-video", "deepseek-speed", "save"]) $(id).disabled = blocked;
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
  if ($('backup-list')) $('backup-list').disabled = busy;
  for (const button of $('backup-versions')?.querySelectorAll('.backup-restore') || []) button.disabled = busy || button.dataset.restoreAvailable !== 'true';
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
    $('update-message').textContent = `仍在等待更新后的启动器（${Math.floor((Date.now()-updateStartedAt)/1000)} 秒）。下载与校验可能持续几分钟，请查看更新窗口的进度或 logs/update-helper.log；窗口报错时可运行 UPDATE-PORTABLE.bat 重试。`;
  }
}
async function requestUpdate(path, body, message) {
  const previous = state;
  if (state) render({...state,job:{state:'updating',message}});
  try { await api(path,body); }
  catch(error) {
    if (error.responseReceived) { if(previous)render(previous); fail(error); return; }
    // The launcher can exit after accepting the request but before its reply arrives.
    // Keep reconnecting in that case; a lost reply is not proof of a failed update.
    try { const current=await api('/api/status');render(current);if(!['updating','error'].includes(current.job?.state))fail(error); }
    catch(_) { await reconnectAfterUpdate(); }
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
        $("model-path").value = model.path; applyModelCapabilities(model); markSelectedModel();
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
  try { const result = await api("/api/check", {path: requested}); if ($("model-path").value.trim() !== requested) return; $("model-path").value = result.model.path; applyModelCapabilities(result.model); markSelectedModel(); $("check-result").textContent = "检查通过 · " + result.model.label + (result.model.text_only ? " · 当前模型包支持文本；图片和视频请选择 Qwen" : " · 模型与辅助文件完整"); $("check-result").hidden = false; }
  catch (e) { if ($("model-path").value.trim() !== requested) return; $("check-result").hidden = true; fail(e); }
}
function applyModelCapabilities(model) {
  $('deepseek-options').hidden = model.family !== 'deepseek41';
  if (model.family === 'deepseek41') {
    if (selectedFamily && selectedFamily !== 'deepseek41') {
      qwenSettings = {context:$('context').value, vision:$('vision').value, native:$('native-video').checked, backend:$('backend').value};
    }
    $('context').value = String(Math.min(Number($('context').value) || 8192, 8192));
    $('vision').value = 'no'; $('native-video').checked = false; $('backend').value = 'cuda';
  } else if (selectedFamily === 'deepseek41') {
    const saved = qwenSettings || {context:'32768', vision:state?.vision_available ? 'auto' : 'no', native:false, backend:'auto'};
    $('context').value = saved.context; $('vision').value = saved.vision;
    $('native-video').checked = saved.native; $('backend').value = saved.backend;
  }
  selectedFamily = model.family || 'qwen'; // Older launchers omit the family for Qwen cards.
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
  clearError(); redirect = true; choosing = true; if (state) render(state);
  try {
    const checked = await api('/api/check', {path:$('model-path').value.trim()});
    applyModelCapabilities(checked.model);
    const payload = {path: checked.model.path, port: Number($("port").value), context: Number($("context").value), vision: $("vision").value, backend: $("backend").value, native_video: $("native-video").checked};
    if (checked.model.family === 'deepseek41') payload.speed = $('deepseek-speed').value;
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
    confirmedPort = payload.port;
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
  await requestUpdate('/api/update/start', {}, '正在确认更新程序是否就绪，随后会自动停止服务。进度见更新窗口和 logs/update-helper.log。');
};
if ($('backup-list')) $('backup-list').onclick=async()=>{
  const list=$('backup-versions');list.replaceChildren();clearError();
  try {
    const result=await api('/api/update/backups',{});
    if(!result.backups.length){list.textContent='尚无成功升级后的备份。';return;}
    for(const backup of result.backups) {
      const row=document.createElement('p'),text=document.createElement('span'),button=document.createElement('button');
      text.textContent=`${backup.to_version} → ${backup.from_version} `;button.type='button';button.className='text-button backup-restore';button.dataset.restoreAvailable=String(backup.available);
      button.textContent=backup.available?'回退到此版本':'当前版本不匹配';button.disabled=!backup.available;
      button.onclick=async()=>{clearError();button.disabled=true;redirect=false;
        await requestUpdate('/api/update/restore',{id:backup.id},'正在确认回退程序就绪，进度见更新窗口和 logs/update-helper.log。');};
      row.append(text,button);list.append(row);
    }
  }catch(error){fail(error);}
};
$('copy-api-model').onclick = () => copyApiValue('api-model-id');
$('copy-api-base').onclick = () => copyApiValue('api-base-url');
$('copy-lan-base').onclick = () => copyModelField($('lan-base-url'),$('api-copy-status'),()=>!$('lan-connection').hidden);
for (const id of ['api-model-id', 'api-base-url','lan-base-url']) $(id).onclick = () => $(id).select();
$('api-scope').onchange = () => {
  $('clear-network-key').disabled = $('api-scope').value === 'lan';
  if ($('api-scope').value === 'lan') $('clear-network-key').checked = false;
};
$('generate-network-key').onclick = () => {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  $('network-key').value = 'strata_' + Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
  $('clear-network-key').checked = false;
  $('network-save-status').textContent = '已生成新密钥；请先复制给客户端，再保存设置。';
};
$('copy-network-key').onclick = () => copyModelField($('network-key'),$('network-save-status'));
$('network-form').onsubmit = async e => {
  e.preventDefault();clearError();$('save-network').disabled=true;
  try {
    await api('/api/network',{scope:$('api-scope').value,api_key:$('network-key').value,clear_api_key:$('clear-network-key').checked});
    $('network-key').value='';$('clear-network-key').checked=false;
    $('network-save-status').textContent='正在应用访问设置；已运行的模型服务会自动重启。';
    render({...state,job:{state:'configuring',message:'正在应用 API 访问设置…'}});
  } catch(e) { fail(e);$('save-network').disabled=false; }
};
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
