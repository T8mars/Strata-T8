"""Normal startup may prepare local data but cannot fetch MTP in a child process."""
import unittest
from unittest import mock
import portable


class OfflineLauncher(unittest.TestCase):
    def guards(self):
        names = ['settings_path', 'other_installs', 'data_folder', 'get_llama_cpp', 'pip_install', 'download', 'get_prebuilt_hip', 'run']
        return mock.patch.multiple(portable.upstream, **{name: mock.DEFAULT for name in names})

    def test_corrupt_delivery_cannot_trigger_child_model_download(self):
        with self.guards() as originals, mock.patch('urllib.request.urlopen'):
            portable.isolate_setup()
            with self.assertRaisesRegex(RuntimeError, 'PREPARE-MODEL'):
                portable.upstream.run(['python', str(portable.ROOT/'tools/mtp_fetch.py'), 'fetch', '--out', 'missing'])
            originals['run'].assert_not_called()

    def test_local_preparation_is_allowed_and_network_guard_is_restored(self):
        with self.guards() as originals, mock.patch('urllib.request.urlopen'):
            originals['run'].return_value = 'prepared'
            portable.isolate_setup()
            command = ['python', str(portable.ROOT/'tools/iq_pack.py'), '--gguf', 'local.gguf']
            self.assertEqual(portable.upstream.run(command, check=True), 'prepared')
            originals['run'].assert_called_once_with(command, check=True)
            with self.assertRaisesRegex(RuntimeError, 'Offline package'):
                portable.upstream.download('https://example.invalid/model', 'model.gguf')


if __name__ == '__main__': unittest.main()
