"""Loopback model settings and an owned lazy server for the portable launcher."""
from __future__ import annotations

import hmac
import copy
import hashlib
import html
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import shutil
import tempfile
import threading
import time
from urllib.parse import urlsplit
import webbrowser
from serve import api_access

ROOT = Path(__file__).resolve().parents[1]
BODY_LIMIT = 16 * 1024
LOG_LIMIT = 16 * 1024
UI_PORTS = range(17880, 17912)
UPDATE_READY_SECONDS = 12


class SetupError(ValueError):
    pass


def wait_update_helper(process, receipt, nonce, root):
    """A spawned PowerShell process is not proof that its script loaded successfully."""
    deadline = time.monotonic() + UPDATE_READY_SECONDS
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise SetupError(f'更新程序提前退出（代码 {code}）。启动器保持打开，请运行 UPDATE-PORTABLE.bat 查看错误。')
        try:
            if receipt.stat().st_size > 16384:
                raise SetupError('更新程序的启动回执无效。')
            status = json.loads(receipt.read_text('utf-8-sig'))
        except FileNotFoundError:
            time.sleep(.05)
            continue
        if (not isinstance(status, dict) or status.get('nonce') != nonce
                or status.get('app_root') != str(root) or status.get('parent_id') != os.getpid()):
            raise SetupError('更新程序的启动回执与当前整合包不匹配。')
        if status.get('state') != 'waiting_parent':
            raise SetupError('更新程序未能就绪：'+str(status.get('error') or status.get('state')))
        return
    raise SetupError('更新程序在 12 秒内未就绪，启动器保持打开。请运行 UPDATE-PORTABLE.bat 查看错误。')


def free_port(preferred=8080, *, strict=False, exclude=()):
    ports = [preferred] if strict else list(dict.fromkeys([preferred, *range(8080, 8113)]))
    for port in ports:
        if port in exclude:
            continue
        try:
            with socket.socket() as sock:
                if os.name == 'nt':
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(('127.0.0.1', port))
            return port
        except OSError:
            continue
    raise SetupError(f'端口 {preferred} 正在使用，请选择其他端口。')


def json_reply(port, path, *, headers=None, timeout=2):
    connection = HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.request('GET', path, headers=headers or {})
        response = connection.getresponse()
        data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or response.status != 200:
            raise SetupError('服务尚未就绪。')
        value = json.loads(data)
        if not isinstance(value, dict):
            raise SetupError('服务没有返回有效状态。')
        return value
    finally:
        connection.close()


def _regular(path, root):
    root = root.resolve()
    original = Path(path)
    resolved = original.resolve()
    if not resolved.is_relative_to(root):
        raise SetupError('程序配置路径超出了整合包目录。')
    for component in [original, *original.parents]:
        if component == root.parent:
            break
        if component.is_symlink() or getattr(component, 'is_junction', lambda: False)():
            raise SetupError('程序配置不能通过链接指向其他目录。')
    return resolved


def model_summary(data, portable):
    path = Path(data).expanduser().resolve()
    if not path.is_dir():
        raise SetupError('这个模型目录不存在，请重新选择。')
    if (path/'portable-model.json').is_file() and (path/'portable-model.json').stat().st_size > 1024 * 1024:
        raise SetupError('模型清单过大，请使用完整的正规模型包。')
    try:
        model = portable.model_delivery(path)
    except (OSError, ValueError, RuntimeError, RecursionError) as error:
        if not (path/'portable-model.json').is_file() and not (path/'ds4.txt').is_file():
            raise SetupError('未找到完整模型。请选择收到的 Strata-data 模型根目录，而不是内部 GGUF 子目录。') from error
        raise SetupError(f'模型文件不完整或不兼容：{error}') from error
    label = model.get('model_name')
    if model.get('repository') == 'SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF':
        label = f"SC117 Uncensored · {model['model']}"
    if not isinstance(label, str) or not label.strip() or len(label) > 200:
        label = f"{model['family'].upper()} · {model['model']}"
    return {'path': str(path), 'family': model['family'], 'quantization': model['model'],
            'label': label, 'api_model_id': portable.model_api_id(model),
            'bytes': sum(e['size'] for e in model.get('files', [])), 'complete': True,
            'text_only': model.get('text_only', False)}


def discover_models(root, portable):
    candidates, seen, results = [], set(), []
    try:
        saved = portable.read_state().get('portable_data_dir')
        if saved:
            path = Path(saved)
            candidates.append(path if path.is_absolute() else root/path)
    except (OSError, ValueError, RuntimeError):
        pass
    # Look only beside this installation and its ancestors, never walk a drive.
    for parent in [root, *list(root.parents)[:3]]:
        candidates.append(parent/'Strata-data')
        try:
            candidates.extend(sorted(parent.glob('Strata-data*'))[:32])
        except OSError:
            continue
    for path in candidates[:128]:
        key = os.path.normcase(str(path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        if (path/'portable-model.json').is_file() or (path/'ds4.txt').is_file():
            try:
                results.append(model_summary(path, portable))
            except (OSError, ValueError, RuntimeError):
                continue
    return results


class ModelSetup:
    def __init__(self, root=ROOT, options=None):
        import portable
        self.root, self.portable, self.options = Path(root).resolve(), portable, options
        if self.root != portable.ROOT.resolve():
            raise SetupError('模型设置必须从当前程序目录启动。')
        self.version = json.loads((self.root/'meta.json').read_text(encoding='utf-8'))['version']
        self.token = secrets.token_urlsafe(32)
        self.instance_id = secrets.token_hex(16)
        self.lock = threading.RLock()
        self.folder_lock = threading.Lock()
        self.closed = threading.Event()
        self.http = None
        self.url = None
        self.process = None
        self.configure_process = None
        self.backend_id = None
        self.backend_port = None
        self.backend_config = None
        self.backend_model_id = None
        self.backend_api_key_required = None
        self.log = None
        self.job = {'state': 'idle', 'message': '选择模型目录后即可开始。', 'error': None}
        self.update = {'state': 'unchecked', 'current': self.version, 'latest': None, 'available': False,
                       'supported': os.name == 'nt' and (self.root/'PACKAGE-MANIFEST.json').is_file() and not (self.root/'.git').exists(),
                       'message': '启动时自动检查新版本；离线仍可使用。'}
        self.model = None
        self.current_error = None
        # The detached helper survives the old page. Bring its failure back to the
        # restarted settings page without trusting arbitrary files or a foreign install.
        try:
            receipt = _regular(self.root/'.portable-update/gui-result.json', self.root)
            if receipt.is_file() and receipt.stat().st_size <= 16384:
                result = json.loads(receipt.read_text('utf-8-sig'))
                if isinstance(result,dict) and result.get('app_root') == str(self.root) and result.get('state') == 'error':
                    detail = result.get('error')
                    if isinstance(detail,str):
                        self.job = {'state':'error','message':'更新未完成，可重试；详细进度见 logs/update-helper.log。','error':detail[:4000]}
        except (OSError, ValueError, RuntimeError):
            pass
        self.requested = getattr(options, 'data_dir', None)
        self.current_config = None
        self.prepared_model = None
        self.job_log = None
        try:
            state = portable.read_state()
            cfg = portable.config_path(state)
            if cfg.is_file():
                self.model = model_summary(portable.data_path(self.requested), portable)
                portable.read_config(cfg)
                self.current_config = cfg
        except (OSError, ValueError, RuntimeError) as error:
            self.current_error = str(error)

    def set_job(self, state, message, error=None):
        with self.lock:
            self.job = {'state': state, 'message': message, 'error': error}

    def _start_job(self, target, args=()):
        try:
            threading.Thread(target=target, args=args, daemon=True).start()
        except (OSError, RuntimeError, MemoryError) as error:
            message = '无法启动后台任务，请释放系统资源后重试。'
            self.set_job('error', message, str(error))
            raise SetupError(message) from error

    def _options(self, data):
        allowed = {'path', 'port', 'context', 'vision', 'native_video', 'backend', 'speed'}
        if not isinstance(data, dict) or set(data)-allowed:
            raise SetupError('无效的模型设置。')
        path = data.get('path')
        if not isinstance(path, str) or not path.strip() or len(path) > 4096 or any(ord(c) < 32 for c in path):
            raise SetupError('请选择模型目录。')
        port = data.get('port', 8080)
        context = data.get('context', 32768)
        if type(port) is not int or not 1 <= port <= 65535:
            raise SetupError('服务端口必须在 1–65535 之间。')
        if type(context) is not int or not 512 <= context <= 131072:
            raise SetupError('上下文长度必须在 512–131072 之间。')
        vision = data.get('vision', 'no')
        backend = data.get('backend', 'auto')
        native = data.get('native_video', False)
        if vision not in ('auto', 'no', 'gpu', 'cpu') or backend not in ('auto', 'cuda', 'hip') or type(native) is not bool:
            raise SetupError('无效的运行设置。')
        summary = model_summary(path.strip().strip('"'), self.portable)
        if summary['family'] == 'deepseek41' and backend == 'hip':
            raise SetupError('此整合包的 DeepSeek 需要 NVIDIA CUDA 后端。')
        if native and (summary['family'] != 'qwen' or backend == 'hip'):
            raise SetupError('原生视频需要 Qwen NVIDIA 配置；DeepSeek 请使用文本模式。')
        if 'speed' in data and (summary['family'] != 'deepseek41' or data['speed'] not in ('full', 'faster')):
            raise SetupError('完整质量 / 更快模式只适用于 DeepSeek。')
        if native and vision == 'no':
            raise SetupError('原生视频需要先启用视觉。')
        if vision not in ('no', 'auto') and (summary['family'] != 'qwen' or backend == 'hip'):
            raise SetupError('此视觉权重用于 Qwen NVIDIA 配置，请改用文本模式。')
        if vision not in ('no', 'auto') and not self.vision_available():
            raise SetupError('此版本未附视觉权重，请选择文本模式或安装 VisionReady 版。')
        return {**data, 'path': summary['path'], 'port': port, 'context': context,
                'vision': vision, 'backend': backend, 'native_video': native}, summary

    def vision_available(self):
        try:
            catalog = json.loads((self.root/'vision/catalog.json').read_text(encoding='utf-8'))
            return (self.root/'vision/weights'/catalog['file']).is_file()
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def defaults(self):
        cfg = {}
        if self.current_config:
            try:
                cfg = self.portable.read_config(self.current_config)
            except (OSError, ValueError, RuntimeError):
                pass
        preferred = getattr(self.options, 'port', None) or cfg.get('port', 8080)
        try:
            port = self.backend_port or free_port(preferred, exclude=[self.http.server_port] if self.http else [])
        except SetupError:
            port = preferred
        context = getattr(self.options, 'context', None) or 32768
        args = cfg.get('args', [])
        if not getattr(self.options, 'context', None):
            for index, arg in enumerate(args):
                value = (args[index+1] if arg == '--max-context' and index+1 < len(args) else
                         arg.partition('=')[2] if arg.startswith('--max-context=') else None)
                if value is not None:
                    try:
                        context = int(value)
                    except ValueError:
                        pass
        vision = getattr(self.options, 'vision', None)
        if vision is None:
            vision = ('gpu' if cfg['vision'].get('gpu', True) else 'cpu') if cfg.get('vision') else ('auto' if self.vision_available() else 'no')
        native_arg = getattr(self.options, 'native_video', None)
        native = bool((cfg.get('vision') or {}).get('native_video'))
        backend = getattr(self.options, 'backend', None) or cfg.get('backend', 'auto')
        if (self.model or {}).get('family') == 'deepseek41':
            # A newly selected pack can still have the previous Qwen config.
            # Adapt inherited defaults; explicit incompatible choices must fail.
            if getattr(self.options, 'vision', None) is None:
                vision = 'no'
            if native_arg is None:
                native = False
            if getattr(self.options, 'backend', None) is None:
                backend = 'cuda'
            if getattr(self.options, 'context', None) is None:
                context = min(context, 8192)
        speed = 'full'
        for index, arg in enumerate(args):
            if arg == '--experts' and index + 1 < len(args):
                speed = 'faster' if args[index + 1] == '5' else 'full'
            elif arg.startswith('--experts='):
                speed = 'faster' if arg.partition('=')[2] == '5' else 'full'
        if getattr(self.options, 'speed', None) in ('full', 'faster'):
            speed = self.options.speed
        return {'path': self.requested or (self.model or {}).get('path', ''), 'port': port,
                'context': context, 'vision': vision, 'native_video': native,
                **({'speed':speed} if (self.model or {}).get('family') == 'deepseek41'
                   or getattr(self.options, 'speed', None) is not None else {}),
                'backend': backend,
                **({'native_video': native_arg} if native_arg is not None else {})}

    def snapshot(self):
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            if self.process is not None and not running and self.job['state'] == 'ready':
                self.job = {'state': 'error', 'message': '服务已退出，请重新启动。', 'error': '服务进程已结束。'}
            connection = None
            network_error = None
            try:
                network = self.network_settings()
            except (OSError, ValueError, RuntimeError) as error:
                # A config can be temporarily unavailable while a model is being
                # switched. Keep status readable; writes/startup still require the
                # real configuration and never use this display-only fallback.
                fallback = self.backend_config or {}
                network = {'scope':'lan' if fallback.get('host') == '0.0.0.0' and fallback.get('api_key') else 'local',
                           'api_key':fallback.get('api_key','')}
                network_error = str(error)
            if running and self.job['state'] == 'ready' and self.backend_port and self.backend_model_id:
                base = f'http://127.0.0.1:{self.backend_port}/v1'
                connection = {'model_id': self.backend_model_id, 'base_url': base, 'models_url': base+'/models',
                              'api_key_required': (bool((self.backend_config or {}).get('api_key'))
                                                   if self.backend_api_key_required is None else self.backend_api_key_required),
                              'lan_base_urls': [f'http://{ip}:{self.backend_port}/v1' for ip in api_access.ipv4_addresses()]
                                  if network['scope'] == 'lan' and (self.backend_config or {}).get('host') == '0.0.0.0' else []}
            return {'version': self.version, 'app_root': str(self.root), 'instance_id': self.instance_id,
                    'job': dict(self.job), 'model': self.model,
                    'configured': self.current_config is not None, 'running': running,
                    'chat_url': f'http://127.0.0.1:{self.backend_port}/' if running and self.job['state'] == 'ready' else None,
                    'defaults': self.defaults(), 'vision_available': self.vision_available(),
                    'folder_picker': os.name == 'nt', 'configuration_error': self.current_error or network_error,
                    'prepared_model': self.prepared_model,
                    'api_connection': connection,
                    'network': api_access.public(network),
                    'update': dict(self.update),
                    'log_tail': self._tail(self.job_log) if self.job_log and self.job_log.is_file() else ''}

    def _spawn(self, args, log):
        from serve.winjob import contain
        process = subprocess.Popen(args, cwd=self.root, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        contain(process)
        return process

    def _log_path(self, name):
        directory = _regular(self.root/'.portable-ui', self.root)
        directory.mkdir(exist_ok=True)
        return _regular(directory/name, self.root)

    def _tail(self, path):
        with Path(path).open('rb') as f:
            f.seek(max(0, Path(path).stat().st_size-LOG_LIMIT))
            return f.read(LOG_LIMIT).decode('utf-8', 'replace')

    def network_settings(self):
        with self.lock:
            saved = self.portable.read_state().get('portable_api')
            if saved is not None:
                return api_access.normalize(saved)
            config = self.portable.read_config(self.current_config) if self.current_config else {}
            return api_access.load(self.portable,config)

    def save_network(self,data):
        with self.lock:
            if self.closed.is_set() or self.job['state'] in ('preparing','configuring','starting','stopping','updating'):
                raise SetupError('请先完成当前操作。')
            settings = api_access.normalize(data,self.network_settings())
            running = self.process is not None and self.process.poll() is None
            state_path = self.portable.STATE
            previous_state = state_path.read_bytes() if state_path.exists() else None
            path = self.current_config
            previous_config = path.read_bytes() if path and path.exists() else None
            self.job={'state':'configuring','message':'正在保存 API 访问设置；已运行的服务将自动重启…','error':None}
        def work():
            try:
                if running:self.stop_backend()
                if self.closed.is_set():return
                with self.lock:
                    state=self.portable.read_state();state['portable_api']=settings
                    self.portable.save_json(state_path,state)
                    if path:
                        config=self.portable.read_config(path)
                        self.portable.save_json(path,api_access.apply(config,settings))
                if running and not self.closed.is_set():self._start_backend()
                else:self.set_job('idle','API 访问设置已保存，启动模型服务后生效。')
            except Exception as error:
                stop_error = None
                if running:
                    try:self.stop_backend()
                    except (OSError, subprocess.SubprocessError) as failed:
                        stop_error = str(failed)
                with self.lock:
                    try:
                        if previous_state is None:state_path.unlink(missing_ok=True)
                        else:self.portable.atomic_bytes(state_path,previous_state)
                        if path and previous_config is not None:self.portable.atomic_bytes(path,previous_config)
                    except OSError as restore_error:
                        self.set_job('error','API 设置保存失败，请检查配置文件。',f'{error}\n配置恢复失败：{restore_error}');return
                    if stop_error is not None:
                        self.set_job('error','原配置已恢复，但未能确认服务停止，请重试停止服务。',f'{error}\n停止服务失败：{stop_error}')
                    else:
                        self.set_job('error','API 设置未应用，原设置已保留。',str(error))
        self._start_job(work)

    def begin(self, data=None, *, existing=False, launch=False):
        if existing:
            if self.current_config is None:
                raise SetupError('请先设置模型目录。')
            normalized, summary = None, self.model
        else:
            normalized, summary = self._options(data)
        with self.lock:
            if self.closed.is_set():
                raise SetupError('启动器正在关闭。')
            if self.job['state'] in ('preparing', 'configuring', 'starting', 'stopping', 'updating'):
                raise SetupError('正在处理上一次操作，请稍候。')
            if existing and self.process is not None and self.process.poll() is None:
                return
            running = self.process is not None and self.process.poll() is None
            if not existing and not (running and normalized['port'] == self.backend_port):
                free_port(normalized['port'], strict=True, exclude=[self.http.server_port])
            self.job = {'state': 'starting' if existing else 'configuring', 'message': '正在启动服务…' if existing else ('正在停止当前服务并保存新配置…' if running else '正在检查并保存模型配置…'), 'error': None}
            self._start_job(self._configure_and_start, (normalized, summary, launch))

    def _configure_and_start(self, data, summary, launch=False):
        try:
            if data is not None:
                # Saving settings is the explicit switch action. Validate first,
                # then stop only the child owned by this launcher.
                owned_port = (self.process is not None and self.process.poll() is None
                              and data['port'] == self.backend_port
                              and getattr(self.options, 'port', None) is None)
                self.stop_backend()
                # Windows can retain closed connections on the old port. Keep
                # switching usable without enabling unsafe address reuse or
                # terminating an unrelated listener. Status reports the new port.
                port = free_port(data['port'], strict=not owned_port, exclude=[self.http.server_port])
                command = [sys.executable, '-X', 'utf8', '-u', str(self.root/'portable.py'), 'import',
                           '--data-dir', data['path'], '--port', str(port), '--context', str(data['context']),
                           '--native-video' if data['native_video'] else '--no-native-video']
                if data['vision'] != 'auto':
                    command += ['--vision', data['vision']]
                if data['backend'] != 'auto':
                    command += ['--backend', data['backend']]
                if data.get('speed'):
                    command += ['--speed', data['speed']]
                tokens = getattr(self.options, 'vision_tokens', None)
                if tokens is not None:
                    command += ['--vision-tokens', str(tokens)]
                logpath = self._log_path('configure.log')
                self.job_log = logpath
                with logpath.open('wb') as output:
                    with self.lock:
                        if self.closed.is_set():
                            return
                        self.configure_process = self._spawn(command, output)
                    code = self.configure_process.wait()
                    with self.lock:
                        self.configure_process = None
                if code:
                    raise SetupError('配置未完成，原有设置已保留。\n'+self._tail(logpath))
                self.current_config = self.portable.config_path(self.portable.read_state())
                self.portable.read_config(self.current_config)
                self.model, self.current_error = summary, None
                self.requested = None
                if not launch and self.options is not None:
                    # A saved web form supersedes initial model/port flags.
                    # Keep unrelated launcher controls and hidden token budgets.
                    options = copy.copy(self.options)
                    for name in ('data_dir', 'port', 'context', 'backend', 'vision', 'native_video', 'speed'):
                        setattr(options, name, None)
                    self.options = options
            if self.closed.is_set():
                return
            self._start_backend()
            if data is not None and self.backend_port != data['port']:
                with self.lock:
                    if self.job['state'] == 'ready':
                        self.set_job('ready', f"服务已就绪。原端口 {data['port']} 暂不可用，已切换到 {self.backend_port}；外部客户端请使用下方最新 API 地址。")
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
            try:
                self.stop_backend()
            except (OSError, subprocess.SubprocessError) as failed:
                self.set_job('error', '未能确认服务停止，请重试停止服务后再切换模型。', f'{error}\n停止服务失败：{failed}')
            else:
                self.set_job('error', '请修正设置后重试。', str(error))

    def prepare(self, data):
        if (not isinstance(data, dict) or set(data) != {'path', 'accept_download'}
                or data['accept_download'] is not True):
            raise SetupError('请先确认下载流量。已有完整模型时直接选择目录即可。')
        raw = data.get('path')
        if not isinstance(raw, str) or not raw.strip().strip('"') or len(raw) > 4096 or any(ord(c) < 32 for c in raw):
            raise SetupError('请选择模型保存目录。')
        original = Path(raw.strip().strip('"')).expanduser()
        if original.is_symlink() or getattr(original, 'is_junction', lambda: False)():
            raise SetupError('模型准备目录不能通过链接指向其他位置。')
        path = original.resolve()
        component = path.relative_to(self.root).parts[0].lower() if path != self.root and path.is_relative_to(self.root) else ''
        if path == self.root or component.startswith('engine-') or component in {
                'runtime','engine','serve','tools','vision','data','docs','licenses','ref','third_party','.git','.github','.portable-ui'}:
            raise SetupError('请为模型选择独立文件夹，例如 Strata-data。')
        catalog_path = self.root/'model-sources.json'
        catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
        marker = path/'.strata-model-preparation.json'
        expected = {'repository': catalog['repository'], 'catalog_sha256': hashlib.sha256(catalog_path.read_bytes()).hexdigest()}
        if marker.is_symlink() or getattr(marker, 'is_junction', lambda: False)():
            raise SetupError('模型准备标记不能指向其他文件。')
        if (path/'portable-model.json').is_file():
            if (path/'portable-model.json').stat().st_size > 1024 * 1024:
                raise SetupError('模型清单过大，请选择正规模型目录。')
            descriptor = self.portable.read_json(path/'portable-model.json')
            if not isinstance(descriptor, dict) or descriptor.get('repository') != catalog['repository'] or descriptor.get('model') != 'IQ3_S':
                raise SetupError('此目录已用于其他模型。请为官方 IQ3_S 选择新目录，避免混用模型。')
        elif marker.is_file():
            if marker.stat().st_size > 1024 * 1024:
                raise SetupError('模型准备标记过大，请选择新目录。')
            if self.portable.read_json(marker) != expected:
                raise SetupError('此目录的模型准备来源不同，请选择新目录。')
        elif path.exists():
            top = {p.name for p in path.iterdir()}
            gguf = path/'models/IQ3_S'
            allowed = {entry['file'] for entry in catalog['files']}
            if (top and (top != {'models'} or not gguf.is_dir()
                         or {p.name for p in (path/'models').iterdir()} != {'IQ3_S'}
                         or any(p.name not in allowed for p in gguf.iterdir()))):
                raise SetupError('这个目录包含其他文件。请选择空模型目录，或只含官方 models/IQ3_S GGUF 的目录。')
        with self.lock:
            if self.closed.is_set() or self.job['state'] in ('preparing','configuring','starting','stopping','updating') or (self.process and self.process.poll() is None):
                raise SetupError('请先完成当前操作或停止当前服务。')
            path.mkdir(parents=True, exist_ok=True)
            self.portable.save_json(marker, expected)
            self.job = {'state':'preparing','message':'正在下载缺少的官方模型文件并准备索引，请保持启动窗口开启。','error':None}
            self._start_job(self._prepare_model, (path,))

    def _prepare_model(self, path):
        try:
            logpath = self._log_path('prepare.log')
            self.job_log = logpath
            with logpath.open('wb') as output:
                with self.lock:
                    if self.closed.is_set():
                        return
                    self.configure_process = self._spawn([sys.executable,'-X','utf8','-u',
                         str(self.root/'tools/prepare_portable_model.py'),'--data-dir',str(path),'--yes'],output)
                    process = self.configure_process
                code = process.wait()
                with self.lock:
                    self.configure_process = None
            if self.closed.is_set():
                return
            if code:
                raise SetupError('模型准备未完成。可保留已有文件，重试后会复用已校验的文件。\n'+self._tail(logpath))
            self.prepared_model = model_summary(path,self.portable)
            self.set_job('prepared','模型已准备好，请保存设置并进入。')
        except (OSError,ValueError,RuntimeError,KeyError) as error:
            self.set_job('error','模型准备未完成。',str(error))

    def _start_backend(self):
        config = self.portable.read_config(self.current_config)
        data = self.portable.data_path(self.requested)
        current = self.portable.fingerprint(data)
        state = self.portable.read_state()
        if not self.portable.same_machine(state.get('portable_fingerprint'), current):
            raise SetupError('程序位置、模型或硬件已变化，请重新保存模型设置。')
        if state['portable_fingerprint'].get('version') != current['version'] or state['portable_fingerprint'].get('edition') != current.get('edition'):
            with self.lock:
                self.portable.refresh_updated_config(self.current_config, state, current)
                config = self.portable.read_config(self.current_config)
        settings = self.network_settings()
        host = '0.0.0.0' if settings['scope']=='lan' else '127.0.0.1'
        preferred = getattr(self.options, 'port', None) or config.get('port', 8080)
        self.backend_port = free_port(preferred, strict=getattr(self.options, 'port', None) is not None,
                                      exclude=[self.http.server_port])
        if self.backend_port != config.get('port', 8080):
            config['port'] = self.backend_port
            with self.lock:self.portable.save_json(self.current_config, config)
        if 'portable_api' in self.portable.read_state():
            config = api_access.apply(config,settings)
            with self.lock:self.portable.save_json(self.current_config,config)
        if host != '127.0.0.1' and not config.get('api_key'):
            raise SetupError('局域网服务必须设置 API Key。')
        self.backend_config = config
        self.backend_api_key_required = None
        self.backend_model_id = None
        self.backend_id = secrets.token_hex(16)
        self.set_job('starting', '正在启动本地服务，模型将在首次请求时加载…')
        command = [sys.executable, '-X', 'utf8', '-u', str(self.root/'serve/server.py'), '--engine', 'strata',
                   '--config', str(self.current_config), '--host', host, '--port', str(self.backend_port), '--lazy']
        self.log = self._log_path('server.log').open('wb')
        environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'STRATA_PORTABLE_UI_URL': self.url,
                       'STRATA_PORTABLE_MODEL_LABEL': (self.model or {}).get('label', ''),
                       'STRATA_PORTABLE_UI_ID': self.backend_id}
        if 'portable_api' in state:
            environment.pop('STRATA_API_KEY',None)
        from serve.winjob import contain
        with self.lock:
            if self.closed.is_set():
                self.log.close()
                self.log = None
                return
            self.process = subprocess.Popen(command, cwd=self.root, stdout=self.log, stderr=subprocess.STDOUT,
                         env=environment,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            contain(self.process)
            process = self.process
        deadline = time.monotonic()+90
        while not self.closed.is_set() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise SetupError('服务未能启动。\n'+self._tail(self._log_path('server.log')))
            try:
                status = json_reply(self.backend_port, '/health')
                if status.get('service') == 'strata' and status.get('portable_launcher_instance') == self.backend_id:
                    if type(status.get('api_key')) is bool:
                        self.backend_api_key_required = status['api_key']
                    name = status.get('model', config.get('model_name', 'qwen3.8-flash-next'))
                    if isinstance(name, str) and name.strip() and len(name) <= 200 and not any(ord(c) < 32 or ord(c) == 127 for c in name):
                        self.backend_model_id = name
                    self.set_job('ready', '服务已就绪，可以进入聊天。')
                    return
            except (OSError, ValueError, RuntimeError):
                pass
            self.closed.wait(.2)
        if not self.closed.is_set():
            raise SetupError('服务启动超时，请查看页面中的日志后重试。')

    def check_updates(self):
        with self.lock:
            if self.closed.is_set() or self.job['state'] == 'updating' or self.update['state'] == 'checking':
                return
            self.update.update(state='checking', available=False, message='正在检查新版本…')
        def work():
            try:
                from tools.portable_update import latest_release
                from tools.portable_version import version_key, archive_name
                release = latest_release('T8mars/Strata-T8', timeout=5)
                latest = release['tag_name'].removeprefix('v')
                newer = version_key(latest) > version_key(self.version)
                meta = json.loads((self.root/'meta.json').read_text(encoding='utf-8'))
                asset = archive_name(latest, meta.get('edition', 'Portable-NoModels'))
                names = {item.get('name') for item in release.get('assets', []) if isinstance(item, dict)}
                from tools.component_updates import index_name
                component=index_name(latest,meta.get('edition','Portable-NoModels'))
                if newer and not ({asset, asset+'.sha256'}.issubset(names) or {component,component+'.sha256'}.issubset(names)):
                    raise SetupError('新版缺少当前发行类型的 ZIP 或校验文件，请稍后重试。')
                with self.lock:
                    self.update.update(state='ready', latest=latest, available=newer,
                                       message=f'发现新版本 {latest}。' if newer else '当前已是最新版本。')
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                with self.lock:
                    self.update.update(state='error', available=False, message='暂时无法检查更新，离线使用不受影响。')
        try:
            threading.Thread(target=work, daemon=True).start()
        except (OSError, RuntimeError, MemoryError):
            with self.lock:
                self.update.update(state='error', available=False, message='暂时无法检查更新，离线使用不受影响。')

    def begin_update(self, restore_id=None):
        with self.lock:
            if self.closed.is_set() or self.job['state'] in ('preparing','configuring','starting','stopping','updating'):
                raise SetupError('当前操作尚未完成，请稍候。')
            if not self.update['supported']:
                raise SetupError('一键更新适用于 Windows 解压版；源码仓库请使用 Git 更新。')
            if restore_id is not None:
                if not isinstance(restore_id,str) or len(restore_id)!=32 or any(c not in '0123456789abcdef' for c in restore_id):
                    raise SetupError('无效的回退版本。')
            elif self.update['state'] != 'ready' or not self.update['available']:
                raise SetupError('请先检查更新，确认有可用的新版本。')
            self.job = {'state': 'updating', 'message': '正在启动更新程序，确认就绪后才停止当前服务…', 'error': None}
        def work():
            helper_process = None
            try:
                if restore_id is not None:
                    from tools.portable_restore import read_receipt
                    self.set_job('updating','正在校验回退备份，校验完成后才停止当前服务…')
                    read_receipt(self.root,restore_id)
                    if self.closed.is_set():return
                source = _regular(self.root/'tools/gui_portable_update.ps1', self.root)
                temporary = Path(tempfile.mkdtemp(prefix='Strata-T8-ui-update-')).resolve()
                if temporary.is_relative_to(self.root):
                    raise SetupError('更新工作目录必须位于程序目录外。')
                helper = temporary/'update.ps1'
                shutil.copy2(source, helper)
                nonce = secrets.token_hex(16)
                receipt = temporary/'status.json'
                log = _regular(self.root/'logs/update-helper.log', self.root)
                log.parent.mkdir(parents=True, exist_ok=True)
                # This helper must survive this launcher; do not put it in the
                # owned backend's kill-on-close Job Object.
                arguments=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(helper),
                           '-AppRoot',str(self.root),'-ParentId',str(os.getpid()),'-UIPort',str(self.http.server_port),
                           '-StatusFile',str(receipt),'-HandoffNonce',nonce,'-LogPath',str(log)]
                if restore_id is not None:arguments.extend(['-RestoreId',restore_id])
                # A native EXE proxy also holds a managed file open. Wait for
                # this actual parent, never an arbitrary environment-supplied PID.
                import psutil
                try:
                    parent = psutil.Process(os.getpid()).parent()
                    if parent and Path(parent.exe()).resolve() == self.root/'Strata-T8.exe':
                        arguments.extend(['-LauncherId',str(parent.pid)])
                except (OSError,psutil.Error):
                    pass
                helper_process = subprocess.Popen(arguments,
                    cwd=temporary, creationflags=getattr(subprocess,'CREATE_NEW_CONSOLE',0))
                wait_update_helper(helper_process, receipt, nonce, self.root)
                self.set_job('updating', '更新程序已就绪，正在停止当前服务；下载进度见更新窗口和 logs/update-helper.log。')
                self.stop_backend()
                self.closed.set()
            except Exception as error:
                if helper_process is not None and helper_process.poll() is None:
                    try:
                        helper_process.terminate()
                        helper_process.wait(timeout=3)
                    except (OSError, subprocess.SubprocessError):
                        pass
                self.set_job('error', '更新未启动，请重试或运行 UPDATE-PORTABLE.bat。', str(error))
        self._start_job(work)

    def stop_backend(self):
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        with self.lock:
            self.process = None
            if self.log:
                self.log.close()
                self.log = None

    def stop(self):
        with self.lock:
            if self.job['state'] in ('preparing', 'configuring', 'starting', 'stopping', 'updating'):
                raise SetupError('当前操作尚未完成，请稍候。')
            self.job = {'state': 'stopping', 'message': '正在停止当前服务…', 'error': None}
        def work():
            try:
                self.stop_backend()
            except (OSError, subprocess.SubprocessError) as error:
                self.set_job('error', '服务未能停止，请重试；当前服务记录已保留。', str(error))
            else:
                self.set_job('idle', '服务已停止，可以重新设置模型。')
        self._start_job(work)

    def close(self):
        self.closed.set()
        with self.lock:
            process = self.configure_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        self.stop_backend()


def choose_folder():
    if os.name != 'nt':
        raise SetupError('请在输入框填写模型目录。')
    # Fixed code, no interpolation of a path or other request text into a shell.
    script = """[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择完整的 Strata-data 模型目录'
$dialog.ShowNewFolderButton = $false
try { if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Write($dialog.SelectedPath) } }
finally { $dialog.Dispose() }
"""
    from serve.winjob import contain
    process = subprocess.Popen(['powershell.exe', '-NoProfile', '-STA', '-Command', script], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    contain(process)
    try:
        output, error = process.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise SetupError('目录选择已超时，请重新打开或手动填写路径。')
    if process.returncode:
        raise SetupError('无法打开目录选择窗口，请手动填写路径。')
    return output.decode('utf-8-sig').strip()


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def log_message(self, *args):
            pass

        def _send(self, status, body, kind='application/json; charset=utf-8'):
            if status >= 400:
                self._discard_request_body()
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8') if isinstance(body, dict) else body
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass

        def _guard(self, *, token=False):
            valid = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if len(self.headers.get_all('Host', [])) != 1 or self.headers.get('Host') not in valid:
                self._reject({'error': '仅允许本机访问模型设置。'})
                return False
            origin = self.headers.get('Origin')
            if len(self.headers.get_all('Origin', [])) > 1 or (origin is not None and origin not in {'http://'+h for h in valid}):
                self._reject({'error': '请从本地模型设置页面操作。'})
                return False
            if token and (len(self.headers.get_all('X-Strata-Setup-Token', [])) != 1 or not hmac.compare_digest(self.headers.get('X-Strata-Setup-Token', '').encode('utf-8'), app.token.encode('ascii'))):
                self._reject({'error': '页面会话已失效，请刷新后再试。'})
                return False
            return True

        def _reject(self, body):
            self._send(403,body)

        def _discard_request_body(self):
            # An unread POST body can reset the TCP connection on Windows before
            # the browser receives the 403. Consume only a small, bounded body;
            # never parse it or let a stalled sender delay rejection indefinitely.
            length = self.headers.get('Content-Length', '')
            if self.command == 'POST' and not getattr(self, '_request_body_read', False):
                deadline = time.monotonic() + .25
                bounded_length = (self.headers.get('Transfer-Encoding') is None
                                  and len(self.headers.get_all('Content-Length', [])) == 1
                                  and len(length) <= 8 and length.isascii() and length.isdigit())
                remaining = min(int(length), BODY_LIMIT) if bounded_length else BODY_LIMIT
                try:
                    while remaining and time.monotonic() < deadline:
                        self.connection.settimeout(max(.001, deadline-time.monotonic()))
                        chunk = self.rfile.read1(min(remaining, 4096))
                        if not chunk:break
                        remaining -= len(chunk)
                except OSError:
                    pass

        def do_GET(self):
            if not self._guard():
                return
            path = urlsplit(self.path).path
            if path == '/api/identity':
                self._send(200, {'service': 'strata-portable-ui', 'app_root': str(app.root),
                                 'version': app.version, 'instance_id': app.instance_id})
                return
            if path == '/':
                body = (app.root/'serve/web/portable-setup.html').read_text(encoding='utf-8')
                body = body.replace('@@TOKEN@@', html.escape(app.token, quote=True)).replace('@@VERSION@@', html.escape(app.version))
                self._send(200, body.encode('utf-8'), 'text/html; charset=utf-8')
                return
            assets = {'/setup.css': ('portable-setup.css', 'text/css; charset=utf-8'),
                      '/setup.js': ('portable-setup.js', 'text/javascript; charset=utf-8')}
            if path in assets:
                name, kind = assets[path]
                self._send(200, (app.root/'serve/web'/name).read_bytes(), kind)
                return
            if path in ('/api/status', '/api/models'):
                if not self._guard(token=True):
                    return
                try:
                    body = app.snapshot() if path == '/api/status' else {'models': discover_models(app.root, app.portable)}
                    self._send(200, body)
                except (OSError, ValueError, RuntimeError) as error:
                    self._send(400, {'error': str(error)})
                return
            if path == '/api/asr/settings':
                if not self._guard(token=True):
                    return
                from serve.asr import config as asr_config
                try:
                    self._send(200, {'settings':asr_config.load(app.root), 'capabilities':asr_config.availability(app.root)})
                except (OSError, ValueError) as error:
                    self._send(400, {'error':str(error)})
                return
            self._send(404, {'error': '页面不存在。'})

        def do_POST(self):
            if not self._guard(token=True):
                return
            if self.headers.get('Transfer-Encoding') is not None:
                self._send(400, {'error': '无效的请求长度。'})
                return
            length = self.headers.get('Content-Length', '')
            limit = 65536 if urlsplit(self.path).path == '/api/asr/settings' else BODY_LIMIT
            if len(self.headers.get_all('Content-Length', [])) != 1 or not length.isascii() or not length.isdigit() or len(length) > 8 or int(length) > limit:
                self._send(413, {'error': '请求过大或缺少长度。'})
                return
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                self._send(415, {'error': '请使用模型设置页面提交。'})
                return
            try:
                from tools.portable_update import update_json
                self.connection.settimeout(5)
                raw = self.rfile.read(int(length))
                self._request_body_read = True
                if len(raw) != int(length):
                    raise SetupError('请求内容不完整。')
                data = update_json(raw)
                if not isinstance(data, dict):
                    raise SetupError('无效的设置内容。')
                path = urlsplit(self.path).path
                if path == '/api/check':
                    if set(data) != {'path'} or not isinstance(data['path'], str) or not data['path'].strip().strip('"') or len(data['path']) > 4096 or any(ord(c) < 32 for c in data['path']):
                        raise SetupError('请选择有效的模型目录。')
                    self._send(200, {'model': model_summary(data['path'].strip().strip('"'), app.portable)})
                elif path == '/api/import':
                    app.begin(data)
                    self._send(202, {'accepted': True})
                elif path == '/api/prepare':
                    app.prepare(data)
                    self._send(202, {'accepted': True})
                elif path == '/api/start' and not data:
                    app.begin(existing=True)
                    self._send(202, {'accepted': True})
                elif path == '/api/stop' and not data:
                    app.stop()
                    self._send(202, {'accepted': True})
                elif path == '/api/asr/settings':
                    from serve.asr import config as asr_config
                    with app.lock:
                        if app.closed.is_set() or app.job['state'] == 'updating':
                            raise SetupError('更新或关闭期间不能修改语音设置，请稍后重试。')
                        self._send(200, {'settings':asr_config.save(app.root, data), 'capabilities':asr_config.availability(app.root)})
                elif path == '/api/network':
                    app.save_network(data)
                    self._send(202, {'accepted': True})
                elif path == '/api/update/check' and not data:
                    app.check_updates()
                    self._send(202, {'accepted': True})
                elif path == '/api/update/start' and not data:
                    app.begin_update()
                    self._send(202, {'accepted': True})
                elif path == '/api/update/backups' and not data:
                    from tools.portable_restore import list_backups
                    self._send(200,list_backups(app.root))
                elif path == '/api/update/restore' and set(data)=={'id'}:
                    if not isinstance(data['id'],str):raise SetupError('无效的回退版本。')
                    app.begin_update(restore_id=data['id'])
                    self._send(202,{'accepted':True})
                elif path == '/api/browse' and not data:
                    if not app.folder_lock.acquire(blocking=False):
                        raise SetupError('目录选择窗口已打开，请先完成选择。')
                    try:
                        self._send(200, {'path': choose_folder()})
                    finally:
                        app.folder_lock.release()
                else:
                    self._send(404, {'error': '操作不存在。'})
            except (OSError, ValueError, RuntimeError) as error:
                self._send(400, {'error': str(error)})
    return Handler


def create_server(app, port=0):
    http = ThreadingHTTPServer(('127.0.0.1', port), handler(app))
    http.daemon_threads = True
    app.http, app.url = http, f'http://127.0.0.1:{http.server_port}/'
    return http


def run(options):
    app = ModelSetup(options=options)
    requested = getattr(options, 'ui_port', None)
    explicit = any(getattr(options, name, None) is not None for name in
                   ('data_dir', 'context', 'backend', 'vision', 'vision_tokens', 'native_video', 'speed'))
    http = None
    for port in ([requested] if requested else UI_PORTS):
        try:
            existing = json_reply(port, '/api/identity', timeout=.15)
        except (OSError, ValueError, RuntimeError):
            existing = None
        if (isinstance(existing, dict) and existing.get('service') == 'strata-portable-ui'
                and existing.get('app_root') == str(app.root) and existing.get('version') == app.version):
            if explicit or getattr(options, 'port', None) is not None:
                raise SetupError(f'模型设置已运行：http://127.0.0.1:{port}/。本次命令参数未应用。请在该页面修改设置，或关闭已有启动器后重试。')
            if not options.no_browser:
                webbrowser.open(f'http://127.0.0.1:{port}/'+('?enter=1' if options.action == 'start' else ''))
            print(f'模型设置已运行：http://127.0.0.1:{port}/', flush=True)
            return 0
        try:
            http = create_server(app, port)
            break
        except OSError:
            continue
    if http is None:
        raise SetupError('模型设置端口被占用，请用 --ui-port 指定一个空闲端口。')
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    app.check_updates()
    print(f'模型设置：{app.url}', flush=True)
    print('在网页中选择模型并启动。关闭此窗口会停止本启动器的服务。', flush=True)
    if not options.no_browser:
        webbrowser.open(app.url+('?enter=1' if options.action == 'start' else ''))
    if options.action == 'start' and app.current_config is not None:
        try:
            if explicit:
                app.begin(app.defaults(), launch=True)
            else:
                app.begin(existing=True)
        except (OSError, ValueError, RuntimeError) as error:
            app.set_job('error', '请重新保存模型设置。', str(error))
    def stop_signal(signum, frame):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, stop_signal)
        while not app.closed.wait(.25):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)
    return 0
