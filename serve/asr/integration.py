"""Lazy server adapter; speech never changes the default text/vision protocol."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import threading
from .manager import AsrManager

_creation_lock = threading.Lock()


def manager(service, root):
    with _creation_lock:
        if getattr(service,'asr_manager',None) is None:
            service.asr_manager = AsrManager(Path(root))
        return service.asr_manager


@contextmanager
def gpu_scope(service):
    if getattr(service.engine,'batch',0):
        raise RuntimeError('CUDA ASR requires serial Strata serving; use CPU transcription with parallel serving')
    if not service.fifo.acquire(blocking=False):
        raise RuntimeError('Strata is busy; CUDA transcription cannot interrupt the current request')
    try:
        with service.status_lock:
            if service.status.get('busy') or service.status.get('queued'):
                raise RuntimeError('Strata is busy; wait for the current requests before CUDA transcription')
        if not hasattr(service.engine,'unload'):
            raise RuntimeError('This engine cannot release its GPU for ASR; select CPU transcription')
        # The server owns these engine processes. External services are never searched or terminated.
        service.engine.unload()
        if service.vision is not None:
            service.vision.unload()
        yield
    finally:
        service.fifo.release()
