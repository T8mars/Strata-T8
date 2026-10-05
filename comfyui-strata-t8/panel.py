"""Local-only configuration/status endpoints; credentials never returned to the browser."""
import asyncio
import json
from urllib.parse import urlsplit
from . import core


def register():
    try:
        from server import PromptServer
        from aiohttp import web
        server = PromptServer.instance
    except (ImportError, AttributeError):
        return

    def local(request, post=False):
        host = urlsplit('http://'+request.host).hostname
        origin = request.headers.get('Origin')
        if request.remote not in ('127.0.0.1', '::1') or host not in ('127.0.0.1', 'localhost', '::1'):
            raise web.HTTPForbidden(text='Strata profile controls require a loopback ComfyUI connection')
        if origin and urlsplit(origin).netloc != request.host:
            raise web.HTTPForbidden(text='Foreign Origin')
        if post and request.content_type != 'application/json':
            raise web.HTTPUnsupportedMediaType(text='Use application/json')

    @server.routes.get('/strata_t8/profiles')
    async def get_profiles(request):
        local(request)
        values = {}
        for name in core.profiles():
            if core.profile_path(name).is_file():
                value = core.read_profile(name)
                values[name] = {k: v for k, v in value.items() if k != 'api_key'}
                values[name]['key_configured'] = bool(value.get('api_key'))
        return web.json_response({'profiles': values, 'home': str(core.HOME)})

    @server.routes.post('/strata_t8/profile')
    async def set_profile(request):
        local(request, True)
        body = await request.json()
        try:
            await asyncio.to_thread(core.save_profile, body['name'], body['profile'])
            return web.json_response({'saved': body['name']})
        except Exception as error:
            return web.json_response({'error': str(error)}, status=400)

    @server.routes.post('/strata_t8/control')
    async def control(request):
        local(request, True)
        body = await request.json()
        if body.get('action') not in ('start', 'status', 'load', 'unload', 'stop'):
            raise web.HTTPBadRequest(text='Unknown action')
        if body['action'] == 'load':
            return web.json_response({'error': 'Load must run through a Strata Control workflow in the ComfyUI queue.'}, status=409)
        # UI controls must not unload ComfyUI weights while an unrelated graph is running.
        running, _ = server.prompt_queue.get_current_queue()
        if running and body['action'] != 'status':
            return web.json_response({'error': 'Wait for the ComfyUI queue to finish before controlling the service from the panel.'}, status=409)
        try:
            status = await asyncio.to_thread(core.control, core.Connection(body['name']), body['action'], lambda: None)
            return web.json_response(status)
        except Exception as error:
            return web.json_response({'error': str(error)}, status=400)
