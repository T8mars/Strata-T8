// Offline browser logic checks; no live model, browser state or GPU access.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const V = require('../serve/web/video-analysis.js');
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
    classList:{toggle(){}, add(){}, remove(){}}, setAttribute(){}, reportValidity(){return true;},
    querySelectorAll(){return this.children;}, replaceChildren(){this.children=[];}, append(...v){this.children.push(...v);}};
}
function videoPage() {
  const fields = Object.fromEntries(Object.entries({'video-max-frames':8,'video-fps':1,'video-start':0,
    'video-duration':'','video-processing':'single','video-mode':'native'}).map(([k,v]) => [k,element(v)]));
  const calls = [], saved = {}, notices = [];
  const context = vm.createContext({StrataVideoAnalysis:V, AbortController, DOMException, console,
    health:{images:true}, attachments:[], headers:()=>({}), toast:(...args)=>notices.push(args),
    store:{get:(k,d)=>d, set:(k,v)=>saved[k]=v},
    $:id=>fields[id] || (fields[id]=element()), renderAttachments(){},
    FileReader:class {readAsDataURL(){this.result='data:video/mp4;base64,fixture'; queueMicrotask(()=>this.onload());} abort(){}},
    fetch:async (url, options) => {
      const body = JSON.parse(options.body); calls.push({url,body});
      if (url === 'v1/video/sample') {
        const segment = {...body, start_s:body.start_s || 0, end_s:Math.min(body.end_s ?? 12,12)};
        return {ok:true,json:async()=>sample(segment,12,body.mode)};
      }
      return {ok:true,json:async()=>({choices:[{finish_reason:'stop',message:{content:'动作证据'}}]})};
    },
  });
  vm.runInContext(source.slice(source.indexOf('let videoFrameLimit ='), source.indexOf('function isTextFile(')), context);
  return {context, fields, calls, saved, notices, run:code=>vm.runInContext(code,context)};
}
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

function setupPage(legacy=false, invalid=false) {
  const nodes={}, calls=[], navigations=[], current={configured:true,running:true,chat_url:'http://127.0.0.1:8080/',model:{path:'E:\\old'},job:{state:'ready',message:'ready'},
    defaults:{path:'E:\\old',port:8080,context:32768,backend:'cuda',vision:'gpu',native_video:true},vision_available:true,folder_picker:true};
  const get=id=>nodes[id] || (nodes[id]=element());
  get('vision').options=[];
  const context=vm.createContext({console,URL,URLSearchParams,setTimeout,Date,
    document:{getElementById:get,querySelector:()=>({content:'fixture-token'}),createElement:()=>element()},
    location:{search:''},window:{location:{assign(url){navigations.push(url);}}},
    fetch:async (url,options)=>{
      const body=options.body ? JSON.parse(options.body):null; calls.push({url,body});
      if(url==='/api/models') return {ok:true,json:async()=>({models:[{path:'E:\\old',label:'Old'},{path:'E:\\new',label:'New'}]})};
      if(url==='/api/import') {
        if(invalid)return {ok:false,json:async()=>({error:'模型目录不完整'})};
        if(legacy&&current.running)return {ok:false,json:async()=>({error:'请先停止当前服务，再更换模型。'})};
        current.running=true; current.model={path:body.path}; current.job={state:'starting',message:'switching'};
      }
      if(url==='/api/stop'){current.running=false;current.job={state:'idle',message:'stopped'};}
      return {ok:true,json:async()=>current};
    }});
  const script=fs.readFileSync(path.join(__dirname,'../serve/web/portable-setup.js'),'utf8').replace(/poll\(\);\s*scan\(\);\s*$/, '');
  vm.runInContext(script,context); vm.runInContext('render('+JSON.stringify(current)+')',context);
  return {nodes,calls,navigations,context,run:code=>vm.runInContext(code,context)};
}
test('model card immediately switches on both new and already-running older launchers',async()=>{
  for (const legacy of [false,true]) {
    const ui=setupPage(legacy); await ui.run('scan()');
    await ui.nodes.candidates.children[0].onclick(); assert.equal(ui.calls.filter(c=>c.url==='/api/import').length,0);
    await ui.nodes.candidates.children[1].onclick();
    const lifecycle=ui.calls.filter(c=>['/api/import','/api/stop'].includes(c.url)).map(c=>c.url);
    assert.deepEqual(lifecycle,legacy?['/api/import','/api/stop','/api/import']:['/api/import']);
    assert.equal(ui.calls.filter(c=>c.url==='/api/import').at(-1).body.path,'E:\\new');
    assert.equal(ui.navigations.length,0,'Do not enter the old model chat during a switch');
    ui.run("render({...state,job:{state:'ready',message:'ready'}})");
    assert.deepEqual(ui.navigations,['http://127.0.0.1:8080/']);
  }
});
test('invalid model selection does not stop the running backend',async()=>{
  const ui=setupPage(false,true); await ui.run('scan()'); await ui.nodes.candidates.children[1].onclick();
  assert.equal(ui.calls.filter(c=>c.url==='/api/stop').length,0); assert.equal(ui.nodes['error-box'].hidden,false);
});
