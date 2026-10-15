"""Opt-in LAN API settings. Model settings remain loopback-only; keys never appear in status."""
import ipaddress
import socket

PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))


def ipv4_addresses():
    import psutil
    found=set()
    try:
        active=psutil.net_if_stats()
        for name,addresses in psutil.net_if_addrs().items():
            if name in active and not active[name].isup:
                continue
            for address in addresses:
                if address.family != socket.AF_INET:
                    continue
                ip=ipaddress.ip_address(address.address)
                if any(ip in network for network in PRIVATE_NETWORKS):
                    found.add(str(ip))
    except (OSError,ValueError,psutil.Error):
        pass
    return sorted(found,key=lambda ip:(not ip.startswith('192.168.'),not ip.startswith('10.'),ip))[:8]


def normalize(data,current=None):
    if (not isinstance(data,dict) or set(data)-{'scope','api_key','clear_api_key'}
            or data.get('scope') not in ('local','lan')
            or ('clear_api_key' in data and type(data['clear_api_key']) is not bool)):
        raise ValueError('请选择仅本机或局域网访问。')
    value=data.get('api_key','')
    if not isinstance(value,str) or len(value)>256 or any(ord(ch)<33 or ord(ch)>126 for ch in value):
        raise ValueError('API Key 请使用不含空格的 ASCII 字符，最多 256 位。')
    key=value or (current or {}).get('api_key','')
    if data.get('clear_api_key'):
        key=''
    if data['scope']=='lan' and (not isinstance(key,str) or len(key)<16 or len(key)>256
            or any(ord(ch)<33 or ord(ch)>126 for ch in key)):
        raise ValueError('开放局域网必须设置至少 16 位的 API Key，可点击生成密钥。')
    return {'scope':data['scope'],'api_key':key}


def load(portable,config=None):
    saved=portable.read_state().get('portable_api')
    if saved is None:
        return {'scope':'local','api_key':(config or {}).get('api_key','')}
    return normalize(saved)


def public(settings):
    return {'scope':settings['scope'],'api_key_configured':bool(settings.get('api_key')),
            'host':'0.0.0.0' if settings['scope']=='lan' else '127.0.0.1',
            'lan_ips':ipv4_addresses() if settings['scope']=='lan' else []}


def apply(config,settings):
    updated=dict(config)
    updated['host']='0.0.0.0' if settings['scope']=='lan' else '127.0.0.1'
    if settings.get('api_key'):
        updated['api_key']=settings['api_key']
    else:
        updated.pop('api_key',None)
    return updated
