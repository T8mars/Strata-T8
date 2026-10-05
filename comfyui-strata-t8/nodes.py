"""Stable node IDs and shared synchronous execution for V1/V3 ComfyUI."""
import json
from pathlib import Path
from . import core

CONNECTION = 'STRATA_CONNECTION'
TEXT_LIST = 'STRATA_TEXT_LIST'
STORY_SCHEMA = {
    'type': 'object', 'required': ['shots'], 'additionalProperties': False,
    'properties': {'shots': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': {
        'type': 'object', 'required': ['subject', 'action', 'scene', 'camera', 'duration', 'prompt'],
        'additionalProperties': False, 'properties': {
            **{k: {'type': 'string', 'minLength': 1} for k in ['subject', 'action', 'scene', 'camera', 'prompt']},
            'duration': {'type': 'number', 'minimum': .1, 'maximum': 600}}}}}}
PRESETS = {
    'expand': 'Expand the input into a clear, concrete prompt. Preserve the subject and intent. Output only the prompt.',
    'translate': 'Translate the input accurately to English. Preserve technical terms. Output only the translation.',
    'rewrite': 'Rewrite the input for clarity and specificity, preserving all constraints. Output only the rewrite.',
    'image': 'Write an English image-generation prompt: subject, action, composition, setting, lighting, visual style. Preserve intent. Output only the prompt.',
    'video': 'Write an English video-generation prompt with visible actions, camera movement, temporal progression, and continuity constraints. Output only the prompt.',
    'positive_negative': 'Produce JSON with positive and negative strings for image generation. Positive: desired concrete visible content. Negative: unwanted artifacts. Preserve the input intent.'}
IMAGE_PRESETS = {
    'caption': 'Describe only visible content in this image: subjects, actions, composition, setting, light and colors.',
    'reverse_prompt': 'Write an English image-generation prompt to recreate visible subjects, composition, style, lighting and camera view. Output only the prompt.',
    'ocr': 'Transcribe readable text in reading order. Preserve original language and line breaks. Mark illegible text; do not invent characters.',
    'question': 'Answer the user question using the image. Separate visible evidence from inference.',
    'evaluate': 'Evaluate this image under the user rubric. State visible evidence, uncertainties and concrete improvements. Any score is your subjective judgment.'}


def sampling():
    return {'max_tokens': ('INT', {'default': 1024, 'min': 1, 'max': 131072}),
            'temperature': ('FLOAT', {'default': .7, 'min': 0, 'max': 2, 'step': .05}),
            'top_p': ('FLOAT', {'default': .95, 'min': .01, 'max': 1, 'step': .01}),
            'top_k': ('INT', {'default': 20, 'min': 1, 'max': 64}),
            'seed': ('INT', {'default': 0, 'min': 0, 'max': 2147483647}),
            'reasoning_effort': (['none', 'low', 'medium', 'high', 'xhigh'],),
            'refresh': ('INT', {'default': 0, 'min': 0, 'max': 2147483647})}


def request(prompt, system='', history='[]', **options):
    messages = json.loads(history)
    if not isinstance(messages, list) or len(messages) > 256 or any(not isinstance(m, dict) or m.get('role') not in ('system', 'user', 'assistant') or not isinstance(m.get('content'), str) for m in messages):
        raise core.StrataError('History must be a JSON array of at most 256 role/content text messages')
    messages = ([{'role': 'system', 'content': system}] if system else []) + messages + [{'role': 'user', 'content': prompt}]
    options.pop('refresh', None)
    if not 1 <= options.get('top_k', 20) <= 64 or not 0 < options.get('top_p', .95) <= 1:
        raise core.StrataError('Sampling requires top_k 1..64 and top_p above 0 through 1')
    return {'messages': messages, **options}


def check_schema(schema):
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(schema)
    # No HTTP/file reference retrieval in schemas supplied by a workflow.
    def local_refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('$ref', '$dynamicRef') and not child.startswith('#'):
                    raise core.StrataError('JSON Schema references must be local fragments')
                local_refs(child)
        elif isinstance(value, list):
            for child in value:
                local_refs(child)
    local_refs(schema)


def validated(text, schema):
    from jsonschema import Draft202012Validator
    check_schema(schema)
    value = json.loads(text)
    Draft202012Validator(schema).validate(value)
    return value


class Base:
    CATEGORY = 'Strata-T8'
    FUNCTION = 'run'


class StrataConnection(Base):
    RETURN_TYPES, RETURN_NAMES = (CONNECTION,), ('connection',)

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'profile': (core.profiles(),)}}

    @classmethod
    def IS_CHANGED(cls, profile):
        path = core.profile_path(profile)
        return path.stat().st_mtime_ns if path.exists() else 'missing'

    def run(self, profile):
        core.read_profile(profile)
        return (core.Connection(profile),)


class StrataText(Base):
    RETURN_TYPES, RETURN_NAMES = ('STRING', 'STRING', 'STRING'), ('text', 'reasoning', 'usage_json')

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'prompt': ('STRING', {'multiline': True}),
                             'system': ('STRING', {'multiline': True, 'default': 'You are a helpful assistant.'}),
                             'history': ('STRING', {'multiline': True, 'default': '[]'}), **sampling()}}

    def run(self, connection, prompt, system='', history='[]', **options):
        return core.generate(connection, [request(prompt, system, history, **options)])[0]


class StrataPrompt(Base):
    RETURN_TYPES, RETURN_NAMES = ('STRING', 'STRING', 'STRING'), ('positive', 'negative', 'usage_json')

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'text': ('STRING', {'multiline': True}),
                             'task': (list(PRESETS),), 'template': ('STRING', {'multiline': True, 'default': ''}), **sampling()}}

    def run(self, connection, text, task='image', template='', **options):
        req = request(text, template.strip() or PRESETS[task], **options)
        schema = {'type': 'object', 'required': ['positive', 'negative'], 'additionalProperties': False,
                  'properties': {'positive': {'type': 'string'}, 'negative': {'type': 'string'}}}
        if task == 'positive_negative':
            req['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'prompts', 'schema': schema}}
        output, _, usage = core.generate(connection, [req])[0]
        if task == 'positive_negative':
            data = validated(output, schema)
            return data['positive'], data['negative'], usage
        return output, '', usage


class StrataStructured(Base):
    RETURN_TYPES = ('STRING', 'STRING', TEXT_LIST, 'STRING')
    RETURN_NAMES = ('json', 'shot_prompts', 'text_list', 'usage_json')
    OUTPUT_IS_LIST = (False, True, False, False)

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'prompt': ('STRING', {'multiline': True}),
                             'schema': ('STRING', {'multiline': True, 'default': json.dumps(STORY_SCHEMA)}),
                             'system': ('STRING', {'multiline': True, 'default': 'Convert the story into concrete sequential shots. Each prompt describes one visible shot. Return the requested JSON.'}),
                             'repair_attempts': ('INT', {'default': 0, 'min': 0, 'max': 2}), **sampling()}}

    def run(self, connection, prompt, schema='', system='', repair_attempts=0, **options):
        schema = json.loads(schema) if schema else STORY_SCHEMA
        # Validate schema before contacting/loading a model.
        check_schema(schema)
        req = request(prompt, system, **options)
        req['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'strata_output', 'schema': schema}}
        for attempt in range(repair_attempts+1):
            try:
                text, _, usage = core.generate(connection, [req])[0]
                value = validated(text, schema)
                break
            except (core.StrataError, ValueError) as error:
                if attempt == repair_attempts or 'structured_output_failed' not in str(error):
                    raise
                req['messages'][0]['content'] += '\nReturn valid JSON only, matching every required field and its type.'
        prompts = [shot['prompt'] for shot in value.get('shots', [])] if isinstance(value, dict) and isinstance(value.get('shots'), list) and all(isinstance(s, dict) and isinstance(s.get('prompt'), str) for s in value['shots']) else []
        return json.dumps(value, ensure_ascii=False), prompts, prompts, usage


def pointer(value, path):
    if not path:
        return value
    if not path.startswith('/'):
        raise core.StrataError('Use a JSON Pointer, for example /shots/0/prompt')
    for component in path[1:].split('/'):
        key = component.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def as_text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class StrataExtract(Base):
    RETURN_TYPES, RETURN_NAMES = ('STRING', 'STRING', TEXT_LIST), ('value', 'items', 'text_list')
    OUTPUT_IS_LIST = (False, True, False)

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'json_text': ('STRING', {'multiline': True, 'forceInput': True}),
                             'pointer': ('STRING', {'default': '/shots'}),
                             'item_field': ('STRING', {'default': 'prompt'}),
                             'expected_type': (['any', 'string', 'number', 'array', 'object', 'boolean'],)}}

    def run(self, json_text, pointer='', item_field='', expected_type='any'):
        value = globals()['pointer'](json.loads(json_text), pointer)
        types = {'string': str, 'number': (int, float), 'array': list, 'object': dict, 'boolean': bool}
        if expected_type != 'any' and (not isinstance(value, types[expected_type]) or (expected_type == 'number' and isinstance(value, bool))):
            raise core.StrataError(f'Extracted value is not {expected_type}')
        items = value if isinstance(value, list) else [value]
        if item_field:
            items = [item[item_field] for item in items]
        items = [as_text(item) for item in items]
        return as_text(value), items, items


class StrataNumber(Base):
    RETURN_TYPES, RETURN_NAMES = ('FLOAT',), ('number',)

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'json_text': ('STRING', {'forceInput': True}), 'pointer': ('STRING', {'default': '/shots/0/duration'})}}

    def run(self, json_text, pointer):
        value = globals()['pointer'](json.loads(json_text), pointer)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise core.StrataError('The selected JSON field must be a number')
        return (float(value),)


class StrataImage(Base):
    RETURN_TYPES, RETURN_NAMES = ('STRING', 'STRING', 'STRING'), ('text', 'reasoning', 'usage_json')

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'images': ('IMAGE',), 'task': (list(IMAGE_PRESETS),),
                             'question': ('STRING', {'multiline': True, 'default': ''}),
                             'schema': ('STRING', {'multiline': True, 'default': ''}),
                             'max_pixels': ('INT', {'default': 1048576, 'min': 65536, 'max': 4194304}), **sampling()}}

    def run(self, connection, images, task='caption', question='', schema='', max_pixels=1048576, **options):
        encoded = core.encode_images(images, max_pixels=max_pixels)
        req = request('', IMAGE_PRESETS[task], **options)
        req['messages'][-1]['content'] = [{'type': 'text', 'text': question or IMAGE_PRESETS[task]},
                                       *({'type': 'image_url', 'image_url': {'url': image}} for image in encoded)]
        parsed = json.loads(schema) if schema.strip() else None
        if parsed is not None:
            check_schema(parsed)
            req['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'image_analysis', 'schema': parsed}}
        result = core.generate(connection, [req])[0]
        if parsed is not None:
            validated(result[0], parsed)
        return result


class StrataBatch(Base):
    RETURN_TYPES, RETURN_NAMES = ('STRING', 'STRING', TEXT_LIST), ('results', 'paired_json', 'text_list')
    OUTPUT_IS_LIST = (True, False, False)

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'texts': (TEXT_LIST,),
                             'system': ('STRING', {'multiline': True, 'default': 'Rewrite this into an English image-generation prompt. Output only the prompt.'}), **sampling()}}

    def run(self, connection, texts, system='', **options):
        if not isinstance(texts, list) or not 1 <= len(texts) <= 64 or any(not isinstance(t, str) for t in texts):
            raise core.StrataError('Provide a list of 1–64 text strings')
        answers = core.generate(connection, [request(text, system, **options) for text in texts])
        outputs = [a[0] for a in answers]
        paired = [{'index': i, 'input': text, 'output': a[0], 'reasoning': a[1], 'usage': json.loads(a[2])} for i, (text, a) in enumerate(zip(texts, answers))]
        return outputs, json.dumps(paired, ensure_ascii=False), outputs


class StrataImageBatch(Base):
    RETURN_TYPES, RETURN_NAMES = StrataBatch.RETURN_TYPES, StrataBatch.RETURN_NAMES
    OUTPUT_IS_LIST = StrataBatch.OUTPUT_IS_LIST

    @classmethod
    def INPUT_TYPES(cls):
        definition = StrataImage.INPUT_TYPES()
        definition['required'].pop('schema')
        return definition

    def run(self, connection, images, task='caption', question='', max_pixels=1048576, **options):
        encoded = core.encode_images(images, max_pixels=max_pixels)
        requests = []
        for image in encoded:
            req = request('', IMAGE_PRESETS[task], **options)
            req['messages'][-1]['content'] = [{'type': 'text', 'text': question or IMAGE_PRESETS[task]},
                                           {'type': 'image_url', 'image_url': {'url': image}}]
            requests.append(req)
        answers = core.generate(connection, requests)
        outputs = [a[0] for a in answers]
        return outputs, json.dumps([{'index': i, 'output': a[0], 'usage': json.loads(a[2])} for i, a in enumerate(answers)], ensure_ascii=False), outputs


class StrataControl(Base):
    RETURN_TYPES, RETURN_NAMES = (CONNECTION, 'STRING', 'STRING', 'IMAGE'), ('connection', 'status_json', 'text', 'images')
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'action': (['status', 'start', 'load', 'unload', 'stop'],)},
                'optional': {'text': ('STRING', {'forceInput': True}), 'images': ('IMAGE',)}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float('nan')

    def run(self, connection, action='status', text='', images=None):
        status = core.control(connection, action)
        return {'ui': {'text': [json.dumps(status, ensure_ascii=False, indent=2)]},
                'result': (connection, json.dumps(status, ensure_ascii=False), text, images)}


NODE_CLASS_MAPPINGS = {
    'StrataT8Connection': StrataConnection, 'StrataT8Text': StrataText, 'StrataT8Prompt': StrataPrompt,
    'StrataT8Structured': StrataStructured, 'StrataT8Extract': StrataExtract, 'StrataT8Number': StrataNumber,
    'StrataT8Image': StrataImage, 'StrataT8Batch': StrataBatch, 'StrataT8ImageBatch': StrataImageBatch,
    'StrataT8Control': StrataControl}
NODE_DISPLAY_NAME_MAPPINGS = {
    'StrataT8Connection': 'Strata 连接 / Connection', 'StrataT8Text': 'Strata 文本生成 / Text',
    'StrataT8Prompt': 'Strata 提示词助手 / Prompt', 'StrataT8Structured': 'Strata 结构化分镜 / JSON',
    'StrataT8Extract': 'Strata JSON 提取 / Extract', 'StrataT8Number': 'Strata JSON 数值 / Number',
    'StrataT8Image': 'Strata 图片分析 / Vision', 'StrataT8Batch': 'Strata 文本批量 / Batch',
    'StrataT8ImageBatch': 'Strata 图片批量 / Image Batch', 'StrataT8Control': 'Strata 服务控制 / Control'}
