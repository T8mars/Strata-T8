"""One local media job. Native libraries exist only for this process's lifetime."""
from __future__ import annotations
import faulthandler
import json
from pathlib import Path
import sys

def main():
    faulthandler.enable()
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    job_path=Path(sys.argv[1]).resolve()
    directory=job_path.parent
    if job_path.name!='job.json' or job_path.stat().st_size>32768:
        raise ValueError('Invalid media job')
    job=json.loads(job_path.read_text(encoding='utf-8'))
    if not isinstance(job,dict) or set(job)!={'operation','options'} or not isinstance(job['options'],dict):
        raise ValueError('Invalid media job envelope')
    source=directory/'input.media'
    if not source.is_file() or source.is_symlink():
        raise ValueError('Invalid local media job source')
    try:
        if job['operation']=='video':
            from serve.video import sample_source
            options=job['options']
            if set(options)!={'start_s','end_s','sampling_fps','max_frames','mode','allow_empty_segment'}:
                raise ValueError('Invalid video job options')
            result=sample_source(source,options)
        elif job['operation']=='audio':
            from serve.asr.media import extract
            options=job['options']
            if set(options)!={'start_s','end_s','audio_track','channel'}:
                raise ValueError('Invalid audio job options')
            result=extract(source,**options)
            pcm=result.pop('pcm')
            if pcm is not None:
                from serve.media_jobs import MAX_PCM_BYTES
                if pcm.ndim!=1 or pcm.nbytes>MAX_PCM_BYTES:
                    raise ValueError('Extracted audio exceeds its PCM budget')
                pcm.astype('<f4',copy=False).tofile(directory/'audio.f32')
        else:
            raise ValueError('Unsupported media job operation')
        envelope={'result':result}
    except (ValueError,TimeoutError,InterruptedError) as error:
        envelope={'error':{'type':type(error).__name__,'message':str(error)}}
        # VideoWindowEmpty is a ValueError with the same public error message.
        if envelope['error']['type'] not in ('ValueError','TimeoutError','InterruptedError'):
            envelope['error']['type']='ValueError'
    output=json.dumps(envelope,ensure_ascii=False,allow_nan=False)
    from serve.media_jobs import MAX_RESULT_BYTES
    if len(output.encode('utf-8'))>(MAX_RESULT_BYTES if job['operation']=='video' else 1024*1024):
        raise ValueError('Media result exceeds its IPC budget')
    (directory/'result.json').write_text(output,encoding='utf-8')

if __name__=='__main__':
    main()
