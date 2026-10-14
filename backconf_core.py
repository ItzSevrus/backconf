#!/usr/bin/env python3
"""Shared primitives for Backconf."""
import json, os, tempfile, time, re, socket, pathlib, hashlib
CONFIG='/etc/backconf/config.json'
STATE='/var/lib/backconf/state.json'
SNAPSHOTS='/var/lib/backconf/snapshots'
SOCKET='/run/backconf/control.sock'
GROUP='backconf'
SCHEMA=1

class BackconfError(Exception): pass

def parse_duration(value):
    m=re.fullmatch(r'\s*(\d+)\s*([smhd])\s*', str(value).lower())
    if not m: raise BackconfError('Invalid duration. Use values such as 30s, 5m, 2h, or 1d.')
    n=int(m.group(1)); unit={'s':1,'m':60,'h':3600,'d':86400}[m.group(2)]; seconds=n*unit
    if seconds < 10 or seconds > 365*86400: raise BackconfError('Interval must be between 10 seconds and 365 days.')
    return seconds

def format_duration(seconds):
    for suffix, n in [('d',86400),('h',3600),('m',60)]:
        if seconds and seconds % n == 0: return f'{seconds//n}{suffix}'
    return f'{seconds}s'

def default_config(): return {'schema':SCHEMA,'settings':{'interval_seconds':1800,'watch_enabled':False},'targets':[]}

def validate_config(obj):
    if not isinstance(obj,dict) or obj.get('schema') != SCHEMA: raise BackconfError('Unsupported or missing configuration schema. Run: sudo backconf setup --repair')
    s=obj.get('settings'); t=obj.get('targets')
    if not isinstance(s,dict) or not isinstance(s.get('interval_seconds'),int) or isinstance(s.get('interval_seconds'),bool) or not 10 <= s['interval_seconds'] <= 365*86400 or not isinstance(s.get('watch_enabled'),bool): raise BackconfError('Invalid settings in configuration. Run: sudo backconf setup --repair')
    if not isinstance(t,list): raise BackconfError('Configuration targets must be a list.')
    seen=set()
    for item in t:
        if not isinstance(item,dict) or not isinstance(item.get('path'),str) or not item['path'].startswith('/') or not isinstance(item.get('id'),str): raise BackconfError('Invalid target entry in configuration.')
        if item['path'] in seen: raise BackconfError('Duplicate target in configuration.')
        seen.add(item['path'])
    return obj

def atomic_json(path, obj, mode=0o640):
    path=pathlib.Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=str(path.parent))
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(obj,f,indent=2,ensure_ascii=False); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,mode); os.replace(tmp,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass

def read_json(path, default=None):
    try:
        with open(path,encoding='utf-8') as f: return json.load(f)
    except FileNotFoundError:
        if default is not None: return default
        raise BackconfError(f'File not found: {path}')
    except (json.JSONDecodeError,UnicodeDecodeError) as e: raise BackconfError(f'Invalid JSON in {path}: {e}')

def rpc(request, timeout=120):
    data=(json.dumps(request,separators=(',',':'))+'\n').encode()
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            s.settimeout(timeout); s.connect(SOCKET); s.sendall(data)
            f=s.makefile('rb'); line=f.readline(2*1024*1024+1)
            if not line: raise BackconfError('Daemon closed the connection without a response.')
            if len(line)>2*1024*1024: raise BackconfError('Daemon response exceeded the size limit.')
            response=json.loads(line.decode('utf-8'))
    except PermissionError: raise BackconfError('Permission denied accessing /run/backconf/control.sock. Check `ls -ld /run/backconf; ls -l /run/backconf/control.sock; id -nG`. If `backconf` is missing from the group list, refresh your login with `newgrp backconf` or log out completely and back in. Repair does not refresh an existing login session.')
    except FileNotFoundError: raise BackconfError('Backconf daemon socket is missing. Check `systemctl status backconfd` or run `sudo backconf setup --repair`.')
    except ConnectionRefusedError: raise BackconfError('Backconf daemon is not accepting connections. Check `sudo journalctl -u backconfd -n 80 --no-pager`.')
    except socket.timeout: raise BackconfError('Timed out waiting for Backconf daemon.')
    except OSError as e: raise BackconfError(f'Cannot communicate with Backconf daemon: {e}')
    except (json.JSONDecodeError,UnicodeDecodeError) as e: raise BackconfError(f'Invalid response from daemon: {e}')
    if not response.get('ok'): raise BackconfError(response.get('error','Unknown daemon error'))
    return response.get('result')

def target_id(path): return hashlib.sha256(path.encode()).hexdigest()[:20]
