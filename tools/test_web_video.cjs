// Offline browser logic checks; no live model, browser state or GPU access.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const V = require('../serve/web/video-analysis.js');
const S = require('../serve/web/speech-analysis.js');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../serve/web/app.js'), 'utf8');
function sample(segment, duration = 12, mode = 'native') {
  const count = Math.min(segment.max_frames, Math.ceil((segment.end_s - segment.start_s) * segment.sampling_fps - 1e-9));
  const frames = Array.from({length:count}, (_, i) => ({index:i, timestamp_s:i / segment.sampling_fps,
    source_timestamp_s:5 + segment.start_s + i / segment.sampling_fps, source:'data:image/png;base64,fixture'}));
  return {part:{type:'strata_video', mode, frames, metadata:{time_origin_s:5 + segment.start_s}},
    manifest:{source:{duration_s:duration, time_origin_s:5}, sampling:{start_s:segment.start_s,end_s:segment.end_s,
      max_frames:segment.max_frames,sampling_fps:segment.sampling_fps}, coverage:{sample_count:count}}};
}
function element(value = '') {
  return {get value() { return String(this._value ?? value); }, set value(v) { this._value = v; },
    hidden:false, disabled:false, textContent:'', dataset:{}, options:[], children:[], checked:false,
    classList:{toggle(){}, add(){}, remove(){}}, setAttribute(){}, removeAttribute(key){delete this[key];}, focus(){this.focused=true;}, select(){this.selected=true;}, reportValidity(){return true;},
    querySelectorAll(selector){const found=[];for(const child of this.children){if(selector.startsWith('.') && (child.className||'').split(' ').includes(selector.slice(1)))found.push(child);found.push(...child.querySelectorAll(selector));}return found;},
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}, replaceChildren(){this.children=[];}, append(...v){this.children.push(...v);}};
}
function videoPage() {
  const fields = Object.fromEntries(Object.entries({'video-max-frames':8,'video-fps':1,'video-start':0,
    'video-duration':'','video-processing':'single','video-mode':'native',
    'video-asr-enabled':'','video-timed-transcript':''}).map(([k,v]) => [k,element(v)]));
  const calls = [], saved = {}, notices = [];
  const context = vm.createContext({StrataVideoAnalysis:V, StrataSpeech:S, AbortController, DOMException, console, setTimeout, clearTimeout,
    health:{images:true}, attachments:[], headers:()=>({}), toast:(...args)=>notices.push(args),
    store:{get:(k,d)=>d, set:(k,v)=>saved[k]=v},
    $:id=>fields[id] || (fields[id]=element()), renderAttachments(){},
    FileReader:class {readAsDataURL(){this.result='data:video/mp4;base64,fixture'; queueMicrotask(()=>this.onload());} abort(){}},
    fetch:async (url, options) => {
      const body = options.body ? JSON.parse(options.body) : null; calls.push({url,body});
      if(url==='v1/asr/capabilities')return {ok:true,json:async()=>({supported:true})};
      if(url==='v1/asr/status')return {ok:true,json:async()=>({phase:'completed'})};
      if(['v1/video/transcribe','v1/asr/transcript/parse'].includes(url))return {ok:true,json:async()=>({
        contract_version:1,timeline:'input',status:'complete',cues:[{id:'u1',start_s:1,end_s:2,text:'向右移动'}]})};
      if (url === 'v1/video/sample') {
        const segment = {...body, start_s:body.start_s || 0, end_s:Math.min(body.end_s ?? 12,12)};
        return {ok:true,json:async()=>sample(segment,12,body.mode)};
      }
      return {ok:true,json:async()=>({choices:[{finish_reason:'stop',message:{content:'动作证据'}}]})};
    },
  });
  // send() now uses the model-card sampling helper as well as the T8 video helpers.
  vm.runInContext(source.slice(source.indexOf('const DEFAULTS ='), source.indexOf('let settings =')), context);
  vm.runInContext(source.slice(source.indexOf('let videoFrameLimit ='), source.indexOf('function isTextFile(')), context);
  return {context, fields, calls, saved, notices, run:code=>vm.runInContext(code,context)};
}
function reusableVideoPage() {
  const ui=videoPage(), retained=[];
  ui.fields['video-source-reuse']=element();ui.fields['video-source-reuse'].checked=true;
  ui.context.StrataVideoTasks={token:()=> 'a'.repeat(64),call:async(path,value,_headers,signal)=>{
    retained.push({path,value,signal});return path==='v1/video/sources'?{id:'created-source'}:{deleted:true};
  }};
  const original=ui.context.fetch;
  ui.context.fetch=async(url,options)=>url==='v1/video/capabilities'?
    {ok:true,json:async()=>({source_handles:true})}:original(url,options);
  return {...ui,retained};
}
test('failed sampling deletes only the newly uploaded reusable source',async()=>{
  const ui=reusableVideoPage(), original=ui.context.fetch;
  ui.context.fetch=async(url,options)=>url==='v1/video/sample'?
    {ok:false,json:async()=>({error:{message:'fixture decode failed'}})}:original(url,options);
  await ui.run("addVideo({name:'broken.mp4',size:100})");
  assert.equal(ui.run('attachments.length'),0);
  assert.match(ui.run('videoUploadError'),/fixture decode failed/);
  assert.deepEqual(ui.retained.map(c=>c.path),['v1/video/sources','v1/video/sources/delete']);
  assert.equal(ui.retained[1].value.source_id,'created-source');
});
test('cancelled sampling also releases its newly uploaded source',async()=>{
  const ui=reusableVideoPage(), original=ui.context.fetch;
  ui.context.fetch=async(url,options)=>{
    if(url==='v1/video/sample'){
      ui.run('attachments[0].controller.abort()');
      throw new DOMException('Cancelled','AbortError');
    }
    return original(url,options);
  };
  await ui.run("addVideo({name:'clip.mp4',size:100})");
  assert.equal(ui.run('attachments.length'),0);
  assert.deepEqual(ui.retained.map(c=>c.path),['v1/video/sources','v1/video/sources/delete']);
  assert.notEqual(ui.retained[0].signal,ui.retained[1].signal);
  assert.equal(ui.retained[0].signal.aborted,true);
  assert.equal(ui.retained[1].signal.aborted,false);
});
test('a stalled cleanup is independently cancelled without hiding the sampling error',async()=>{
  const ui=reusableVideoPage(), original=ui.context.fetch, call=ui.context.StrataVideoTasks.call;
  ui.context.fetch=async(url,options)=>url==='v1/video/sample'?
    {ok:false,json:async()=>({error:{message:'original decode failure'}})}:original(url,options);
  ui.context.setTimeout=callback=>{queueMicrotask(callback);return 1;};
  ui.context.clearTimeout=()=>{};
  ui.context.StrataVideoTasks.call=async(path,value,headers,signal)=>{
    if(path==='v1/video/sources/delete')return new Promise((_resolve,reject)=>
      signal.addEventListener('abort',()=>reject(new DOMException('Cleanup deadline','AbortError')),{once:true}));
    return call(path,value,headers,signal);
  };
  await ui.run("addVideo({name:'broken.mp4',size:100})");
  assert.match(ui.run('videoUploadError'),/original decode failure/);
  assert.equal(ui.run('attachments.length'),0);
});
test('failed resampling preserves the previous attachment and its reused source',async()=>{
  const ui=reusableVideoPage();
  await ui.run("addVideo({name:'clip.mp4',size:100})");
  const old=ui.run('attachments[0]');
  ui.context.fetch=async()=>({ok:false,json:async()=>({error:{message:'fixture retry failed'}})});
  await ui.run('addVideo(attachments[0].file,attachments[0])');
  assert.equal(ui.run('attachments[0]'),old);
  assert.deepEqual(ui.retained.map(c=>c.path),['v1/video/sources']);
});
test('changing mode on an empty first segmented window does not dereference its absent part',()=>{
  const ui=videoPage();
  ui.context.attachments=[{kind:'video',part:null,plan:{segments:[]}}];
  ui.fields['video-mode'].addEventListener=(_event,callback)=>callback();
  assert.doesNotThrow(()=>vm.runInContext(source.slice(source.indexOf("$('video-mode').addEventListener"),
    source.indexOf('function addFiles(')),ui.context));
});
test('12 seconds at 24 fps plans 288 frames across 18 requests, without a 64-frame total cap', () => {
  const p = V.plan({source:{duration_s:12}}, {max_frames:16,sampling_fps:24});
  assert.equal(p.estimated_frames,288); assert.equal(p.segments.length,18);
  assert.equal(p.segments.at(-1).end_s,12);
  assert.throws(()=>V.plan({source:{duration_s:null}}, {max_frames:16,sampling_fps:24}));
  assert.throws(()=>V.plan({source:{duration_s:3600}}, {max_frames:16,sampling_fps:120}), /512/);
});
test('fractional windows and shorter files clamp the plan to actual duration', () => {
  const p = V.plan({source:{duration_s:2.1}}, {start_s:1.3,end_s:11.3,max_frames:16,sampling_fps:24});
  assert.equal(p.end_s,2.1); assert.equal(p.estimated_frames,20); assert.equal(p.segments.length,2);
  assert.equal(p.segments.at(-1).end_s,2.1);
});
test('sequential analysis observes all 288 frames and reports actual source-clock coverage', async () => {
  const p = V.plan({source:{duration_s:12}}, {max_frames:16,sampling_fps:24});
  const order = [], updates = [];
  const result = await V.run({plan:p,first:sample(p.segments[0]),
    sample:async s=>{order.push(`sample${s.index}`);return sample(s);},
    observe:async (r,s,previous)=>{order.push(`observe${s.index}`); if (s.index) assert.equal(previous.text,`N${s.index-1}`); return `N${s.index}`;},
    merge:async()=>{throw Error('Small notes require no merge');},progress:p=>updates.push(p)});
  assert.equal(order[0],'observe0'); assert.deepEqual(order.slice(1,3),['sample1','observe1']);
  assert.equal(result.coverage.sample_count,288); assert.equal(result.coverage.analysis_completed,true);
  assert.ok(Math.abs(result.coverage.max_sample_gap_s - 1/24)<1e-7);
  assert.equal(result.coverage.first_sample_s,0); assert.ok(result.coverage.last_sample_s>11.95);
  assert.match(result.evidence,/N17/); assert.equal(updates.at(-1).completed,18);
});
test('cancellation and sampling failure preserve partial completion without processing later segments', async () => {
  const p = V.plan({source:{duration_s:12}}, {max_frames:16,sampling_fps:24});
  for (const cancel of [true,false]) {
    const controller = new AbortController(); let latest, samples=0;
    await assert.rejects(V.run({plan:p,first:sample(p.segments[0]),signal:controller.signal,
      sample:async s=>{samples++; if (!cancel && s.index===2) throw Error('decode failed'); return sample(s);},
      observe:async()=> 'observed',merge:async()=> 'merged',progress:state=>{latest=state; if(cancel && state.completed===2)controller.abort();}}), cancel ? {name:'AbortError'} : /decode failed/);
    assert.equal(latest.coverage.sample_count,32); assert.equal(latest.completed,2);
    assert.equal(latest.coverage.analysis_completed,false); assert.equal(samples,cancel ? 1 : 2);
  }
});
test('hierarchical merge consumes every segment without exceeding its text budget', async () => {
  const p = V.plan({source:{duration_s:12}}, {max_frames:16,sampling_fps:24}); let merges=0;
  const result = await V.run({plan:p, first:sample(p.segments[0]),sample:async s=>sample(s),
    observe:async(r,s)=>`NOTE_${s.index} `+'x'.repeat(1400), merge:async records=>{merges++; assert.ok(records.length<=6000); return [...records.matchAll(/NOTE_\d+/g)].map(m=>m[0]).join(' ');}});
  assert.ok(merges>1); for (let i=0;i<18;i++) assert.ok(result.evidence.includes(`NOTE_${i}`));
  assert.equal(result.notes.length,18); assert.ok(result.evidence.length<=6000);
});
test('mismatched samples cannot be labelled as completing the selected window', async () => {
  const p=V.plan({source:{duration_s:12}},{max_frames:16,sampling_fps:24});
  const wrong=sample({...p.segments[0],end_s:1});
  await assert.rejects(V.run({plan:p,first:wrong, sample:async()=>wrong,observe:async()=> 'evidence'}), /分段计划不一致/);
});
test('CPU pixel measurements constrain color and region geometry only on qualified uniform frames',()=>{
  function pixels(center){
    const out=new Uint8ClampedArray(128*72*4);
    for(let y=0;y<72;y++)for(let x=0;x<128;x++){
      const i=(y*128+x)*4;out[i]=220;out[i+3]=255;
      if((x-center)**2+(y-36)**2<=64)out[i]=out[i+1]=out[i+2]=255;
    }return out;
  }
  const a=V.pixelMeasurements(pixels(20),128,72),b=V.pixelMeasurements(pixels(100),128,72);
  assert.deepEqual(a.dominant_uniform_rgb,[220,0,0]);
  assert.ok(a.foreground_region_center_xy[0]<b.foreground_region_center_xy[0]);
  assert.equal(a.foreground_region_center_xy[1],b.foreground_region_center_xy[1]);
  const texture=new Uint8ClampedArray(128*72*4);
  for(let i=0;i<128*72;i++){texture[i*4]=(i*37)%256;texture[i*4+1]=(i*71)%256;texture[i*4+2]=(i*103)%256;texture[i*4+3]=255;}
  assert.deepEqual(V.pixelMeasurements(texture,128,72),{});
});
test('browser bitmap measurement reaches the actual observation and closes every bitmap',async()=>{
  const pixels=new Uint8ClampedArray(16*16*4);
  for(let i=0;i<256;i++){pixels[i*4]=220;pixels[i*4+3]=255;}
  let closed=0;
  const context=vm.createContext({module:{exports:{}},AbortController,DOMException,
    fetch:async()=>({blob:async()=>({})}),
    createImageBitmap:async()=>({width:16,height:16,close(){closed++;}}),
    OffscreenCanvas:class {getContext(){return {drawImage(){},getImageData(){return {data:pixels};}};}}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../serve/web/video-analysis.js'),'utf8'),context);
  const browser=context.module.exports,p=browser.plan({source:{duration_s:1}},{max_frames:2,sampling_fps:4});
  await browser.run({plan:p,first:sample(p.segments[0],1),sample:async s=>sample(s,1),
    observe:async sampled=>{assert.equal(sampled.pixel_measurements.length,2);assert.equal(JSON.stringify(sampled.pixel_measurements[0].pixel_measurements.dominant_uniform_rgb),'[220,0,0]');return 'observed';},
    merge:async()=> 'summary'});
  assert.equal(closed,4);
});
test('UI defaults retain the original 8 frames / 1 fps single request', () => {
  const ui=videoPage(); assert.equal(JSON.stringify(ui.run('videoSamplingOptions()')),JSON.stringify({max_frames:8,sampling_fps:1}));
  const html=fs.readFileSync(path.join(__dirname,'../serve/web/index.html'),'utf8');
  assert.match(html,/<details><summary>视频分析设置<\/summary>/);
  assert.ok(!html.includes('id="video-end"')); assert.ok(html.includes('id="video-duration"'));
});
test('entering 10 seconds at 24 fps calculates 240 frames and uploads only the first bounded segment', async () => {
  const ui=videoPage(); ui.fields['video-duration'].value=10; ui.fields['video-fps'].value=24;
  ui.fields['video-duration'].onchange();
  assert.equal(ui.run('videoSamplingOptions().end_s'),10); assert.equal(ui.run('videoSamplingOptions().segmented'),true);
  await ui.run('addVideo({name:"clip.mp4",size:1000})');
  assert.equal(ui.calls.length,1); assert.equal(ui.calls[0].body.max_frames,16); assert.equal(ui.calls[0].body.end_s,16/24);
  assert.equal(ui.run('attachments[0].plan.estimated_frames'),240); assert.equal(ui.run('attachments[0].plan.segments.length'),15);
  const result=await ui.run('analyzeVideoSegments(attachments[0],"分析动作",{model:"fixture"},new AbortController().signal,()=>{})');
  assert.equal(result.coverage.sample_count,240);
  assert.equal(ui.calls.filter(c=>c.url==='v1/chat/completions').length,15);
  assert.ok(ui.calls.filter(c=>c.url==='v1/video/sample').every(c=>c.body.max_frames===16));
});
test('invalid upload displays a persistent error, and resampling replaces the existing attachment', async () => {
  const ui=videoPage(); ui.fields['video-fps'].value=0;
  await ui.run('addVideo({name:"clip.mp4",size:1000})'); assert.equal(ui.calls.length,0);
  assert.match(ui.run('videoUploadError'),/FPS/);
  ui.fields['video-fps'].value=24; ui.fields['video-fps'].onchange();
  await ui.run('addVideo({name:"clip.mp4",size:1000})'); assert.equal(ui.run('videoUploadError'),'');
  ui.fields['video-duration'].value=10; ui.fields['video-duration'].onchange();
  await ui.run('addVideo(attachments[0].file, attachments[0])');
  assert.equal(ui.run('attachments.length'),1); assert.equal(ui.run('attachments[0].plan.estimated_frames'),240);
});
function installChat(ui) {
  Object.assign(ui.context,{messages:[],busy:null,settings:{thinking:'none',temperature:0.6,top_p:0.95,top_k:20},
    mcpInfo:{tools:0},projectionLoaded:()=>false,renderChat(){},updateAssistant(){},scrollDown(){},autosize(){},
    requestAnimationFrame:()=>0,cancelAnimationFrame(){},performance,fmt:n=>String(n)});
  ui.fields.chat=element(); ui.fields.chat.lastElementChild={}; ui.fields.input=element('分析动作');
  const original=ui.context.fetch;
  ui.context.fetch=async(url,options)=>{
    const body=JSON.parse(options.body);
    if(!body.stream)return original(url,options);
    ui.calls.push({url,body}); let done=false;
    return {ok:true,body:{getReader:()=>({read:async()=>{
      if(done)return {done:true}; done=true;
      return {done:false,value:new TextEncoder().encode('data: '+JSON.stringify({choices:[{delta:{content:'最终视频分析'}}]})+'\n\ndata: [DONE]\n\n')};
    }})}};
  };
  ui.context.TextDecoder=TextDecoder;
  ui.run(source.slice(source.indexOf('function apiMessages()'),source.indexOf('$("composer").onsubmit')));
  ui.run(source.slice(source.indexOf('function fileBlock(f)'),source.indexOf('function renderAttachments()')));
  ui.run(source.slice(source.indexOf('function saveChat()'),source.indexOf('function timeStr(')));
}
test('real chat send path analyzes 240 frames then streams a text-only summary and saves no video payload',async()=>{
  const ui=videoPage(); ui.fields['video-duration'].value=10;ui.fields['video-fps'].value=24;ui.fields['video-fps'].onchange();
  await ui.run('addVideo({name:"clip.mp4",size:1000})'); installChat(ui);
  await ui.run('send()');
  assert.equal(ui.run('messages[1].text'),'最终视频分析'); assert.equal(ui.run('busy'),null);
  assert.equal(ui.run('messages[0].videos[0].analysis.coverage.sample_count'),240);
  const summary=ui.calls.find(c=>c.body.stream);
  assert.ok(summary); assert.ok(summary.body.messages.every(m=>typeof m.content==='string'));
  assert.match(summary.body.messages[0].content,/已分析 240 帧/);
  assert.equal(ui.run('messages[0].videos[0].encoded'),undefined);
  const stored=JSON.stringify(ui.saved.chat);assert.ok(!stored.includes('data:image'));assert.ok(!stored.includes('fixture'));
});
test('a failed video upload blocks sending an ordinary text request against old chat images',async()=>{
  const ui=videoPage();installChat(ui);ui.run("videoUploadError='视频上传失败';");await ui.run('send()');
  assert.equal(ui.calls.length,0);assert.equal(ui.run('messages.length'),0);
});

function setupPage(legacy=false, invalid=false, family='qwen') {
  const nodes={}, calls=[], navigations=[], current={configured:true,running:true,chat_url:'http://127.0.0.1:8080/',model:{path:'E:\\old'},job:{state:'ready',message:'ready'},
    defaults:{path:'E:\\old',port:8080,context:32768,backend:'cuda',vision:'gpu',native_video:true},vision_available:true,folder_picker:true};
  const get=id=>nodes[id] || (nodes[id]=element());
  get('vision').options=[];
  const context=vm.createContext({console,URL,URLSearchParams,setTimeout,clearTimeout,AbortController,Date,crypto:require('node:crypto').webcrypto,
    document:{getElementById:get,querySelector:()=>({content:'fixture-token'}),createElement:()=>element()},
    location:{search:''},window:{location:{assign(url){navigations.push(url);}}},
    fetch:async (url,options)=>{
      const body=options.body ? JSON.parse(options.body):null; calls.push({url,body});
      if(url==='/api/models') return {ok:true,json:async()=>({models:[{path:'E:\\old',label:'Old',api_model_id:'configured-old-id'},{path:'E:\\new',label:'New',api_model_id:'configured-new-id'}]})};
      if(url==='/api/check') return {ok:!invalid,json:async()=>invalid ? {error:'模型目录不完整'} : {model:{path:body.path,family}}};
      if(url==='/api/import') {
        if(invalid)return {ok:false,json:async()=>({error:'模型目录不完整'})};
        if(legacy&&current.running)return {ok:false,json:async()=>({error:'请先停止当前服务，再更换模型。'})};
        current.running=true; current.model={path:body.path}; current.job={state:'starting',message:'switching'};
      }
      if(url==='/api/stop'){current.running=false;current.job={state:'idle',message:'stopped'};}
      return {ok:true,json:async()=>current};
    }});
  const script=fs.readFileSync(path.join(__dirname,'../serve/web/portable-setup.js'),'utf8').replace(/loadAsrSettings\(\);\s*poll\(\);\s*scan\(\);\s*$/, '');
  vm.runInContext(script,context); vm.runInContext('render('+JSON.stringify(current)+')',context);
  return {nodes,calls,navigations,context,run:code=>vm.runInContext(code,context)};
}
test('setup form follows the actual switched API port and the next model uses it', async () => {
  const ui=setupPage();
  ui.run("render({...state, defaults:{...state.defaults,port:8081},chat_url:'http://127.0.0.1:8081/'})");
  assert.equal(Number(ui.nodes.port.value),8081);
  await ui.run('importSelectedModel()');
  const call=ui.calls.find(c=>c.url==='/api/import');
  assert.equal(call.body.port,8081);
});
test('setup polling preserves an unsaved user port edit', () => {
  const ui=setupPage();ui.nodes.port.value='9123';
  ui.run("render({...state, defaults:{...state.defaults,port:8081},chat_url:'http://127.0.0.1:8081/'})");
  assert.equal(ui.nodes.port.value,'9123');
});
test('manually entered DeepSeek pack applies text-only limits before import and restores Qwen choices',async()=>{
  const ui=setupPage(false,false,'deepseek41');
  ui.nodes['model-path'].value='D:/existing native pack';
  ui.nodes['deepseek-speed'].value='full';
  await ui.run('importSelectedModel()');
  const sent=ui.calls.find(c=>c.url==='/api/import').body;
  assert.equal(sent.path,'D:/existing native pack');assert.equal(sent.context,8192);
  assert.equal(sent.vision,'no');assert.equal(sent.native_video,false);assert.equal(sent.speed,'full');
  assert.equal(ui.nodes['deepseek-options'].hidden,false);
  ui.run("applyModelCapabilities({family:'qwen'})");
  assert.equal(ui.nodes.context.value,'32768');assert.equal(ui.nodes.vision.value,'gpu');
  assert.equal(ui.nodes['native-video'].checked,true);assert.equal(ui.nodes['deepseek-options'].hidden,true);
});

test('DeepSeek Faster is explicit and first entering Qwen restores visual defaults',async()=>{
  const ui=setupPage(false,false,'deepseek41');ui.nodes['deepseek-speed'].value='faster';
  await ui.run('importSelectedModel()');
  assert.equal(ui.calls.find(c=>c.url==='/api/import').body.speed,'faster');
  ui.run("qwenSettings=null;applyModelCapabilities({family:'qwen'})");
  assert.equal(ui.nodes.vision.value,'auto');assert.equal(ui.nodes.context.value,'32768');
});

test('LAN base URLs require ready authenticated service and private IPv4 matching the actual API port',async()=>{
  const ui=setupPage();
  ui.run("render({...state,api_connection:{model_id:'live-id',base_url:'http://127.0.0.1:8080/v1',api_key_required:true,lan_base_urls:['http://8.8.8.8:8080/v1','http://192.168.0.5:9999/v1','http://192.168.0.5:8080/v1?key=secret','http://192.168.0.5:8080/v1']}})");
  assert.equal(ui.nodes['lan-connection'].hidden,false);assert.equal(ui.nodes['lan-base-url'].value,'http://192.168.0.5:8080/v1');
  ui.context.navigator={clipboard:{writeText:async value=>assert.equal(value,'http://192.168.0.5:8080/v1')}};
  await ui.nodes['copy-lan-base'].onclick();
  ui.run("render({...state,job:{state:'starting'}})");assert.equal(ui.nodes['lan-connection'].hidden,true);
  assert.equal(ui.nodes['lan-base-url'].value,'');
});
test('LAN settings generate a key, reject clearing in LAN mode, and submit without navigation',async()=>{
  const ui=setupPage();assert.equal(ui.nodes['api-scope'].value,'local');
  ui.nodes['generate-network-key'].onclick();assert.match(ui.nodes['network-key'].value,/^strata_[0-9a-f]{48}$/);
  ui.nodes['api-scope'].value='lan';ui.nodes['clear-network-key'].checked=true;ui.nodes['api-scope'].onchange();
  assert.equal(ui.nodes['clear-network-key'].checked,false);assert.equal(ui.nodes['clear-network-key'].disabled,true);
  await ui.nodes['network-form'].onsubmit({preventDefault(){}});
  const call=ui.calls.find(c=>c.url==='/api/network');assert.equal(call.body.scope,'lan');assert.equal(call.body.clear_api_key,false);
  assert.match(call.body.api_key,/^strata_/);assert.equal(ui.nodes['network-key'].value,'');
  assert.equal(ui.nodes['save-network'].disabled,true);assert.equal(ui.navigations.length,0);
});
test('model card immediately switches on both new and already-running older launchers',async()=>{
  for (const legacy of [false,true]) {
    const ui=setupPage(legacy); await ui.run('scan()');
    await ui.nodes.candidates.children[0].querySelector('.candidate-select').onclick(); assert.equal(ui.calls.filter(c=>c.url==='/api/import').length,0);
    await ui.nodes.candidates.children[1].querySelector('.candidate-select').onclick();
    const lifecycle=ui.calls.filter(c=>['/api/import','/api/stop'].includes(c.url)).map(c=>c.url);
    assert.deepEqual(lifecycle,legacy?['/api/import','/api/stop','/api/import']:['/api/import']);
    assert.equal(ui.calls.filter(c=>c.url==='/api/import').at(-1).body.path,'E:\\new');
    assert.equal(ui.navigations.length,0,'Do not enter the old model chat during a switch');
    ui.run("render({...state,job:{state:'ready',message:'ready'}})");
    assert.deepEqual(ui.navigations,['http://127.0.0.1:8080/']);
  }
});
test('invalid model selection does not stop the running backend',async()=>{
  const ui=setupPage(false,true); await ui.run('scan()'); await ui.nodes.candidates.children[1].querySelector('.candidate-select').onclick();
  assert.equal(ui.calls.filter(c=>c.url==='/api/stop').length,0); assert.equal(ui.nodes['error-box'].hidden,false);
});

test('updater acknowledgement shows progress instead of the stale new-version message',async()=>{
  const ui=setupPage();
  ui.run("render({...state,update:{supported:true,state:'ready',available:true,message:'发现新版本'}})");
  await ui.nodes['update-start'].onclick();
  assert.equal(ui.run('state.job.state'),'updating');
  assert.match(ui.nodes['update-message'].textContent,/确认更新程序/);
  assert.equal(ui.nodes.save.disabled,true);
});

test('the old update page reloads its token after the same installation restarts',async()=>{
  const ui=setupPage();let reloads=0;
  ui.context.window.location.replace=url=>{assert.equal(url,'/');reloads++;};
  ui.run("render({...state,app_root:'E:/Strata',instance_id:'old',version:'1',job:{state:'updating',message:'working'}})");
  ui.context.fetch=async()=>({ok:true,json:async()=>({service:'strata-portable-ui',app_root:'E:/Strata',instance_id:'new',version:'1'})});
  await ui.run('reconnectAfterUpdate()');assert.equal(reloads,1);
});

test('an unrelated service or the original launcher cannot trigger an update-page reload',async()=>{
  const ui=setupPage();let reloads=0;
  ui.context.window.location.replace=()=>{reloads++;};
  ui.run("render({...state,app_root:'E:/Strata',instance_id:'old',version:'1',job:{state:'updating',message:'working'}})");
  for(const identity of [
    {service:'strata-portable-ui',app_root:'E:/Other',instance_id:'new',version:'2'},
    {service:'strata-portable-ui',app_root:'E:/Strata',instance_id:'old',version:'1'},
    {service:'llama.cpp',app_root:'E:/Strata',instance_id:'new',version:'2'},
  ]) {
    ui.context.fetch=async()=>({ok:true,json:async()=>identity});
    await ui.run('reconnectAfterUpdate()');
  }
  assert.equal(reloads,0);
});

test('a slow update exposes recovery instructions without declaring a failure',async()=>{
  const ui=setupPage();
  ui.run("clearError();render({...state,job:{state:'updating',message:'working'}});updateStartedAt=Date.now()-121000");
  ui.context.fetch=async()=>{throw Error('network unavailable');};
  await ui.run('reconnectAfterUpdate()');
  assert.notEqual(ui.nodes['error-box'].hidden,false);
  assert.match(ui.nodes['update-message'].textContent,/UPDATE-PORTABLE\.bat/);
  assert.match(ui.nodes['update-message'].textContent,/update-helper\.log/);
});

test('a lost update reply keeps the old page in reconnect mode',async()=>{
  const ui=setupPage();
  ui.run("render({...state,update:{supported:true,state:'ready',available:true,message:'new'}})");
  ui.context.fetch=async()=>{throw Error('launcher exited before acknowledgement');};
  await ui.nodes['update-start'].onclick();
  assert.equal(ui.run('state.job.state'),'updating');
  assert.equal(ui.nodes.save.disabled,true);
  assert.match(ui.nodes['update-message'].textContent,/update-helper\.log/);
});

test('an explicit rejected update restores the usable settings page',async()=>{
  const ui=setupPage();
  ui.run("render({...state,job:{state:'idle',message:'idle'},update:{supported:true,state:'ready',available:true,message:'new'}})");
  ui.context.fetch=async()=>({ok:false,json:async()=>({error:'update not available'})});
  await ui.nodes['update-start'].onclick();
  assert.equal(ui.run('state.job.state'),'idle');
  assert.equal(ui.nodes['error-box'].hidden,false);
  assert.match(ui.nodes['error-detail'].textContent,/not available/);
});

test('a lost reply followed by a confirmed running update does not show a false error',async()=>{
  const ui=setupPage();ui.run('clearError()');
  ui.context.fetch=async url=>{
    if(url==='/api/update/start')throw Error('reply lost');
    return {ok:true,json:async()=>ui.run("({...state,job:{state:'updating',message:'confirmed working'}})")};
  };
  await ui.nodes['update-start'].onclick();
  assert.equal(ui.run('state.job.state'),'updating');
  assert.equal(ui.nodes['error-box'].hidden,true);
  assert.match(ui.nodes['update-message'].textContent,/confirmed working/);
});

test('restore controls recover after rejection and remain frozen only while updating',async()=>{
  const ui=setupPage();
  ui.context.fetch=async url=>url==='/api/update/backups'?{ok:true,json:async()=>({backups:[
    {id:'a'.repeat(32),available:true,to_version:'new',from_version:'old'},
    {id:'b'.repeat(32),available:false,to_version:'other',from_version:'older'}]})}:
    {ok:false,json:async()=>({error:'fixture backup refused'})};
  await ui.nodes['backup-list'].onclick();
  const buttons=ui.nodes['backup-versions'].querySelectorAll('.backup-restore');
  assert.equal(buttons[0].disabled,false);assert.equal(buttons[1].disabled,true);
  await buttons[0].onclick();
  assert.equal(buttons[0].disabled,false);assert.equal(buttons[1].disabled,true);
  ui.run("render({...state,job:{state:'updating',message:'working'}})");
  assert.equal(buttons[0].disabled,true);assert.equal(ui.nodes['backup-list'].disabled,true);
  ui.run("render({...state,job:{state:'error',message:'failed'}})");
  assert.equal(buttons[0].disabled,false);assert.equal(buttons[1].disabled,true);
  assert.equal(ui.nodes['backup-list'].disabled,false);
});

test('a stalled status request is aborted and releases the polling lock',async()=>{
  const ui=setupPage();const timers=[];
  ui.context.setTimeout=fn=>{timers.push(fn);return timers.length;};
  ui.context.clearTimeout=()=>{};
  ui.context.fetch=async(_url,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('timed out'))));
  const polling=ui.run('poll()');assert.equal(ui.run('polling'),true);
  timers[0]();await polling;
  assert.equal(ui.run('polling'),false);
  assert.match(ui.nodes['error-detail'].textContent,/timed out/);
});
test('DSH connection uses the live API ID, copies it, and clears during a switch',async()=>{
  const ui=setupPage(), copied=[];
  ui.context.navigator={clipboard:{writeText:async value=>copied.push(value)}};
  ui.run("render({...state,api_connection:{model_id:'live-sc117-id',base_url:'http://127.0.0.1:8080/v1',api_key_required:false}})");
  assert.equal(ui.nodes['api-connection'].hidden,false);
  assert.equal(ui.nodes['api-model-id'].value,'live-sc117-id');
  assert.equal(ui.nodes['api-base-url'].value,'http://127.0.0.1:8080/v1');
  assert.equal(ui.nodes['api-models-link'].href,'http://127.0.0.1:8080/v1/models');
  await ui.nodes['copy-api-model'].onclick(); await ui.nodes['copy-api-base'].onclick();
  assert.deepEqual(copied,['live-sc117-id','http://127.0.0.1:8080/v1']);
  ui.run("render({...state,job:{state:'starting',message:'switching'}})");
  assert.equal(ui.nodes['api-connection'].hidden,true); assert.equal(ui.nodes['api-model-id'].value,'');
  assert.equal(ui.nodes['api-base-url'].value,''); assert.equal(ui.nodes['api-copy-status'].textContent,'');
  assert.equal(ui.nodes['api-models-link'].href,undefined);
  await ui.nodes['copy-api-model'].onclick(); assert.equal(copied.length,2);
});
test('DSH copy falls back to selecting text and rejects a mismatched API origin',async()=>{
  const ui=setupPage();
  ui.run("render({...state,api_connection:{model_id:'actual-id',base_url:'http://127.0.0.1:8080/v1',api_key_required:true}})");
  await ui.nodes['copy-api-model'].onclick();
  assert.equal(ui.nodes['api-model-id'].selected,true);
  assert.ok(ui.nodes['api-copy-status'].textContent.includes('Ctrl+C'));
  assert.ok(ui.nodes['api-key-hint'].textContent.includes('你为此服务设置的密钥'));
  ui.run("render({...state,api_connection:{model_id:'actual-id',base_url:'https://other.invalid/v1'}})");
  assert.equal(ui.nodes['api-connection'].hidden,true); assert.equal(ui.nodes['api-model-id'].value,'');
});
test('model card copy uses the current API ID and never switches or stops a model',async()=>{
  const ui=setupPage(), copied=[];
  ui.context.navigator={clipboard:{writeText:async value=>copied.push(value)}};
  ui.run("render({...state,api_connection:{model_id:'actual-old-id',base_url:'http://127.0.0.1:8080/v1'}})");
  await ui.run('scan()');
  const [current, other]=ui.nodes.candidates.children;
  assert.equal(current.querySelector('.candidate-api-label').textContent,'当前 API 模型 ID');
  assert.equal(current.querySelector('.candidate-api-id').value,'actual-old-id');
  assert.equal(other.querySelector('.candidate-api-id').value,'configured-new-id');
  assert.equal(other.querySelector('.candidate-api-id').readOnly,true);
  other.querySelector('.candidate-api-id').onclick();
  assert.equal(other.querySelector('.candidate-api-id').selected,true);
  await current.querySelector('.candidate-copy').onclick(); await other.querySelector('.candidate-copy').onclick();
  assert.deepEqual(copied,['actual-old-id','configured-new-id']);
  assert.equal(ui.calls.filter(c=>['/api/import','/api/stop'].includes(c.url)).length,0);
  assert.equal(ui.nodes['model-path'].value,'E:\\old'); assert.equal(ui.navigations.length,0);
});
test('model card copy remains available during a switch and falls back when clipboard is denied',async()=>{
  const ui=setupPage(); await ui.run('scan()');
  const card=ui.nodes.candidates.children[1], field=card.querySelector('.candidate-api-id');
  ui.context.navigator={clipboard:{writeText:async()=>{throw new Error('denied');}}};
  let legacyCopies=0; ui.context.document.execCommand=command=>{assert.equal(command,'copy');legacyCopies++;return true;};
  ui.run("render({...state,job:{state:'starting',message:'switching'}})");
  assert.equal(card.querySelector('.candidate-select').disabled,true);
  assert.equal(card.querySelector('.candidate-copy').disabled,false);
  await card.querySelector('.candidate-copy').onclick();
  assert.equal(legacyCopies,1); assert.equal(field.selected,true);
  assert.equal(card.querySelector('.candidate-copy-status').textContent,'已复制。');
  assert.equal(ui.calls.filter(c=>['/api/import','/api/stop'].includes(c.url)).length,0);
});
test('speech evidence preserves utterance identity and never assigns words to microframes',()=>{
  const value={contract_version:1,timeline:'input',status:'complete',cues:[{id:'u1',start_s:1,end_s:8,text:'一句完整对白'}]};
  const a=S.overlap(value,1,1.333)[0],b=S.overlap(value,1.333,1.666)[0];
  assert.equal(a.id,b.id);assert.equal(b.start_s,1);assert.equal(b.end_s,8);assert.equal(b.text,'一句完整对白');
  assert.deepEqual(S.overlap(value,8,9),[]);assert.equal(S.groups(value).length,1);
});
test('automatic ASR happens once for the selected window and imported subtitles bypass model loading',async()=>{
  for(const imported of [false,true]) {
    const ui=videoPage(); ui.fields['video-processing'].value='segmented';ui.fields['video-duration'].value=10;
    await ui.run('addVideo({name:"clip.mp4",size:1000})');
    if(imported)ui.fields['video-timed-transcript'].value='1\n00:00:01,000 --> 00:00:02,000\n向右移动';
    const result=await ui.run('prepareVideoSpeech(attachments[0],new AbortController().signal,()=>{})');
    assert.equal(result.cues[0].id,'u1');
    assert.equal(ui.calls.filter(c=>c.url==='v1/video/transcribe').length,imported?0:1);
    assert.equal(ui.calls.filter(c=>c.url==='v1/asr/capabilities').length,imported?0:1);
    if(!imported){const request=ui.calls.find(c=>c.url==='v1/video/transcribe').body;assert.equal(request.start_s,0);assert.equal(request.end_s,10);}
  }
});
test('dense visual stages get only overlapping dialogue and final fusion uses unique cues',async()=>{
  const ui=videoPage();ui.fields['video-processing'].value='segmented';ui.fields['video-duration'].value=2;
  ui.fields['video-fps'].value=24;
  await ui.run('addVideo({name:"clip.mp4",size:1000})');
  ui.run("attachments[0].speech={contract_version:1,timeline:'input',status:'complete',cues:[{id:'u1',start_s:0,end_s:1,text:'向右移动'}]}");
  const output=await ui.run("analyzeVideoSegments(attachments[0],'分析',{},new AbortController().signal,()=>{})");
  const calls=ui.calls.filter(c=>c.url==='v1/chat/completions');
  const observations=calls.filter(c=>Array.isArray(c.body.messages[0].content));
  assert.equal(observations.length,6);
  assert.ok(observations[0].body.messages[0].content[0].text.includes('[u1'));
  assert.ok(!observations.at(-1).body.messages[0].content[0].text.includes('[u1'));
  assert.equal(calls.filter(c=>typeof c.body.messages[0].content==='string' && c.body.messages[0].content.includes('按唯一ID去重')).length,1);
  assert.equal(output.speech.cues.length,1);
});

test('speech defaults off, ordinary dense analysis makes no ASR requests, and preview can be corrected',async()=>{
  const ui=videoPage();assert.equal(ui.fields['video-asr-enabled'].checked,false);
  ui.fields['video-processing'].value='segmented';ui.fields['video-duration'].value=2;ui.fields['video-fps'].value=24;
  await ui.run('addVideo({name:"clip.mp4",size:1000})');
  await ui.run("analyzeVideoSegments(attachments[0],'分析',{},new AbortController().signal,()=>{})");
  assert.equal(ui.calls.filter(call=>call.url.includes('/asr/')||call.url.endsWith('/transcribe')).length,0);
  ui.fields['video-timed-transcript'].value='1\n00:00:01,000 --> 00:00:02,000\n原对白';
  await ui.run('prepareVideoSpeech(attachments[0],new AbortController().signal,()=>{})');
  assert.equal(ui.fields['video-speech-preview'].hidden,false);
  ui.fields['video-speech-srt'].value='1\n00:00:01,000 --> 00:00:02,000\n校对白';
  ui.fields['video-speech-use'].onclick();
  assert.ok(ui.fields['video-timed-transcript'].value.includes('校对白'));
  await ui.run('addVideo({name:"another.mp4",size:1000},attachments[0])');
  assert.equal(ui.fields['video-speech-preview'].hidden,true);
  assert.equal(ui.fields['video-speech-srt'].value,'');
  assert.ok(ui.fields['video-timed-transcript'].value.includes('校对白'));
});

test('selected-window speech uses source clock in SRT and final dialogue groups',()=>{
  const value={contract_version:1,timeline:'selected_window',status:'complete',cues:[{id:'u1',start_s:.5,end_s:2,text:'校对对白'}]};
  assert.ok(S.srt(value,10).includes('00:00:10,500 --> 00:00:12,000'));
  assert.equal(JSON.parse(S.groups(value,5000,10)[0]).start_s,10.5);
  assert.throws(()=>S.validate({...value,status:'silence'}));
});

test('final dialogue fusion excludes subtitles outside the selected video window',()=>{
  const value={contract_version:1,timeline:'input',status:'complete',cues:[
    {id:'outside-before',start_s:0,end_s:5,text:'前面的对白'},
    {id:'overlap',start_s:9,end_s:11,text:'跨边界的完整对白'},
    {id:'inside',start_s:11,end_s:13,text:'片段对白'},
    {id:'outside-after',start_s:15,end_s:17,text:'后面的对白'}]};
  const groups=S.groups(value,5000,10,12).join('\n');
  assert.ok(!groups.includes('outside-before'));assert.ok(!groups.includes('outside-after'));
  const first=JSON.parse(groups.split('\n')[0]);
  assert.equal(first.id,'overlap');assert.equal(first.start_s,9);assert.equal(first.overlap_start_s,10);
  assert.equal(first.text,'跨边界的完整对白');
});

test('audit R01: uploading six seconds then selecting twelve uses the current window on Send',async()=>{
  const ui=videoPage();ui.fields['video-duration'].value=6;ui.fields['video-duration'].onchange();
  await ui.run('addVideo({name:"twelve.mp4",size:1000})');installChat(ui);
  ui.fields['video-duration'].value=12;ui.fields['video-duration'].onchange();
  await ui.run('send()');
  const c=ui.run('messages[0].videos[0].analysis.coverage');
  assert.equal(c.window_end_s,12);assert.equal(c.sample_count,12);assert.equal(c.analysis_completed,true);
});

test('audit R02: changing video mode after upload resamples using the selected mode',async()=>{
  const ui=videoPage();ui.fields['video-mode'].value='frames';
  await ui.run('addVideo({name:"twelve.mp4",size:1000})');installChat(ui);
  ui.fields['video-mode'].value='native';await ui.run('send()');
  assert.equal(ui.calls.filter(c=>c.url==='v1/video/sample').at(-1).body.mode,'native');
  assert.equal(ui.calls.find(c=>c.body?.stream).body.messages[0].content.at(-1).mode,'native');
});

test('audit R03: unchanged single upload sends without decoding twice',async()=>{
  const ui=videoPage();await ui.run('addVideo({name:"twelve.mp4",size:1000})');installChat(ui);
  await ui.run('send()');assert.equal(ui.calls.filter(c=>c.url==='v1/video/sample').length,1);
});

test('audit R04: failed automatic resampling cannot send the old six-second attachment',async()=>{
  const ui=videoPage();ui.fields['video-duration'].value=6;ui.fields['video-duration'].onchange();
  await ui.run('addVideo({name:"twelve.mp4",size:1000})');installChat(ui);
  ui.fields['video-duration'].value=12;ui.fields['video-duration'].onchange();
  ui.context.fetch=async()=>({ok:false,json:async()=>({error:{message:'decode failure'}})});
  await ui.run('send()');assert.equal(ui.run('messages.length'),0);assert.equal(ui.run('busy'),null);
  assert.ok(ui.notices.some(row=>row.join(' ').includes('decode failure')));
});

test('audit R05: Stop during automatic resampling cancels before any inference or chat history entry',async()=>{
  const ui=videoPage();await ui.run('addVideo({name:"twelve.mp4",size:1000})');installChat(ui);
  ui.fields['video-duration'].value=12;ui.fields['video-duration'].onchange();
  let entered;const pending=new Promise(resolve=>entered=resolve);
  ui.context.fetch=async(url,options)=>{entered();return new Promise((resolve,reject)=>{
    options.signal.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError')),{once:true});
  });};
  const sending=ui.run('send()');await pending;
  assert.equal(ui.fields['video-mode'].disabled,true);ui.run('busy.controller.abort()');await sending;
  assert.equal(ui.run('messages.length'),0);assert.equal(ui.run('busy'),null);
  assert.equal(ui.fields['video-mode'].disabled,false);
});

test('audit R06: explicitly selecting six seconds stays six, and Entire Video clears the restriction',async()=>{
  const ui=videoPage();ui.fields['video-start'].value=2;ui.fields['video-duration'].value=6;
  ui.fields['video-duration'].onchange();await ui.run('addVideo({name:"twelve.mp4",size:1000})');
  assert.equal(ui.run('attachments[0].plan.end_s'),8);
  ui.fields['video-entire'].onclick();await ui.run('synchronizePendingVideo(new AbortController().signal)');
  assert.equal(ui.run('attachments[0].plan.start_s'),0);assert.equal(ui.run('attachments[0].plan.end_s'),12);
});

test('audit R07: result labels distinguish a twelve-second window from a six-second last sample',()=>{
  const ui=videoPage();ui.context.labelVideo={manifest:{source:{duration_s:12}},analysis:{coverage:{
    window_start_s:0,window_end_s:12,sample_count:2,completed_segments:1,total_segments:1,
    first_sample_s:0,last_sample_s:6,max_sample_gap_s:6,analysis_completed:true}}};
  const text=ui.run('videoCoverageText(labelVideo)');
  assert.match(text,/原视频 12.00 秒/);assert.match(text,/分析范围 0.00–12.00 秒/);assert.match(text,/实际采样帧 0.00–6.00 秒/);
});

test('audit R08: a trimmed single clip explicitly maps relative frame labels onto input speech time',async()=>{
  const ui=videoPage();ui.fields['video-start'].value=6;
  await ui.run('addVideo({name:"twelve.mp4",size:1000})');installChat(ui);await ui.run('send()');
  const prompt=ui.calls.find(c=>c.body?.stream).body.messages[0].content[0].text;
  assert.match(prompt,/分析范围 6.00–12.00 秒/);assert.match(prompt,/加 6.000 秒/);
});

function emptySegment(segment) {
  return {part:null,empty_segment:true,manifest:{source:{duration_s:12,time_origin_s:5},frames:[],
    sampling:{start_s:segment.start_s,end_s:segment.end_s},coverage:{sample_count:0}}};
}
test('audit R09: source frame gaps do not truncate later visual windows or invent intervening frames',async()=>{
  const p=V.plan({source:{duration_s:12}},{max_frames:8,sampling_fps:24}),seen=[];
  const read=s=>s.index%3===0?sample({...s,max_frames:1,sampling_fps:1}):emptySegment(s);
  const r=await V.run({plan:p,first:read(p.segments[0]),sample:async s=>read(s),
    observe:async(v,s)=>{seen.push(s.index);return 'one actual frame';},merge:async()=> 'bounded summary'});
  assert.equal(r.coverage.window_end_s,12);assert.equal(r.coverage.completed_windows,36);
  assert.equal(r.coverage.empty_segments,24);assert.equal(r.coverage.visual_segments,12);assert.equal(seen.at(-1),33);
  assert.equal(r.coverage.analysis_completed,true);assert.match(r.evidence,/没有源视频画面帧/);
});

test('audit R10: an empty response from the wrong window is rejected',async()=>{
  const p=V.plan({source:{duration_s:12}},{max_frames:8,sampling_fps:24});
  const wrong=emptySegment({...p.segments[0],end_s:6});
  await assert.rejects(V.run({plan:p,first:wrong,sample:async()=>wrong,observe:async()=> 'bad',merge:async()=> 'bad'}),/不一致/);
});

test('audit R11: a wholly empty selected visual window retains timed dialogue without visual inference',async()=>{
  const ui=videoPage();ui.fields['video-processing'].value='segmented';ui.fields['video-duration'].value=1;
  ui.context.fetch=async(url,options)=>url==='v1/video/sample'?
    {ok:true,json:async()=>emptySegment({start_s:0,end_s:1})}:
    {ok:true,json:async()=>({choices:[{message:{content:'对白：继续'}}]})};
  await ui.run('addVideo({name:"vfr.mp4",size:1000})');
  ui.run("attachments[0].speech={contract_version:1,timeline:'input',status:'complete',cues:[{id:'u1',start_s:.1,end_s:.9,text:'继续'}]}");
  const r=await ui.run("analyzeVideoSegments(attachments[0],'分析',{},new AbortController().signal,()=>{})");
  assert.equal(r.coverage.sample_count,0);assert.equal(r.coverage.empty_segments,1);
  assert.ok(r.evidence.includes('继续'));assert.ok(r.evidence.includes('不能推断画面'));
});

test('audit R12: speech and visual analysis both use the updated twelve-second window exactly once',async()=>{
  const ui=videoPage();ui.fields['video-duration'].value=6;ui.fields['video-duration'].onchange();
  await ui.run('addVideo({name:"twelve.mp4",size:1000})');installChat(ui);
  const original=ui.context.fetch;ui.context.fetch=(url,options)=>url==='v1/chat/completions'?original(url,options):
    (options.body && url==='v1/video/transcribe' ? (ui.calls.push({url,body:JSON.parse(options.body)}),Promise.resolve({ok:true,json:async()=>({contract_version:1,timeline:'input',status:'complete',cues:[]})})) :
      url==='v1/asr/capabilities'||url==='v1/asr/status'?Promise.resolve({ok:true,json:async()=>({supported:true,phase:'completed'})}):original(url,options));
  ui.fields['video-duration'].value=12;ui.fields['video-duration'].onchange();ui.fields['video-asr-enabled'].checked=true;
  await ui.run('send()');const asr=ui.calls.filter(c=>c.url==='v1/video/transcribe');
  assert.equal(asr.length,1);assert.equal(asr[0].body.end_s,12);
  assert.equal(ui.run('messages[0].videos[0].analysis.coverage.window_end_s'),12);
});

test('old saved video history without window fields still renders and does not invent a duration',()=>{
  const ui=videoPage();ui.context.oldVideo={analysis:{coverage:{sample_count:8,first_sample_s:0,last_sample_s:6}}};
  const text=ui.run('videoCoverageText(oldVideo)');assert.match(text,/分析范围 \?–\? 秒/);
  assert.match(text,/实际采样帧 0.00–6.00 秒/);assert.ok(!text.includes('NaN'));
});

test('source reuse uploads once across all segments and timed speech, retaining no encoded original in the attachment',async()=>{
  const ui=videoPage(),original=ui.context.fetch;let uploads=0;
  ui.context.StrataVideoTasks={token:()=> 'a'.repeat(64),call:async(path,value)=>{
    if(path==='v1/video/sources'){uploads++;assert.equal(value.data,'fixture');return {id:'source'};}
    throw new Error('unexpected task');
  }};
  ui.context.fetch=async(path,options)=>path==='v1/video/capabilities'?{ok:true,json:async()=>({source_handles:true})}:original(path,options);
  ui.run("$('video-source-reuse').checked=true");ui.fields['video-processing'].value='segmented';ui.fields['video-fps'].value=24;
  await ui.run("addVideo({name:'clip.mp4',size:100})");
  assert.equal(ui.run('attachments[0].encoded'),undefined);assert.equal(uploads,1);
  ui.context.video=ui.run('attachments[0]');
  await ui.run("prepareVideoSpeech(video,new AbortController().signal,()=>{})");
  await ui.run("analyzeVideoSegments(video,'question',{},new AbortController().signal,()=>{})");
  assert.equal(uploads,1);
  const sources=ui.calls.filter(c=>['v1/video/sample','v1/video/transcribe'].includes(c.url));
  assert.ok(sources.length>18);assert.ok(sources.every(c=>c.body.source_id==='source' && !('data' in c.body)));
});

test('saved observation progress skips completed model calls after browser restart',async()=>{
  const p=V.plan({source:{duration_s:12}},{sampling_fps:2,max_frames:8}),controller=new AbortController();let saved,observations=0;
  await assert.rejects(V.run({plan:p,first:sample(p.segments[0]),sample:async s=>sample(s),
    observe:async()=>{observations++;return 'observed';},merge:async()=> 'merged',signal:controller.signal,
    checkpoint:async state=>{saved=structuredClone(state);if(state.notes.length===1)controller.abort();}}),{name:'AbortError'});
  assert.equal(observations,1);assert.equal(saved.notes.length,1);
  const result=await V.run({plan:p,first:null,resume:JSON.parse(JSON.stringify(saved)),sample:async s=>sample(s),
    observe:async()=>{observations++;return 'observed';},merge:async()=> 'merged',signal:new AbortController().signal});
  assert.equal(observations,3);assert.equal(result.coverage.sample_count,24);assert.equal(result.coverage.window_end_s,12);
});

test('hierarchical merge resumes completed groups and does not repeat them',async()=>{
  const p=V.plan({source:{duration_s:12}},{sampling_fps:4,max_frames:4});let saved,merged=0;
  const controller=new AbortController();
  await assert.rejects(V.run({plan:p,first:sample(p.segments[0]),sample:async s=>sample(s),observe:async()=> 'x'.repeat(900),
    merge:async()=>{merged++;return 'short summary';},signal:controller.signal,checkpoint:async state=>{
      saved=structuredClone(state);if(state.merge_state?.next.length===1)controller.abort();}}),{name:'AbortError'});
  assert.equal(saved.notes.length,12);assert.equal(merged,1);
  await V.run({plan:p,first:null,sample:async()=>{throw new Error('must not sample again');},observe:async()=>{throw new Error('must not observe again');},
    merge:async()=>{merged++;return 'short summary';},resume:saved,signal:new AbortController().signal});
  assert.equal(merged,2);
});

test('boundary continuity provides previous image and bounded historical text without counting it twice',async()=>{
  const p=V.plan({source:{duration_s:12}},{sampling_fps:2,max_frames:8});const bridges=[];
  const result=await V.run({plan:p,first:sample(p.segments[0]),sample:async s=>sample(s),continuity:true,
    observe:async sampled=>{bridges.push(sampled.bridge_frame);assert.ok(sampled.history.length<=4000);return 'chronological note';},
    merge:async()=> 'merged',signal:new AbortController().signal});
  assert.equal(bridges[0],null);assert.equal(bridges[1].source_timestamp_s,8.5);
  assert.equal(result.coverage.sample_count,24);
});

test('actual restore control freezes the saved plan when container duration differs slightly from entered seconds',async()=>{
  const ui=videoPage();ui.context.document={createElement:()=>element()};ui.context.busy=null;
  const plan=V.plan({source:{duration_s:11.9996875}},{sampling_fps:2,max_frames:15,end_s:12});
  const task={id:'task',source_id:'source',revision:0,config:{plan,question:'original question',mode:'frames',name:'clip.mp4',
    sampling:{segmented:true,sampling_fps:2,max_frames:15,end_s:12,continuity:true,sampling_strategy:'adaptive'}},
    state:{notes:[],times:[],empty_segments:0,phase:'observe'}};
  ui.context.StrataVideoTasks={token:()=> 'a'.repeat(64),call:async(path)=>path.endsWith('/list')?
    {tasks:[{...task,completed:0,phase:'observe'}]}:structuredClone(task)};
  vm.runInContext(source.slice(source.indexOf("if ($('video-task-refresh'))"),source.indexOf("$('video-mode').addEventListener")),ui.context);
  await ui.fields['video-task-refresh'].onclick();
  const row=ui.fields['video-task-list'].children[0];await row.children[1].onclick();
  assert.equal(ui.fields['input'].value,'original question');assert.equal(ui.fields['video-duration'].value,'12');
  assert.equal(ui.run('videoParametersChanged(attachments[0])'),false);
  assert.equal(ui.fields['video-fps'].disabled,true);
  assert.equal(ui.run('attachments[0].file'),undefined);
  assert.equal(ui.run('attachments[0].plan.end_s'),11.9996875);
});
