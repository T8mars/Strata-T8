"""One owned file transcription job. Exiting releases every ASR DLL/model allocation."""
from __future__ import annotations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def main(job_path):
    import numpy as np
    from tools.portable_io import atomic_json
    from serve.asr.confucius.native import NativeQ8Engine
    from serve.asr.confucius.segmented import SegmentedStream
    from serve.asr.confucius.vad import FireRedOnnxVAD
    job = json.loads(Path(job_path).read_text(encoding='utf-8'))
    progress = Path(job['progress'])
    atomic_json(progress,{'phase':'loading','completed_samples':0})
    engine = NativeQ8Engine(Path(job['model_dir']),Path(job['build_dir']),
                           quantization=job['quantization'],gpu_layers=0 if job['device']=='cpu' else -1)
    if job['device']=='cuda':
        from serve.asr.confucius.native import _LOADED_PLUGINS
        if not any(path.name=='ggml-cuda.dll' for path in _LOADED_PLUGINS):
            raise RuntimeError('CUDA ASR backend did not load; CPU fallback is not accepted for a CUDA request')
    pcm = np.load(job['pcm'],mmap_mode='r',allow_pickle=False)
    if pcm.ndim != 1 or pcm.dtype != np.float32 or len(pcm) > 3600*16000 or not np.isfinite(pcm).all():
        raise ValueError('Invalid worker PCM payload')
    session = SegmentedStream(engine,language=None if job['language']=='Auto' else job['language'],
                              context=job['context'],offline=True,vad=FireRedOnnxVAD(Path(job['vad_dir'])))
    last_segments = 0
    for pos in range(0,len(pcm),640):
        session.feed(pcm[pos:pos+640])
        if len(session.segments) != last_segments or pos % 16000 == 0:
            last_segments = len(session.segments)
            atomic_json(progress,{'phase':'transcribing','completed_samples':min(pos+640,len(pcm)),
                                  'total_samples':len(pcm),'segments':last_segments})
    terminal = session.finish()
    result = {'status':'truncated' if terminal.get('truncated') else 'completed','text':session.stable_text,
              'language':session.detected_language,'truncated':bool(terminal.get('truncated')),
              'forced_boundaries':session.forced_boundaries,'segments':session.segments,
              'audio_samples_16k':len(pcm),'model':engine.model_name,'projector':engine.projector_name,'device':job['device']}
    atomic_json(Path(job['result']),result)
    atomic_json(progress,{'phase':'releasing','completed_samples':len(pcm),'total_samples':len(pcm)})


if __name__=='__main__':
    main(sys.argv[1])
