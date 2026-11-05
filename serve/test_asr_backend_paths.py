"""The pinned ASR registry must load Unicode paths without changing the system locale."""
import ctypes
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
from serve.asr.confucius import native


class UnicodeBackend(unittest.TestCase):
    def plugin(self, version=2, score=1):
        header=ctypes.c_int(version)
        return SimpleNamespace(header=header, ggml_backend_score=mock.Mock(return_value=score),
                               ggml_backend_init=mock.Mock(return_value=ctypes.addressof(header)))

    def test_loads_the_real_unicode_name_and_keeps_the_supported_library_alive(self):
        plugin=self.plugin();registry=SimpleNamespace(ggml_backend_register=mock.Mock())
        path=Path('模型 😀')/'ggml-cpu-x64.dll'
        with mock.patch.object(ctypes,'WinDLL',return_value=plugin,create=True) as loader, \
             mock.patch.object(native,'_DLL_HANDLES',[]):
            self.assertTrue(native._load_unicode_backend(registry,path))
            loader.assert_called_once_with(str(path))
            registry.ggml_backend_register.assert_called_once_with(ctypes.addressof(plugin.header))
            self.assertEqual(native._DLL_HANDLES,[plugin])

    def test_unsupported_instruction_set_is_not_initialized_or_registered(self):
        plugin=self.plugin(score=0);registry=SimpleNamespace(ggml_backend_register=mock.Mock())
        with mock.patch.object(ctypes,'WinDLL',return_value=plugin,create=True):
            self.assertFalse(native._load_unicode_backend(registry,Path('中文/ggml-cpu.dll')))
        plugin.ggml_backend_init.assert_not_called()
        registry.ggml_backend_register.assert_not_called()

    def test_incompatible_api_is_refused_before_registration(self):
        plugin=self.plugin(version=999);registry=SimpleNamespace(ggml_backend_register=mock.Mock())
        with mock.patch.object(ctypes,'WinDLL',return_value=plugin,create=True), self.assertRaisesRegex(RuntimeError,'API version'):
            native._load_unicode_backend(registry,Path('中文/ggml-cpu.dll'))
        registry.ggml_backend_register.assert_not_called()


if __name__=='__main__':unittest.main()
