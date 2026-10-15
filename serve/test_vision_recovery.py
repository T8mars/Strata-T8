"""A companion encoder can recover without admitting an already-loaded model again."""
from pathlib import Path
from types import SimpleNamespace
import threading,unittest
from unittest import mock
from serve import server
from serve.frontend import ChatTemplate

class VisionRecovery(unittest.TestCase):
    def make(self):
        tok=server.ByteTokenizer()
        engine=server.MockEngine(tok,'ok')
        engine.alive=mock.Mock(return_value=True)
        engine.restart=mock.Mock()
        engine.close=mock.Mock()
        vision=SimpleNamespace(elastic=False,alive=mock.Mock(return_value=False),restart=mock.Mock())
        vision.restart.side_effect=lambda **kwargs:setattr(vision.alive,'return_value',True)
        service=server.Service(engine,tok,ChatTemplate(Path(__file__).with_name('chat_template.jinja')),vision=vision)
        service.min_free_vram_mib=12288
        service.vram_free_wait_s=0
        service.free_vram_mib=mock.Mock(return_value=1024)
        return service,engine,vision

    def test_loaded_model_recovers_encoder_without_hook_or_full_vram_admission(self):
        service,engine,vision=self.make()
        service.before_load=['must-not-run']
        engine.last_prompt=[11,22,33]
        cancel=threading.Event()
        with mock.patch.object(server.subprocess,'run') as hook:
            service.ensure_loaded(cancel)
        hook.assert_not_called()
        service.free_vram_mib.assert_not_called()
        vision.restart.assert_called_once_with(cancel=cancel)
        engine.restart.assert_not_called()
        engine.close.assert_not_called()
        self.assertEqual(engine.last_prompt,[11,22,33])

    def test_healthy_default_does_not_restart_or_inspect_free_vram(self):
        service,engine,vision=self.make();vision.alive.return_value=True
        service.ensure_loaded()
        vision.restart.assert_not_called()
        engine.restart.assert_not_called()
        service.free_vram_mib.assert_not_called()

    def test_encoder_start_failure_keeps_main_model_and_can_be_retried(self):
        service,engine,vision=self.make()
        vision.restart.side_effect=server.EngineStarting('encoder not ready')
        with self.assertRaises(server.EngineStarting):service.ensure_loaded()
        self.assertTrue(service.loaded())
        engine.restart.assert_not_called();engine.close.assert_not_called()
        vision.restart.side_effect=lambda **kwargs:setattr(vision.alive,'return_value',True)
        service.ensure_loaded()
        self.assertEqual(vision.restart.call_count,2)

    def test_cold_model_still_obeys_full_vram_admission(self):
        service,engine,vision=self.make();engine.alive.return_value=False
        with self.assertRaises(server.GpuBusy):service.ensure_loaded()
        vision.restart.assert_not_called();engine.restart.assert_not_called()
        service.free_vram_mib.assert_called_once()

    def test_main_engine_dying_during_recovery_reenters_full_admission(self):
        service,engine,vision=self.make()
        def restart(**kwargs):
            vision.alive.return_value=True
            engine.alive.return_value=False
        vision.restart.side_effect=restart
        with self.assertRaises(server.GpuBusy):service.ensure_loaded()
        service.free_vram_mib.assert_called_once();engine.restart.assert_not_called()

    def test_cancelled_recovery_does_not_start_a_child(self):
        service,engine,vision=self.make();cancel=threading.Event();cancel.set()
        with self.assertRaises(server.RequestCancelled):service.ensure_loaded(cancel)
        vision.restart.assert_not_called();engine.restart.assert_not_called()

if __name__=='__main__':unittest.main()
