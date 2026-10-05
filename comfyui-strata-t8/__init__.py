"""ComfyUI Strata-T8: V3 definitions with a V1 compatibility adapter sharing execution."""
from .nodes import NODE_CLASS_MAPPINGS as LEGACY, NODE_DISPLAY_NAME_MAPPINGS
from . import panel

WEB_DIRECTORY = './web'
panel.register()

try:
    from comfy_api.latest import io, ComfyExtension
except ModuleNotFoundError as error:
    if error.name not in ('comfy_api', 'comfy_api.latest'):
        raise
    NODE_CLASS_MAPPINGS = LEGACY
else:
    def adapt(node_id, legacy):
        class Node(io.ComfyNode):
            @classmethod
            def define_schema(cls):
                inputs = []
                for group, specs in legacy.INPUT_TYPES().items():
                    for name, spec in specs.items():
                        kind, options = spec[0], dict(spec[1] if len(spec) > 1 else {})
                        if 'forceInput' in options:
                            options['force_input'] = options.pop('forceInput')
                        options['optional'] = group == 'optional'
                        if isinstance(kind, list):
                            inputs.append(io.Combo.Input(name, options=kind, **options))
                        else:
                            type_io = {'STRING': io.String, 'INT': io.Int, 'FLOAT': io.Float, 'BOOLEAN': io.Boolean, 'IMAGE': io.Image}.get(kind) or io.Custom(kind)
                            inputs.append(type_io.Input(name, **options))
                output_lists = getattr(legacy, 'OUTPUT_IS_LIST', [False]*len(legacy.RETURN_TYPES))
                outputs = []
                for kind, name, is_list in zip(legacy.RETURN_TYPES, legacy.RETURN_NAMES, output_lists):
                    type_io = {'STRING': io.String, 'FLOAT': io.Float, 'IMAGE': io.Image}.get(kind) or io.Custom(kind)
                    outputs.append(type_io.Output(name, is_output_list=is_list))
                return io.Schema(node_id=node_id, display_name=NODE_DISPLAY_NAME_MAPPINGS[node_id], category='Strata-T8',
                                 inputs=inputs, outputs=outputs, is_output_node=getattr(legacy, 'OUTPUT_NODE', False))

            @classmethod
            def execute(cls, **kwargs):
                result = legacy().run(**kwargs)
                if isinstance(result, dict):
                    return io.NodeOutput(*result['result'], ui=result.get('ui'))
                return io.NodeOutput(*result)

            @classmethod
            def fingerprint_inputs(cls, **kwargs):
                return legacy.IS_CHANGED(**kwargs) if hasattr(legacy, 'IS_CHANGED') else None
        Node.__name__ = node_id
        return Node

    V3_NODES = [adapt(node_id, legacy) for node_id, legacy in LEGACY.items()]
    class Extension(ComfyExtension):
        async def get_node_list(self):
            return V3_NODES

    async def comfy_entrypoint():
        return Extension()
