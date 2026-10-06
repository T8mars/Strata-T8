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
            from referencing.exceptions import Unresolvable
            from referencing.jsonschema import specification_with

            def schema_class(node, inherited):
                if not isinstance(node, dict) or '$schema' not in node:
                    return inherited
                if not isinstance(node['$schema'], str) or not node['$schema']:
                    raise ValueError('response_format schema $schema must be a nonempty string')
                selected = validators.validator_for(node, default=None)
                if selected is None:
                    raise ValueError('response_format schema declares an unsupported $schema dialect')
                return selected

            checked = {}

            def check_tree(node, inherited):
                current_cls = schema_class(node, inherited)
                key = (id(node), current_cls)
                if key in checked:
                    return checked[key]
                specification = specification_with(current_cls.META_SCHEMA['$schema'])
                try:
                    children = list(specification.subresources_of(node))
                except (TypeError, AttributeError):
                    # Let the meta-schema describe invalid keyword containers.
                    current_cls.check_schema(node)
                    raise ValueError('invalid response_format schema child container') from None
                if isinstance(node, dict) and 'dependencies' in current_cls.VALIDATORS:
                    dependencies = node.get('dependencies')
                    if isinstance(dependencies, dict):
                        # Older referencing drafts enumerate dependencies from
                        # the first value. Property-name arrays are data, while
                        # schema dependencies must be found in every position.
                        property_dependencies = {id(value) for value in dependencies.values()
                                                 if isinstance(value, list)}
                        children = [child for child in children if id(child) not in property_dependencies]
                        children.extend(value for value in dependencies.values() if isinstance(value, (dict, bool)))
                child_ids = {id(child) for child in children if isinstance(child, dict)}

                def mask(value):
                    # Check each schema under its own dialect, while retaining
                    # keyword container shapes and boolean schema restrictions.
                    if isinstance(value, dict):
                        if id(value) in child_ids:
                            return {}
                        return {key: {} if isinstance(part, dict) and id(part) in child_ids else part
                                for key, part in value.items()}
                    if isinstance(value, list):
                        return [{} if isinstance(part, dict) and id(part) in child_ids else part for part in value]
                    return value

                shallow = {key: mask(value) for key, value in node.items()} if isinstance(node, dict) else node
                current_cls.check_schema(shallow)
                result = (current_cls, specification, children)
                checked[key] = result
                for child in children:
                    check_tree(child, current_cls)
                return result

            cls, specification, _ = check_tree(schema, validators.validator_for(True))
            registry = Registry(retrieve=no_remote)
            resource = specification.create_resource(schema)
            resolver = registry.resolver_with_root(resource)
            seen = set()
            def local_refs(current, scoped, inherited):
                node = current.contents
                current_cls, current_specification, children = check_tree(node, inherited)
                key = (id(node), current_cls, scoped._base_uri)
                if key in seen: return
                seen.add(key)
                if isinstance(node, dict):
                    active = dict(current_cls._APPLICABLE_VALIDATORS(node))
                    for key in ('$ref', '$dynamicRef', '$recursiveRef'):
                        if key not in active or key not in current_cls.VALIDATORS: continue
                        reference = active[key]
                        if not reference.startswith('#'):
                            raise ValueError('response_format supports only local schema references (#...)')
                        try:
                            resolved = scoped.lookup(reference)
                        except Unresolvable:
                            raise ValueError('response_format local reference does not identify an existing schema') from None
                        except (AttributeError, TypeError):
                            raise ValueError('response_format local reference with mixed legacy dependencies is '
                                             'unsupported; use a JSON Pointer reference (#/...)') from None
                        _, target_specification, _ = check_tree(resolved.contents, current_cls)
                        local_refs(target_specification.create_resource(resolved.contents), resolved.resolver, current_cls)
                    if '$ref' in active and len(active) == 1:
                        # Older drafts ignore all $ref sibling assertions. The
                        # referenced target above remains subject to preflight.
                        return
                for contents in children:
                    _, child_specification, _ = check_tree(contents, current_cls)
                    child = child_specification.create_resource(contents)
                    local_refs(child, scoped.in_subresource(child), current_cls)
            local_refs(resource, resolver, cls)
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
        canonical.encode('utf-8')
    except RecursionError:
        raise StructuredOutputError('model JSON is too deeply nested') from None
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
