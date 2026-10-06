"""JSON response formats at the HTTP boundary: prompt once, validate before delivery.

The native engine has no grammar decoder. Failed generations are errors, never
silently retried or returned as successful structured output.

`jsonschema` is optional (no new hard dependency of the server): json_object needs only the standard library, and
json_schema is checked against its schema when the package is installed - without it, only that the answer is one
JSON object, and the server says so once.
"""
import json
import threading


class StructuredOutputError(RuntimeError):
    pass


class _ObjectOnly:
    """The validator for json_object, and for json_schema without jsonschema: one JSON object."""

    def iter_errors(self, value):
        if not isinstance(value, dict):
            yield _ObjectError()


class _ObjectError:
    absolute_path = ()
    message = "the answer is not a JSON object"


_jsonschema = None                     # (validators, SchemaError, Registry, NoSuchResource), False when not installed
_jsonschema_lock = threading.Lock()


def jsonschema_modules():
    """jsonschema, imported on first use; None when it is not installed (said once, in the server window)."""
    global _jsonschema
    with _jsonschema_lock:
        if _jsonschema is None:
            try:
                from jsonschema import validators
                from jsonschema.exceptions import SchemaError
                from referencing import Registry
                from referencing.exceptions import NoSuchResource
                _jsonschema = (validators, SchemaError, Registry, NoSuchResource)
            except ImportError:
                _jsonschema = False
                print('[strata] response_format json_schema: the Python package jsonschema is not installed, so '
                      'answers are only checked to be one JSON object (python -m pip install "jsonschema>=4.23,<5")',
                      flush=True)
        return _jsonschema or None


def prepare_format(response_format, messages):
    if response_format is None:
        return messages, None
    if not isinstance(response_format, dict):
        raise ValueError("response_format must be an object")
    kind = response_format.get("type")
    if kind == "text":
        return messages, None
    if kind == "json_object":
        schema = {"type": "object"}
        modules = None                 # the standard library is enough for "one JSON object"
    elif kind == "json_schema":
        spec = response_format.get("json_schema")
        if not isinstance(spec, dict) or not isinstance(spec.get("schema"), dict):
            raise ValueError("response_format.json_schema needs a schema object")
        if not isinstance(spec.get("name"), str) or not spec["name"]:
            raise ValueError("response_format.json_schema needs a name")
        if "strict" in spec and not isinstance(spec["strict"], bool):
            raise ValueError("response_format.json_schema.strict must be boolean")
        schema = spec["schema"]
        if schema.get("type") != "object":
            raise ValueError("response_format schema must have type object at its root")
        modules = jsonschema_modules()
    else:
        raise ValueError("response_format.type must be text, json_object or json_schema")

    def check_refs(node):
        if isinstance(node, dict):
            for key in ('$ref', '$dynamicRef'):
                value = node.get(key)
                if key in ("$ref", "$dynamicRef") and isinstance(value, str) and not value.startswith("#"):
                    raise ValueError("response_format supports only local schema references (#...)")
            for key in ('$defs', 'definitions', 'properties', 'patternProperties', 'dependentSchemas'):
                for value in (node.get(key) or {}).values() if isinstance(node.get(key), dict) else ():
                    check_refs(value)
            for key in ('additionalProperties', 'unevaluatedProperties', 'propertyNames', 'items', 'contains',
                        'additionalItems', 'not', 'if', 'then', 'else', 'unevaluatedItems', 'contentSchema',
                        'allOf', 'anyOf', 'oneOf', 'prefixItems'):
                if key in node: check_refs(node[key])
        elif isinstance(node, list):
            for value in node:
                check_refs(value)
    if modules is None:
        try:
            check_refs(schema)
        except RecursionError:
            raise ValueError('response_format schema is too deeply nested') from None
        validator = _ObjectOnly()
    else:
        validators, SchemaError, Registry, NoSuchResource = modules

        def no_remote(uri):
            raise NoSuchResource(ref=uri)

        try:
            cls = validators.validator_for(schema)
            cls.check_schema(schema)
            registry = Registry(retrieve=no_remote)
            from referencing import Resource
            from referencing.exceptions import Unresolvable
            from referencing.jsonschema import DRAFT202012, specification_with
            specification = specification_with(cls.META_SCHEMA['$schema'], default=DRAFT202012)
            resource = Resource.from_contents(schema, default_specification=specification)
            resolver = registry.resolver_with_root(resource)
            seen = set()
            def local_refs(current, scoped):
                node = current.contents
                if id(node) in seen: return
                seen.add(id(node))
                if isinstance(node, dict):
                    current_cls = validators.validator_for(node, default=cls)
                    for key in ('$ref', '$dynamicRef', '$recursiveRef'):
                        if key not in node or key not in current_cls.VALIDATORS: continue
                        reference = node[key]
                        if not reference.startswith('#'):
                            raise ValueError('response_format supports only local schema references (#...)')
                        try:
                            resolved = scoped.lookup(reference)
                        except Unresolvable:
                            raise ValueError('response_format local reference does not identify an existing schema') from None
                        current_cls.check_schema(resolved.contents)
                        local_refs(Resource.from_contents(resolved.contents, default_specification=specification), resolved.resolver)
                for child in current.subresources():
                    local_refs(child, scoped.in_subresource(child))
            local_refs(resource, resolver)
            validator = cls(schema, registry=registry)
        except SchemaError as exc:
            raise ValueError(f"invalid response_format schema: {exc.message}") from exc
        except RecursionError:
            raise ValueError('response_format schema is too deeply nested') from None
    directive = ("OUTPUT FORMAT REQUIREMENT: Return exactly one JSON object matching the JSON Schema below. "
                 "No Markdown, headings, code fences, commentary, or text outside the JSON. "
                 "Use every required field, correct types, and only allowed fields. "
                 "Put all requested writing inside the appropriate JSON string fields.\nJSON Schema:\n" +
                 json.dumps(schema, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    messages = [dict(message) for message in messages]
    if messages and messages[0].get("role") == "system":
        content = messages[0].get("content") or ""
        if isinstance(content, list):
            messages[0]["content"] = content + [{"type": "text", "text": directive}]
        else:
            messages[0]["content"] = content + "\n\n" + directive
    else:
        messages.insert(0, {"role": "system", "content": directive})
    return messages, validator


def validated_json(text, validator, finish):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError(f"duplicate JSON key: {key}")
            obj[key] = value
        return obj

    def constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    if finish != "stop":
        raise StructuredOutputError(f"structured output was incomplete (finish_reason={finish}); increase the output budget")
    try:
        value = json.loads(text or "", object_pairs_hook=pairs, parse_constant=constant)
        canonical = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError) as exc:
        raise StructuredOutputError(f"model did not return valid JSON: {exc}") from exc
    try:
        error = next(validator.iter_errors(value), None)
    except Exception as exc:
        raise StructuredOutputError(f"could not validate structured output: {exc}") from exc
    if error is not None:
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        raise StructuredOutputError(f"model output failed the JSON Schema at {path}: {error.message}")
    return canonical
