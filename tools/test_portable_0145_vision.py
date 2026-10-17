"""Fresh portable imports use the upstream recommendation without changing saved choices."""
from pathlib import Path
import tempfile, unittest
from unittest import mock
import portable


class VisionImportPolicy(unittest.TestCase):
    def configure(self, vram, *, old=None, requested='auto', backend='cuda', selected=0, cards=None):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cfg=root/'strata-iq3_s.json';state=root/'portable-settings.json'
            model={'family':'qwen','model':'IQ3_S','gguf_dir':'models'}
            if old is not None:
                portable.save_json(cfg, {'args':['--native','main.gguf'],'vision':old,'api_key':'keep-fixture-key','port':8097})
            system={'data':str(root/'data'),'gpu':cards if cards is not None else [{'index':0,'vram_gb':vram}]}
            def setup():
                portable.save_json(cfg, {'args':['--native','main.gguf'],'backend':backend,'gpu':selected,'port':8080,'new_fixture':True})
                return 0
            def attach(config, delivery, mode, tokens):
                return {**config,'vision':{**(config.get('vision') or {}),'gpu':mode=='gpu'}}
            with mock.patch.object(portable,'ROOT',root),mock.patch.object(portable,'STATE',state), \
                 mock.patch.object(portable,'model_delivery',return_value=model), \
                 mock.patch.object(portable,'fingerprint',return_value=system), \
                 mock.patch.object(portable.upstream,'main',side_effect=setup), \
                 mock.patch.object(portable,'attach_vision',side_effect=attach) as encoder,mock.patch('sys.argv',['test']):
                code,_=portable.configure(root/'data',vision=requested)
                self.assertEqual(code,0)
                result=portable.read_json(cfg)
                if old is not None:
                    self.assertEqual(result['api_key'],'keep-fixture-key');self.assertEqual(result['port'],8097)
                return result,encoder.call_count

    def test_new_16gb_import_uses_cpu(self):
        self.assertFalse(self.configure(16)[0]['vision']['gpu'])

    def test_24gb_boundary_keeps_gpu(self):
        self.assertTrue(self.configure(24)[0]['vision']['gpu'])

    def test_saved_gpu_and_token_budget_stay_on_small_card(self):
        config,_=self.configure(16,old={'gpu':True,'max_tokens':1024})
        self.assertTrue(config['vision']['gpu']);self.assertEqual(config['vision']['max_tokens'],1024)

    def test_saved_cpu_stays_on_large_card(self):
        self.assertFalse(self.configure(32,old={'gpu':False})[0]['vision']['gpu'])

    def test_explicit_gpu_is_not_replaced_by_recommendation(self):
        self.assertTrue(self.configure(16,requested='gpu')[0]['vision']['gpu'])

    def test_selected_card_controls_recommendation(self):
        cards=[{'index':0,'vram_gb':32},{'index':1,'vram_gb':16}]
        self.assertFalse(self.configure(32,selected=[1,0],cards=cards)[0]['vision']['gpu'])

    def test_unknown_card_keeps_previous_default(self):
        self.assertTrue(self.configure(0,cards=[])[0]['vision']['gpu'])

    def test_windows_amd_auto_remains_text_only(self):
        config,calls=self.configure(16,backend='hip')
        self.assertNotIn('vision',config);self.assertEqual(calls,0)


if __name__=='__main__':
    unittest.main()
