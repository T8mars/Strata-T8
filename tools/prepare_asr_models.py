"""Explicit, resumable official GGUF ASR download. Normal startup never calls it."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.asr.config import MODELS, PROJECTOR
from tools.portable_download import download

MS_REV = '2ba549d4ba1fd99a8b7946107f580c15ed8d7900'
HF_REV = '86ff0251cb9f456b63aeef5f80137f104e22869a'
MIRROR_REV = '2223a55593a85bbaf2b12d58c410da67a8822608'
VAD = [('fireredvad_stream_vad_with_cache.onnx',2306042,'b3c97836130dc34fc32d56fab551e88cf9454511de2b3c250a5e6578dee74b93'),
       ('cmvn.ark',1311,'c87f6f13edf0f0ec7535ddfc9cc3387d9268cb234b70182d566c5e2edf3ca473'),
       ('LICENSE',11357,'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4')]
LICENSES = [('MODEL_LICENSE',11132,'064483c5ba1dc20907038108da4f45d5b37cd0a41a351c8a8bb98ba1af48c505'),
            ('MODEL_LICENSE_zh',7789,'6ed2e9946bd0b0185a6c690565cf2f8de23044a513be6e1ec7c97e6995ce241b')]


def fetch(urls, target, size, digest):
    errors = []
    for url in urls:
        try:
            download(url, target, size, digest, workers=2, chunk_mib=16)
            return
        except (OSError, ValueError, RuntimeError) as error:
            errors.append(str(error))
    raise RuntimeError('ASR download failed: '+'; '.join(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', required=True, type=Path)
    parser.add_argument('--vad-dir', type=Path)
    parser.add_argument('--quantization', choices=list(MODELS), default='Q4_K_M')
    args = parser.parse_args()
    destination = args.model_dir.resolve()
    for name,size,digest in (MODELS[args.quantization], PROJECTOR):
        urls = [f'https://www.modelscope.cn/api/v1/models/netease-youdao/Confucius4-R2T2-GGUF/repo?Revision={MS_REV}&FilePath={name}',
                f'https://hf-mirror.com/netease-youdao/Confucius4-R2T2-GGUF/resolve/{HF_REV}/{name}',
                f'https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF/resolve/{HF_REV}/{name}']
        fetch(urls, destination/name, size, digest)
    for relative, target, entries in (('',destination,LICENSES), ('FireRedVAD-ONNX/',args.vad_dir or destination/'FireRedVAD-ONNX',VAD)):
        for name,size,digest in entries:
            fetch([f'https://{host}/t8star/Confucius-R2t2-Comfy/resolve/{MIRROR_REV}/{relative}{name}'
                   for host in ('hf-mirror.com','huggingface.co')], target/name, size, digest)
    print(f'Confucius {args.quantization} and audio projector verified: {destination}')


if __name__ == '__main__':
    main()
