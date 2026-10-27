"""Native local packs are inspected without copying weights or loading a model."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import portable
from tools import portable_deepseek as ds
from tools.dsv41.pack_gguf import raw_engram_types


class RawEngram(unittest.TestCase):
    def test_explicit_layout_pairs_and_bounds_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'raw.gguf';p.write_bytes(bytes(528))
            g=SimpleNamespace(path=p,data_start=0,kv={'ds1017.recipe':'native-mxfp4-q8-dense-raw-engram-v1'},
                tensors=[('blk.1.engram_embd.weight',24,[256,2],0),
                         ('blk.1.engram_embd_scale.weight',24,[8,2],512),
                         ('engram_primes.weight',27,[2],0)])
            with self.assertRaises(SystemExit):raw_engram_types([g])
            types=raw_engram_types([g],True)
            self.assertEqual(types['blk.1.engram_embd.weight'],('F8_E4M3',1,1))
            self.assertEqual(len(types),2)
            for tensors in (g.tensors[:1], g.tensors+g.tensors[:1],
                            [g.tensors[0],('blk.1.engram_embd_scale.weight',24,[8,3],512)]):
                bad=copy.copy(g);bad.tensors=tensors
                with self.subTest(tensors=tensors),self.assertRaises(SystemExit):raw_engram_types([bad],True)
            p.write_bytes(bytes(527))
            with self.assertRaises(SystemExit):raw_engram_types([g],True)


class HardwarePreflight(unittest.TestCase):
    def test_selected_gpu_driver_memory_and_cuda_arch_are_checked(self):
        card = {'index': 1, 'arch': 89, 'vram_gb': 16}
        machine = SimpleNamespace(ROOT=Path('fixture'),
            read_json=mock.Mock(return_value={'archs':[86,120], 'ptx':True}),
            upstream=SimpleNamespace(gpus=mock.Mock(return_value=[card]),
                arch_numbers=lambda values: values, driver_major=mock.Mock(return_value=616),
                ram_gb=mock.Mock(return_value=128)))
        ds.check_hardware(machine, {'args':['--gpu','1']})
        ds.check_hardware(machine, {'args':['--gpu=1']})
        with self.assertRaisesRegex(RuntimeError, 'selected NVIDIA'):
            ds.check_hardware(machine, {'args':[]})
        for field, value, message in [('arch',75,'compatible CUDA'), ('vram_gb',8,'12 GB')]:
            with self.subTest(field=field), mock.patch.dict(card, {field:value}):
                with self.assertRaisesRegex(RuntimeError, message):
                    ds.check_hardware(machine, {'args':['--gpu','1']})
        machine.upstream.driver_major.return_value = 579
        with self.assertRaisesRegex(RuntimeError, '580'):
            ds.check_hardware(machine, {'args':['--gpu','1']})
        machine.upstream.driver_major.return_value = 616
        machine.upstream.ram_gb.return_value = 32
        with self.assertRaisesRegex(RuntimeError, '46 GB'):
            ds.check_hardware(machine, {'args':['--gpu','1']})


class PortablePack(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.pack=self.root/'model';self.pack.mkdir()
        hardware = mock.patch.object(ds, 'check_hardware')
        hardware.start(); self.addCleanup(hardware.stop)
        (self.pack/'ds4.txt').write_text('hidden_size 5120\nnum_hidden_layers 40\nvocab_size 129280\n',encoding='utf-8')
        (self.pack/'weights.gguf').write_bytes(b'GGUF'+bytes(28))
        (self.pack/'tensors.txt').write_text('file 0 weights.gguf\nt embed.weight F32 0 0 32 2 4\n',encoding='utf-8')
        (self.pack/'tokenizer').mkdir()
        for name in ('engram.bin','tokenizer/vocab.json','tokenizer/merges.txt','tokenizer/token_type.json',
                     'tokenizer/tokenizer.json','tokenizer/chat_template.jinja'):
            (self.pack/name).write_bytes(b'fixture')

    def test_missing_or_truncated_weights_are_not_advertised_as_ready(self):
        self.assertEqual(ds.inspect_pack(self.pack)['family'],'deepseek41')
        (self.pack/'weights.gguf').write_bytes(b'GGUF')
        with self.assertRaisesRegex(RuntimeError,'Truncated'):ds.inspect_pack(self.pack)
        (self.pack/'weights.gguf').unlink()
        with self.assertRaisesRegex(RuntimeError,'missing'):ds.inspect_pack(self.pack)

    def test_configuration_preserves_user_settings_and_full_quality(self):
        (self.root/'engine-dsv41').mkdir();(self.root/'engine-dsv41/strata-dsv41.exe').write_bytes(b'fixture')
        with mock.patch.object(portable,'ROOT',self.root),mock.patch.object(portable,'STATE',self.root/'portable-settings.json'),\
                mock.patch.object(portable,'fingerprint',return_value={'fixture':True}):
            result,cfg=ds.configure(portable,self.pack,32768)
            self.assertEqual(result,0)
            current=portable.read_config(cfg)
            self.assertTrue(portable.context_matches(current,8192))
            self.assertEqual(current['args'][current['args'].index('--experts')+1],'6')
            current.update(host='0.0.0.0',api_key='test-only',port=9123)
            current['args']+=['--cpu-threads','7'];portable.save_json(cfg,current)
            ds.configure(portable,self.pack,4096)
            final=portable.read_config(cfg)
            self.assertEqual((final['host'],final['api_key'],final['port']),('0.0.0.0','test-only',9123))
            self.assertIn('7',final['args'])
            ds.configure(portable,self.pack,4096,speed='faster')
            faster=portable.read_config(cfg)
            self.assertEqual(faster['args'][faster['args'].index('--experts')+1],'5')
            ds.configure(portable,self.pack,4096)
            resumed=portable.read_config(cfg)['args']
            self.assertEqual(resumed[resumed.index('--experts')+1],'5')
            self.assertIn('--cpu-threads',resumed)
            self.assertEqual(portable.model_api_id(portable.model_delivery(self.pack)),'deepseek-v4.1-flash-1017')
            with self.assertRaisesRegex(RuntimeError,'text-only'):ds.configure(portable,self.pack,vision='gpu')

    def test_state_failure_rolls_back_the_new_configuration(self):
        (self.root/'engine-dsv41').mkdir();(self.root/'engine-dsv41/strata-dsv41.exe').write_bytes(b'fixture')
        state=self.root/'portable-settings.json';state.write_text('{"keep":1}')
        with mock.patch.object(portable,'ROOT',self.root),mock.patch.object(portable,'STATE',state),\
                mock.patch.object(portable,'fingerprint',side_effect=OSError('disk')):
            with self.assertRaises(OSError):ds.configure(portable,self.pack)
        self.assertEqual(state.read_text(),'{"keep":1}')
        self.assertEqual(list(self.root.glob('strata-deepseek41-*.json')),[])


if __name__=='__main__':unittest.main()
