"""Real parts, corruption, path rejection, and Windows offline reassembly."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from tools import portable_release_parts as parts


class Multipart(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='Strata 分卷 空格 ');self.addCleanup(self.temp.cleanup)
        self.archive=Path(self.temp.name)/'Strata-T8-0.2.6-t8.1-Windows-x64-VisionReady-NoMainModel.zip'
        self.data=bytes(range(256))*8;self.archive.write_bytes(self.data)
        self.digest=hashlib.sha256(self.data).hexdigest()
        self.record=parts.split(self.archive,part_size=1024)

    def test_parts_reconstruct_audited_archive_and_reject_changes(self):
        assets,hashes=parts.validate(self.archive,self.digest)
        self.assertEqual(len(assets),5)
        self.assertEqual(set(hashes),{p.name for p in assets})
        assets[0].write_bytes(b'x'*1024)
        with self.assertRaisesRegex(ValueError,'checksum'):parts.validate(self.archive,self.digest)

    def test_filename_and_script_are_bound(self):
        path=self.archive.with_name(parts.names(self.archive)[0])
        self.record['parts'][0]['file']='../external.zip'
        path.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError,'entry'):parts.validate(self.archive,self.digest)
        parts.split(self.archive,part_size=1024)
        self.archive.with_name(parts.names(self.archive)[1]).write_text('bad script')
        with self.assertRaisesRegex(ValueError,'script'):parts.validate(self.archive,self.digest)

    @unittest.skipUnless(os.name=='nt','Windows PowerShell helper')
    def test_real_offline_powershell_join_in_unicode_directory(self):
        self.archive.unlink()
        script=self.archive.with_name(parts.names(self.archive)[1])
        result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],
                              capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.archive.read_bytes(),self.data)
        again=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],
                             capture_output=True,timeout=30)
        self.assertNotEqual(again.returncode,0)
        self.assertEqual(self.archive.read_bytes(),self.data)


if __name__=='__main__':unittest.main()
