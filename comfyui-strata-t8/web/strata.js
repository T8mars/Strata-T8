import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const defaults = {mode:"external",url:"http://127.0.0.1:8080",allow_lifecycle:false,same_gpu:false,
    runtime:"",data_dir:"",port:8082,vision:"gpu",context:32768,timeout_s:1800,cleanup_timeout_s:90,
    min_free_vram_mib:12288,min_free_ram_gib:60};
async function request(path, body) {
    const response = await api.fetchApi(path, body ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)} : {});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
}
function renderPanel(container) {
    container.replaceChildren();
    container.style.cssText="padding:14px;overflow:auto;height:100%;box-sizing:border-box;font-size:13px;line-height:1.5";
    const title=document.createElement("h3"); title.textContent="Strata-T8"; container.append(title);
    const note=document.createElement("p"); note.textContent="连接配置保存在本机。工作流只保存配置名称。托管模式使用独立运行包；每次推理结束后自动释放显存。"; container.append(note);
    const select=document.createElement("select"); const refresh=document.createElement("button"); refresh.textContent="刷新配置";
    select.setAttribute("aria-label","已有配置");
    const name=document.createElement("input"); name.value="default"; name.placeholder="配置名称";
    name.setAttribute("aria-label","配置名称");
    const fields=document.createElement("textarea"); fields.rows=16; fields.style.cssText="width:100%;box-sizing:border-box;font-family:monospace";
    fields.value=JSON.stringify(defaults,null,2);
    fields.setAttribute("aria-label","连接配置 JSON");
    const key=document.createElement("input"); key.type="password"; key.placeholder="API key：留空保留；新托管配置会自动生成"; key.autocomplete="off";
    key.setAttribute("aria-label","API key");
    const status=document.createElement("pre"); status.style.cssText="white-space:pre-wrap;overflow-wrap:anywhere";
    status.style.fontSize="12px";
    for(const input of [name,fields,key]) input.style.cssText+=";display:block;width:100%;box-sizing:border-box;margin:8px 0;padding:6px;border:1px solid #666;border-radius:4px;background:var(--comfy-input-bg,#333);color:var(--input-text,#eee)";
    let saved={};
    const report=(error)=>{status.textContent=error.message || String(error);};
    async function reload() {
        try { const data=await request("/strata_t8/profiles"); saved=data.profiles; select.replaceChildren();
            for (const profile of Object.keys(saved)) {const option=document.createElement("option");option.value=profile;option.textContent=profile;select.append(option);}
            if(saved[name.value]) select.value=name.value;
            if(select.value) select.onchange();
            status.textContent=`本机配置目录：${data.home}\n保存后刷新页面，再选择 Strata 连接配置。`;
        } catch(error) {report(error);}
    }
    select.onchange=()=>{name.value=select.value;const value={...saved[select.value]};delete value.key_configured;fields.value=JSON.stringify(value,null,2);key.value="";};
    refresh.onclick=reload;
    container.append(select,refresh,name,fields,key);
    const save=document.createElement("button"); save.textContent="保存配置";
    save.onclick=async()=>{try {const profile=JSON.parse(fields.value);profile.api_key=key.value || "__KEEP__";await request("/strata_t8/profile",{name:name.value,profile});key.value="";await reload();}catch(error){report(error);}};
    container.append(save);
    const actions=document.createElement("div");
    actions.style.cssText="display:flex;gap:6px;flex-wrap:wrap;margin:10px 0";
    for(const [action,label] of [["start","启动"],["status","状态"],["load","加载"],["unload","卸载"],["stop","停止托管服务"]]) {
        const button=document.createElement("button");button.textContent=label;
        button.onclick=async()=>{button.disabled=true;status.textContent="处理中…";try{
            if(action==="load") {
                status.textContent=JSON.stringify(await request("/prompt",{client_id:api.clientId,prompt:{"1":{class_type:"StrataT8Connection",inputs:{profile:name.value}},"2":{class_type:"StrataT8Control",inputs:{connection:["1",0],action:"load"}}}}),null,2)+"\n加载验证已进入 ComfyUI 队列；同卡模式返回前会释放显存。";
            } else status.textContent=JSON.stringify(await request("/strata_t8/control",{name:name.value,action}),null,2);
        }catch(error){report(error);}finally{button.disabled=false;}};
        actions.append(button);
    }
    container.append(actions,status); reload();
}
app.registerExtension({name:"Strata-T8.Panel",async setup(){
    if(app.extensionManager?.registerSidebarTab) {
        app.extensionManager.registerSidebarTab({id:"strata-t8",icon:"pi pi-eye",title:"Strata-T8",tooltip:"Strata 连接和服务状态",type:"custom",render:renderPanel});
    } else {
        const button=document.createElement("button");button.textContent="Strata-T8";
        button.onclick=()=>{const dialog=document.createElement("dialog");dialog.style.cssText="width:560px;height:80vh;background:#222;color:#eee";const close=document.createElement("button");close.textContent="关闭";close.onclick=()=>dialog.remove();dialog.append(close);const content=document.createElement("div");dialog.append(content);renderPanel(content);document.body.append(dialog);dialog.showModal();};
        app.ui.menuContainer.append(button);
    }
}});
