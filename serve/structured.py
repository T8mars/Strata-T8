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


def _only_objects(node, root, refs=(), dialect=None):
    """True when every value `node` accepts is a JSON object, so "return one JSON object" stays true.

    `type: object`, an anyOf/oneOf whose branches all qualify (e.g. a root union of object shapes, which llama.cpp's
    grammar path accepts and apps send), an allOf with a qualifying member, or a local `$ref` to one of these.
    Anything that can also be an array, string, number, boolean or null (including `type: ["object", "null"]`) is
    not, and a `$ref` cycle never qualifies.
    """
    if not isinstance(node, dict):
        return False
    dialect = node.get('$schema', dialect)
    ref = node.get("$ref")
    # Draft 7 and earlier ignore siblings of $ref: an object assertion beside
    # a reference to an array must not prove an object-only output contract.
    legacy_ref = isinstance(ref, str) and isinstance(dialect, str) and any(
        marker in dialect for marker in ('draft-03', 'draft-04', 'draft-06', 'draft-07'))
    if not legacy_ref and node.get("type") == "object":
        return True
    if isinstance(ref, str) and ref.startswith("#") and ref not in refs:
        target = root
        for part in ref[1:].split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                return False
            target = target[part]
        return _only_objects(target, root, refs + (ref,), dialect)
    if legacy_ref:
        return False
    for key in ("anyOf", "oneOf"):
        branches = node.get(key)
        if isinstance(branches, list) and branches and all(_only_objects(b, root, refs, dialect) for b in branches):
            return True
    branches = node.get("allOf")
    return isinstance(branches, list) and any(_only_objects(b, root, refs, dialect) for b in branches)


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
        if not _only_objects(schema, schema):
            raise ValueError("response_format schema must accept only JSON objects at its root "
                             "(type object, or anyOf/oneOf of object schemas)")
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


def _extract_json(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    if s.startswith("```"):
        first = s.find("\n")
        if first != -1:
            end = s.find("```", first + 1)
            if end != -1:
                s = s[first + 1:end].strip()
    start = s.find("{")
    if start == -1:
        return s
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "\"":
                in_str = False
        else:
            if ch == "\"":
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
    return s[start:]


def validated_json(text, validator, finish, compatibility=False):
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
        value = json.loads(_extract_json(text) if compatibility else text,
                           object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError('the answer is not a JSON object')
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
