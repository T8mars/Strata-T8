"""Give bundled native executables UTF-8 argv on Windows 10 1903 and later.

Keep the vendor manifest (including UAC/dependencies) and other PE resources.
This changes the application's code page, never the machine's locale.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

ASM1 = 'urn:schemas-microsoft-com:asm.v1'
ASM3 = 'urn:schemas-microsoft-com:asm.v3'
WIN2019 = 'http://schemas.microsoft.com/SMI/2019/WindowsSettings'
ACTIVE = '{' + WIN2019 + '}activeCodePage'
DEFAULT = f'<assembly xmlns="{ASM1}" manifestVersion="1.0"/>'


def utf8_manifest(data):
    root = ET.fromstring(data, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    existing = list(root.iter(ACTIVE))
    if len(existing) > 1:
        raise ValueError('Ambiguous activeCodePage entries')
    if existing and existing[0].text == 'UTF-8':
        return data
    app = root.find('{' + ASM3 + '}application')
    if app is None:
        app = ET.SubElement(root, '{' + ASM3 + '}application')
    settings = app.find('{' + ASM3 + '}windowsSettings')
    if settings is None:
        settings = ET.SubElement(app, '{' + ASM3 + '}windowsSettings')
    entry = existing[0] if existing else ET.SubElement(settings, ACTIVE)
    entry.text = 'UTF-8'
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def kernel_api():
    if os.name != 'nt':
        raise OSError('PE manifest updates require Windows')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer, uint = ctypes.c_void_p, ctypes.c_uint32
    for name, args, result in [
        ('LoadLibraryExW', [ctypes.c_wchar_p, pointer, uint], pointer),
        ('FreeLibrary', [pointer], ctypes.c_int),
        ('FindResourceExW', [pointer, pointer, pointer, ctypes.c_uint16], pointer),
        ('SizeofResource', [pointer, pointer], uint),
        ('LoadResource', [pointer, pointer], pointer),
        ('LockResource', [pointer], pointer),
        ('BeginUpdateResourceW', [ctypes.c_wchar_p, ctypes.c_int], pointer),
        ('UpdateResourceW', [pointer, pointer, pointer, ctypes.c_uint16, pointer, uint], ctypes.c_int),
        ('EndUpdateResourceW', [pointer, ctypes.c_int], ctypes.c_int),
    ]:
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def read_manifests(path):
    api = kernel_api()
    module = api.LoadLibraryExW(str(Path(path).resolve()), None, 2 | 0x20)
    if not module:
        raise ctypes.WinError(ctypes.get_last_error())
    languages = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_uint16, ctypes.c_ssize_t)
    callback = callback_type(lambda module, kind, name, lang, param: languages.append(lang) or 1)
    api.EnumResourceLanguagesW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                          callback_type, ctypes.c_ssize_t]
    api.EnumResourceLanguagesW.restype = ctypes.c_int
    try:
        if not api.EnumResourceLanguagesW(module, 24, 1, callback, 0):
            error = ctypes.get_last_error()
            if error in (1812, 1813, 1814, 1815):
                return {}
            raise ctypes.WinError(error)
        result = {}
        for lang in languages:
            resource = api.FindResourceExW(module, 24, 1, lang)
            size = api.SizeofResource(module, resource)
            address = api.LockResource(api.LoadResource(module, resource))
            if not address or not size:
                raise ctypes.WinError(ctypes.get_last_error())
            result[lang] = ctypes.string_at(address, size)
        return result
    finally:
        api.FreeLibrary(module)


def signed_pe(path):
    with Path(path).open('rb') as stream:
        stream.seek(0x3c)
        pe = struct.unpack('<I', stream.read(4))[0]
        stream.seek(pe)
        if stream.read(4) != b'PE\0\0':
            raise ValueError('Not a PE executable')
        optional = pe + 24
        stream.seek(optional)
        magic = struct.unpack('<H', stream.read(2))[0]
        directory = {0x10b: 96, 0x20b: 112}.get(magic)
        if directory is None:
            raise ValueError('Unknown PE format')
        stream.seek(optional + directory + 4 * 8)
        offset, size = struct.unpack('<II', stream.read(8))
        return bool(offset or size)


def write_manifests(path, manifests):
    api = kernel_api()
    update = api.BeginUpdateResourceW(str(Path(path).resolve()), False)
    if not update:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        for lang, data in manifests.items():
            buffer = ctypes.create_string_buffer(data)
            if not api.UpdateResourceW(update, 24, 1, lang, buffer, len(data)):
                raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        api.EndUpdateResourceW(update, True)
        raise
    if not api.EndUpdateResourceW(update, False):
        raise ctypes.WinError(ctypes.get_last_error())


def patch_executable(path):
    path = Path(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    original = read_manifests(path)
    patched = {lang: utf8_manifest(data) for lang, data in (original or {0: DEFAULT.encode()}).items()}
    if patched != original:
        if signed_pe(path):
            raise ValueError('Refusing to invalidate a signed native executable; rebuild it with a UTF-8 manifest')
        write_manifests(path, patched)
    if read_manifests(path) != patched:
        raise ValueError('UTF-8 manifest verification failed')
    return {'sha256_before': before, 'sha256_after': hashlib.sha256(path.read_bytes()).hexdigest(),
            'manifest': 'activeCodePage=UTF-8', 'minimum_windows': '10 1903'}


def patch_engine(directory):
    directory = Path(directory)
    path = directory/'BUILD.json'
    build = json.loads(path.read_text(encoding='utf-8'))
    patches = build.setdefault('portable_patches', {})
    changed = False
    for name, key in [('strata.exe', 'engine_utf8'), ('strata-vision.exe', 'vision_utf8')]:
        helper = directory/name
        if not helper.exists():
            continue
        result = patch_executable(helper)
        previous = patches.get(key, {})
        if previous.get('sha256_after') != result['sha256_after']:
            patches[key] = result
            changed = True
    if changed:
        path.write_text(json.dumps(build, indent=2)+'\n', encoding='utf-8')
    return patches
