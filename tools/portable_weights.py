"""Weight policy shared by package builder and updater. Only this exact encoder is allowed."""
VISION_PATH = 'vision/weights/mmproj-Qwen3.8-Flash-Next-BF16.gguf'
VISION_SIZE = 907543008
VISION_SHA256 = 'b1a82259702816a5330d7bd7607cd9676b11780e79ff7348c21103ff3ce49bd0'
MODEL_SUFFIXES = ('.gguf', '.safetensors', '.pt', '.pth', '.ckpt', '.onnx', '.bin')
RUNTIME_BINARY_DATA = {'data/expert-profile.bin', 'data/expert-profile-coder.bin',
                       'data/draft_vocab.bin', 'data/draft_vocab_en.bin',
                       'data/draft_vocab_fr.bin', 'data/draft_vocab_cyrillic.bin'}


def allowed_runtime_data(entry):
    # Small upstream expert routing statistics and token masks are metadata, not tensor weights.
    return entry['path'] in RUNTIME_BINARY_DATA and 0 < entry['size'] <= 1024**2


def weight_declaration(edition):
    vision = edition == 'VisionReady-NoMainModel'
    return {'main': False, 'mtp': False, 'vision': vision}


def validate_weights(manifest):
    edition = manifest.get('edition', 'Portable-NoModels')
    if edition not in ('Portable-NoModels', 'VisionReady-NoMainModel'):
        raise ValueError('Unknown package edition')
    expected = weight_declaration(edition)
    if manifest.get('models_included') is not expected['vision']:
        raise ValueError('Release must explicitly exclude main/MTP models and classify vision weights')
    if edition == 'VisionReady-NoMainModel' or 'weights' in manifest:
        declared = manifest.get('weights')
        if not isinstance(declared, dict) or set(declared) != set(expected) or any(declared[k] is not expected[k] for k in expected):
            raise ValueError('Invalid vision weight roles')
    entries = [entry for entry in manifest['files'] if entry['path'] == VISION_PATH]
    if expected['vision']:
        if len(entries) != 1 or entries[0]['size'] != VISION_SIZE or entries[0]['sha256'] != VISION_SHA256:
            raise ValueError('VisionReady must contain the pinned encoder with its exact hash')
    elif entries:
        raise ValueError('NoModels must not contain vision weights')
    return edition


def allowed_weight(entry, edition):
    return (edition == 'VisionReady-NoMainModel' and entry['path'] == VISION_PATH
            and entry['size'] == VISION_SIZE and entry['sha256'] == VISION_SHA256)
