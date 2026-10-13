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
  function coverage(times, p, completed) {
    const sorted = [...times].sort((a, b) => a - b), unique = [];
    for (const t of sorted) if (!unique.length || t - unique[unique.length - 1] > 1e-7) unique.push(t);
    let gap = 0;
    for (let i = 1; i < unique.length; i++) gap = Math.max(gap, unique[i] - unique[i - 1]);
    return {sample_count: unique.length, first_sample_s: unique[0] ?? null,
      last_sample_s: unique[unique.length - 1] ?? null, max_sample_gap_s: gap,
      window_start_s: p.start_s, window_end_s: p.end_s, completed_segments: completed,
      total_segments: p.segments.length, analysis_completed: completed === p.segments.length,
      continuous_observation: false};
  }
  function record(note) { return `[${note.start_s.toFixed(3)}–${note.end_s.toFixed(3)} 秒；${note.frames} 帧]\n${note.text}`; }
  async function compact(notes, merge, signal, progress) {
    let records = notes.map(record);
    for (let round = 0; records.join('\n\n').length > MERGE_CHARS; round++) {
      if (round >= 10) throw new Error('分段记录未能压缩到上下文预算，请缩短视频窗口。');
      const groups = [];
      for (const text of records) {
        if (text.length > MERGE_CHARS) throw new Error('单段观察记录过长，请减少回答长度后重试。');
        const group = groups[groups.length - 1];
        if (!group || group.join('\n\n').length + text.length + 2 > MERGE_CHARS) groups.push([text]);
        else group.push(text);
      }
      const next = [];
      for (let i = 0; i < groups.length; i++) {
        check(signal);
        progress({phase: 'merge', group: i + 1, groups: groups.length});
        const text = await merge(groups[i].join('\n\n'));
        check(signal);
        if (typeof text !== 'string' || !text.trim() || text.length > 3000) throw new Error('分段汇总为空或超出文字预算。');
        next.push(text);
      }
      records = next;
    }
    return records.join('\n\n');
  }
  async function run({plan: p, first, sample, observe, merge, signal, progress = () => {}}) {
    const notes = [], times = [];
    const notify = (extra) => progress({...extra, completed: notes.length, total: p.segments.length,
      coverage: coverage(times, p, notes.length), notes});
    for (const segment of p.segments) {
      check(signal);
      notify({phase: 'sample', segment});
      const sampled = segment.index === 0 ? first : await sample(segment);
      check(signal);
      const frames = sampled?.part?.frames, sampling = sampled?.manifest?.sampling;
      if (!frames?.length || frames.length > segment.max_frames || !sampling ||
          Math.abs(sampling.start_s - segment.start_s) > 1e-6 || Math.abs(sampling.end_s - segment.end_s) > 1e-6) {
        throw new Error('返回的视频采样与分段计划不一致。');
      }
      notify({phase: 'observe', segment});
      sampled.pixel_measurements = await measureFrames(sampled, signal);
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
      notify({phase: 'observed', segment});
    }
    const evidence = await compact(notes, merge, signal, notify);
    check(signal);
    return {notes, evidence, coverage: coverage(times, p, notes.length)};
  }
  const api = {plan, run, coverage, pixelMeasurements};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.StrataVideoAnalysis = api;
})(globalThis);
