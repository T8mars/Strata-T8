// Bounded sequential video analysis. Each request stays within the server's
// frame limit; the selected window can contain hundreds of sampled frames.
(function (root) {
  'use strict';
  const MAX_SEGMENTS = 512, MERGE_CHARS = 6000;
  function check(signal) { if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError'); }
  function pixelMeasurements(rgba, width, height) {
    const count = width * height, hist = Array.from({length:3},()=>new Uint32Array(256));
    for (let i=0;i<count;i++) for(let c=0;c<3;c++) hist[c][rgba[i*4+c]]++;
    const median = hist.map(h => {
      let total=0,low=null,high=null;
      for(let v=0;v<256;v++){total+=h[v];if(low===null&&total>(count-1)/2)low=v;if(total>count/2){high=v;break;}}
      return (low+high)/2;
    });
    let foreground=0,x=0,y=0;
    for(let i=0;i<count;i++) if(Math.abs(rgba[i*4]-median[0])>45||Math.abs(rgba[i*4+1]-median[1])>45||Math.abs(rgba[i*4+2]-median[2])>45){foreground++;x+=i%width+.5;y+=Math.floor(i/width)+.5;}
    const uniform=1-foreground/count;
    if(uniform<.8)return {};
    const round=n=>Math.round(n*10000)/10000;
    const result={dominant_uniform_rgb:median.map(Math.trunc),uniform_fraction:round(uniform)};
    if(foreground>=count*.005&&foreground<=count*.2){result.foreground_region_center_xy=[round(x/foreground/width),round(y/foreground/height)];result.foreground_region_fraction=round(1-uniform);result.center_tolerance_xy=[round(1/width),round(1/height)];}
    return result;
  }
  async function measureFrames(sampled, signal) {
    check(signal);
    if(typeof createImageBitmap!=='function'||typeof OffscreenCanvas!=='function')return [];
    const measurements=[];
    for(const frame of sampled.part.frames){
      check(signal);
      if(!frame.source?.startsWith('data:image/')||frame.source.length>32*1024**2)continue;
      let bitmap;
      try {
        const response=await fetch(frame.source,{signal});bitmap=await createImageBitmap(await response.blob());
        const scale=Math.min(1,256/Math.max(bitmap.width,bitmap.height));
        const width=Math.max(1,Math.round(bitmap.width*scale)),height=Math.max(1,Math.round(bitmap.height*scale));
        const canvas=new OffscreenCanvas(width,height),context=canvas.getContext('2d');context.drawImage(bitmap,0,0,width,height);
        const value=pixelMeasurements(context.getImageData(0,0,width,height).data,width,height);
        if(value.dominant_uniform_rgb)measurements.push({index:frame.index,timestamp_s:frame.timestamp_s,pixel_measurements:value});
      } catch(error) { if(error.name==='AbortError')throw error; }
      finally { bitmap?.close(); }
    }
    check(signal);return measurements;
  }
  function plan(manifest, options) {
    const duration = manifest?.source?.duration_s;
    if (!Number.isFinite(duration) || duration <= 0) throw new Error('无法确认视频总时长，请先转换为包含时长信息的 MP4。');
    const start = options.start_s || 0, end = Math.min(options.end_s ?? duration, duration);
    const fps = options.sampling_fps, frames = options.max_frames;
    if (!Number.isFinite(start) || start < 0 || !Number.isFinite(end) || end <= start || end - start > 3600 ||
        !Number.isFinite(fps) || fps <= 0 || fps > 120 || !Number.isInteger(frames) || frames < 2 || frames > 64) {
      throw new Error('请选择有效的视频时间范围、FPS 和每段帧数。');
    }
    const count = Math.ceil((end - start) * fps / frames - 1e-9);
    if (count > MAX_SEGMENTS) throw new Error(`本次需要 ${count} 段，超过 ${MAX_SEGMENTS} 段；请缩短时间范围或降低 FPS。`);
    const segments = Array.from({length: Math.max(1, count)}, (_, index) => ({
      index, start_s: start + index * frames / fps,
      end_s: Math.min(end, start + (index + 1) * frames / fps),
      sampling_fps: fps, max_frames: frames,
    }));
    return {start_s: start, end_s: end, sampling_fps: fps,
      estimated_frames: Math.ceil((end - start) * fps - 1e-9), segments};
  }
  function coverage(times, p, completed, emptySegments = 0) {
    const sorted = [...times].sort((a, b) => a - b), unique = [];
    for (const t of sorted) if (!unique.length || t - unique[unique.length - 1] > 1e-7) unique.push(t);
    let gap = 0;
    for (let i = 1; i < unique.length; i++) gap = Math.max(gap, unique[i] - unique[i - 1]);
    return {sample_count: unique.length, first_sample_s: unique[0] ?? null,
      last_sample_s: unique[unique.length - 1] ?? null, max_sample_gap_s: gap,
      window_start_s: p.start_s, window_end_s: p.end_s, completed_segments: completed,
      total_segments: p.segments.length, analysis_completed: completed === p.segments.length,
      empty_segments: emptySegments, completed_windows: completed,
      visual_segments: completed - emptySegments,
      continuous_observation: false};
  }
  function record(note) { return `[${note.start_s.toFixed(3)}–${note.end_s.toFixed(3)} 秒；${note.frames} 帧]\n${note.text}`; }
  async function compact(notes, merge, signal, progress, resumed=null, checkpoint=null) {
    let records = resumed?.records || notes.map(record), savedNext=resumed?.next || [];
    for (let round = resumed?.round || 0; records.join('\n\n').length > MERGE_CHARS; round++) {
      if (round >= 10) throw new Error('分段记录未能压缩到上下文预算，请缩短视频窗口。');
      const groups = [];
      for (const text of records) {
        if (text.length > MERGE_CHARS) throw new Error('单段观察记录过长，请减少回答长度后重试。');
        const group = groups[groups.length - 1];
        if (!group || group.join('\n\n').length + text.length + 2 > MERGE_CHARS) groups.push([text]);
        else group.push(text);
      }
      const next = [...savedNext];savedNext=[];
      if(next.length>groups.length)throw new Error('保存的汇总进度不一致。');
      for (let i = next.length; i < groups.length; i++) {
        check(signal);
        progress({phase: 'merge', group: i + 1, groups: groups.length});
        const text = await merge(groups[i].join('\n\n'));
        check(signal);
        if (typeof text !== 'string' || !text.trim() || text.length > 3000) throw new Error('分段汇总为空或超出文字预算。');
        next.push(text);
        if(checkpoint)await checkpoint({round,records,next:[...next]});
      }
      records = next;
      if(checkpoint)await checkpoint({round:round+1,records,next:[]});
    }
    return records.join('\n\n');
  }
  async function run({plan: p, first, sample, observe, merge, signal, progress = () => {}, resume=null, checkpoint=null, continuity=false}) {
    const notes = resume?.notes ? structuredClone(resume.notes) : [], times = resume?.times ? [...resume.times] : [];
    let emptySegments = resume?.empty_segments || 0, previousFrame=null;
    if(notes.length>p.segments.length || times.length!==notes.reduce((sum,n)=>sum+n.frames,0) ||
       notes.some((n,i)=>n.start_s!==p.segments[i].start_s||n.end_s!==p.segments[i].end_s))throw new Error('保存的视频进度与当前计划不一致。');
    const save=async(phase,evidence)=>{if(checkpoint)await checkpoint({notes:structuredClone(notes),times:[...times],empty_segments:emptySegments,phase,...(evidence?{evidence}:{})});};
    const notify = (extra) => progress({...extra, completed: notes.length, total: p.segments.length,
      coverage: coverage(times, p, notes.length, emptySegments), notes});
    for (const segment of p.segments) {
      if(segment.index<notes.length)continue;
      check(signal);
      notify({phase: 'sample', segment});
      const sampled = segment.index === 0 ? first : await sample(segment);
      if(continuity && !previousFrame && segment.index>0) {
        const previousSample=await sample(p.segments[segment.index-1]);
        previousFrame=previousSample?.part?.frames?.at(-1)||null;
      }
      check(signal);
      const frames = sampled?.part?.frames, sampling = sampled?.manifest?.sampling;
      if (sampled?.empty_segment === true && sampled.part === null &&
          sampled.manifest?.frames?.length === 0 && sampled.manifest?.coverage?.sample_count === 0 &&
          sampling && Math.abs(sampling.start_s - segment.start_s) <= 1e-6 &&
          Math.abs(sampling.end_s - segment.end_s) <= 1e-6) {
        emptySegments++;
        notes.push({start_s:segment.start_s, end_s:segment.end_s, frames:0,
          text:'该时间窗口没有源视频画面帧；不能推断画面或动作。对应对白另行保留在整片汇总中。'});
        notify({phase:'observed', segment});
        await save('observe');
        previousFrame=null;
        continue;
      }
      if (!frames?.length || frames.length > segment.max_frames || !sampling ||
          Math.abs(sampling.start_s - segment.start_s) > 1e-6 || Math.abs(sampling.end_s - segment.end_s) > 1e-6) {
        throw new Error('返回的视频采样与分段计划不一致。');
      }
      notify({phase: 'observe', segment});
      sampled.pixel_measurements = await measureFrames(sampled, signal);
      if(continuity) {
        sampled.bridge_frame=previousFrame;
        sampled.history=notes.slice(-8).map(record).join('\n\n').slice(-4000);
      }
      check(signal);
      const text = await observe(sampled, segment, notes[notes.length - 1]);
      check(signal);
      if (typeof text !== 'string' || !text.trim() || text.length > MERGE_CHARS - 200) throw new Error('分段分析没有返回有效的有界文字。');
      const origin = sampled.manifest.source.time_origin_s;
      for (const frame of frames) {
        const time = frame.source_timestamp_s - origin;
        if (!Number.isFinite(time) || time < segment.start_s - 1e-6 || time >= segment.end_s + 1e-6) throw new Error('分段帧时间戳无效。');
        times.push(time);
      }
      notes.push({start_s: segment.start_s, end_s: segment.end_s, frames: frames.length, text});
      previousFrame=frames.at(-1);
      notify({phase: 'observed', segment});
      await save('observe');
    }
    const evidence = resume?.evidence || await compact(notes, merge, signal, notify,resume?.merge_state,
      checkpoint?async merge_state=>checkpoint({notes:structuredClone(notes),times:[...times],empty_segments:emptySegments,phase:'merge',merge_state}):null);
    check(signal);
    await save('merge',evidence);
    return {notes, evidence, coverage: coverage(times, p, notes.length, emptySegments)};
  }
  const api = {plan, run, coverage, pixelMeasurements};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.StrataVideoAnalysis = api;
})(globalThis);
