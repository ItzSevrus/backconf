#!/usr/bin/env python3
"""Backconf system daemon. Run only through systemd."""
import os, sys, json, time, tarfile, socket, signal, threading, pathlib, tempfile, shutil, pwd, grp, stat, traceback
from datetime import datetime, timezone
from backconf_core import *

running=True
operation_lock=threading.Lock()
config_lock=threading.RLock()
last_error=None
last_run=None
last_success=None
target_states={}


def log(msg): print(f'backconfd: {msg}', flush=True)
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def load_config():
    with config_lock:
        cfg=validate_config(read_json(CONFIG))
        return cfg

def save_config(cfg):
    with config_lock:
        validate_config(cfg); atomic_json(CONFIG,cfg,0o640)
        try: os.chown(CONFIG,0,grp.getgrnam(GROUP).gr_gid)
        except (KeyError,PermissionError): pass

def load_state():
    try: return read_json(STATE, {'last_run':None,'last_success':None,'last_error':None,'targets':{}})
    except BackconfError: return {'last_run':None,'last_success':None,'last_error':'State file is invalid','targets':{}}
def save_state(st):
    atomic_json(STATE,st,0o640)
    try: os.chown(STATE,0,grp.getgrnam(GROUP).gr_gid)
    except (KeyError,PermissionError): pass

def make_snapshot(target):
    src=pathlib.Path(target['path'])
    if not src.exists() and not src.is_symlink(): raise BackconfError(f'Target does not exist: {src}')
    tid=target['id']; destdir=pathlib.Path(SNAPSHOTS)/tid; destdir.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    final=destdir/(stamp+'.tar.gz'); tmp=destdir/('.'+stamp+'.tmp')
    try:
        with tarfile.open(tmp,'w:gz',dereference=False) as tf:
            # Store the target itself as a single top-level entry, preserving symlinks rather than following them.
            tf.add(str(src),arcname='payload',recursive=True)
            info={'schema':1,'target':target['path'],'target_id':tid,'created_utc':stamp,'archive':final.name}
            ti=tarfile.TarInfo('BACKCONF-METADATA.json'); raw=json.dumps(info).encode(); ti.size=len(raw); ti.mode=0o600; ti.mtime=time.time()
            import io; tf.addfile(ti,io.BytesIO(raw))
        with tarfile.open(tmp,'r:gz') as tf:
            if not tf.getmembers(): raise BackconfError('Created snapshot is empty.')
            for m in tf.getmembers():
                p=pathlib.PurePosixPath(m.name)
                if p.is_absolute() or '..' in p.parts: raise BackconfError('Unsafe archive path detected.')
        os.chmod(tmp,0o600); os.replace(tmp,final)
        return str(final),stamp
    finally:
        try: tmp.unlink()
        except FileNotFoundError: pass

def run_backups(paths=None):
    global last_error,last_run,last_success
    if not operation_lock.acquire(blocking=False): raise BackconfError('A backup or restore operation is already running.')
    try:
        cfg=load_config(); targets=cfg['targets']; selected=[t for t in targets if paths is None or t['path'] in paths]
        if not selected: raise BackconfError('No configured targets to back up. Add one with `backconf add PATH`.')
        st=load_state(); last_run=now(); st['last_run']=last_run; st['last_run_epoch']=time.time(); errors=[]
        for t in selected:
            target_states[t['path']]={'status':'running','last_backup':st.get('targets',{}).get(t['path'],{}).get('last_backup')}
            try:
                archive,stamp=make_snapshot(t)
                st.setdefault('targets',{})[t['path']]={'last_backup':stamp,'snapshot':archive,'status':'backed up','error':None}
                target_states[t['path']]={'status':'backed up','last_backup':stamp,'snapshot':archive}
                log(f'backup complete: {t["path"]} -> {archive}')
            except Exception as e:
                msg=str(e); errors.append(f'{t["path"]}: {msg}')
                st.setdefault('targets',{})[t['path']]={'last_backup':st.get('targets',{}).get(t['path'],{}).get('last_backup'),'status':'error','error':msg}
                target_states[t['path']]={'status':'error','error':msg}
        if errors:
            last_error='; '.join(errors); st['last_error']=last_error; save_state(st); raise BackconfError(last_error)
        last_success=now(); st['last_success']=last_success; st['last_error']=None; last_error=None; save_state(st)
        return {'created':len(selected),'last_run':last_run,'last_success':last_success}
    finally: operation_lock.release()

def restore_one(target):
    tid=target['id']; d=pathlib.Path(SNAPSHOTS)/tid
    files=sorted(d.glob('*.tar.gz')) if d.exists() else []
    if not files: raise BackconfError(f'No snapshots found for {target["path"]}.')
    archive=files[-1]; dest=pathlib.Path(target['path'])
    fallback_uid=target.get('owner_uid')
    fallback_gid=target.get('owner_gid')
    if fallback_uid is None or fallback_gid is None:
        parts=dest.parts
        if len(parts)>=3 and parts[1]=='home':
            try:
                home_user=pwd.getpwnam(parts[2]); fallback_uid,fallback_gid=home_user.pw_uid,home_user.pw_gid
            except KeyError: fallback_uid=fallback_gid=None
    dest.parent.mkdir(parents=True,exist_ok=True)
    # Extract only the known payload subtree, rejecting traversal and links escaping the target.
    with tarfile.open(archive,'r:gz') as tf:
        members=[]
        for m in tf.getmembers():
            p=pathlib.PurePosixPath(m.name)
            if p.is_absolute() or '..' in p.parts: raise BackconfError('Unsafe path in snapshot; refusing restore.')
            if m.name=='BACKCONF-METADATA.json': continue
            if not (m.name=='payload' or m.name.startswith('payload/')): raise BackconfError('Unexpected archive entry; refusing restore.')
            # Refuse symlinks and hardlinks in archives for safe root extraction.
            if m.issym() or m.islnk() or m.isdev() or m.isfifo(): raise BackconfError('Snapshot contains a link or special file; safe restore refuses it.')
            members.append(m)
        if not members: raise BackconfError('Snapshot has no payload.')
        root_member=next((m for m in members if m.name=='payload'),None)
        if root_member and root_member.isdir():
            dest.mkdir(parents=True,exist_ok=True)
            try:
                uid=fallback_uid if fallback_uid is not None else root_member.uid
                gid=fallback_gid if fallback_gid is not None else root_member.gid
                os.chown(dest,uid,gid); os.chmod(dest,root_member.mode & 0o777)
            except (PermissionError,OverflowError): pass
            for m in members:
                if m.name=='payload': continue
                rel=pathlib.PurePosixPath(m.name).relative_to('payload')
                out=dest.joinpath(*rel.parts)
                # Verify no existing parent is a symlink before writing.
                parent=out.parent
                chain=[]
                while parent != dest.parent and parent != parent.parent:
                    chain.append(parent); parent=parent.parent
                    if parent==dest.parent: break
                if any(p.is_symlink() for p in chain): raise BackconfError(f'Refusing to restore through symlink: {out}')
                if m.isdir():
                    out.mkdir(parents=True,exist_ok=True); os.chmod(out,m.mode & 0o777)
                    try: os.chown(out, (fallback_uid if fallback_uid is not None else m.uid), (fallback_gid if fallback_gid is not None else m.gid))
                    except (PermissionError,OverflowError): pass
                elif m.isfile():
                    out.parent.mkdir(parents=True,exist_ok=True)
                    f=tf.extractfile(m)
                    if f is None: raise BackconfError(f'Cannot read archive entry: {m.name}')
                    with f,open(out,'wb') as o: shutil.copyfileobj(f,o)
                    os.chmod(out,m.mode & 0o777)
                    try: os.chown(out, (fallback_uid if fallback_uid is not None else m.uid), (fallback_gid if fallback_gid is not None else m.gid))
                    except (PermissionError,OverflowError): pass
                    try: os.utime(out,(m.mtime,m.mtime),follow_symlinks=False)
                    except OSError: pass
        elif root_member and root_member.isfile():
            # For a single-file target, restore to the exact configured path.
            dest.parent.mkdir(parents=True,exist_ok=True)
            f=tf.extractfile(root_member)
            if f is None: raise BackconfError('Cannot read archived file.')
            with f,open(dest,'wb') as o: shutil.copyfileobj(f,o)
            os.chmod(dest,root_member.mode & 0o777)
            try: os.chown(dest, (fallback_uid if fallback_uid is not None else root_member.uid), (fallback_gid if fallback_gid is not None else root_member.gid))
            except (PermissionError,OverflowError): pass
        else: raise BackconfError('Snapshot payload type is unsupported.')
    return {'path':str(dest),'snapshot':str(archive)}

def handle(req):
    if not isinstance(req,dict) or not isinstance(req.get('cmd'),str): raise BackconfError('Malformed request.')
    cmd=req['cmd']
    if cmd=='ping': return {'running':True}
    if cmd=='status':
        cfg=load_config(); st=load_state()
        return {'daemon':'running','watch_enabled':cfg['settings']['watch_enabled'],'interval_seconds':cfg['settings']['interval_seconds'],'targets':len(cfg['targets']),'last_run':st.get('last_run'),'last_success':st.get('last_success'),'last_error':st.get('last_error'),'target_states':st.get('targets',{})}
    if cmd=='list':
        cfg=load_config(); st=load_state(); out=[]
        for t in cfg['targets']:
            ts=st.get('targets',{}).get(t['path'],{})
            out.append({'path':t['path'],'status':ts.get('status','pending'),'last_backup':ts.get('last_backup'),'error':ts.get('error')})
        return out
    if cmd=='config_list': return load_config()
    if cmd=='config_get':
        cfg=load_config(); key=req.get('key'); aliases={'interval':'settings.interval_seconds','watch':'settings.watch_enabled','targets':'targets'}; key=aliases.get(key,key)
        cur=cfg
        for part in str(key).split('.'):
            if isinstance(cur,dict) and part in cur: cur=cur[part]
            else: raise BackconfError(f'Unknown configuration key: {req.get("key")}')
        if req.get('key')=='interval': return {'seconds':cur,'human':format_duration(cur)}
        return cur
    if cmd=='config_set':
        cfg=load_config(); key=req.get('key'); value=req.get('value')
        if key=='interval': cfg['settings']['interval_seconds']=parse_duration(value)
        elif key=='watch':
            if str(value).lower() not in ('true','false'): raise BackconfError('watch must be true or false.')
            cfg['settings']['watch_enabled']=str(value).lower()=='true'
        else: raise BackconfError('Supported settings: interval, watch. Targets are managed with add/remove.')
        save_config(cfg); return {'updated':key,'value':value,'applied':True}
    if cmd=='add':
        raw=req.get('path')
        if not isinstance(raw,str) or not raw: raise BackconfError('Provide a path.')
        path=os.path.abspath(os.path.expanduser(raw))
        if not os.path.exists(path): raise BackconfError(f'Path does not exist: {path}')
        path=os.path.realpath(path)
        if path in (SNAPSHOTS,'/etc/backconf','/var/lib/backconf') or path.startswith(SNAPSHOTS+'/'): raise BackconfError('Backconf storage/configuration cannot be a backup target.')
        cfg=load_config()
        if any(t['path']==path for t in cfg['targets']): raise BackconfError(f'Target already exists: {path}')
        st=os.stat(path, follow_symlinks=False)
        owner_uid,owner_gid=st.st_uid,st.st_gid
        # If an earlier Backconf restore accidentally root-owned a home config, infer its user from /home/USER.
        parts=pathlib.Path(path).parts
        if len(parts)>=3 and parts[1]=='home':
            try:
                home_user=pwd.getpwnam(parts[2])
                if owner_uid==0 and path.startswith(home_user.pw_dir.rstrip('/')+'/'):
                    owner_uid,owner_gid=home_user.pw_uid,home_user.pw_gid
            except KeyError: pass
        cfg['targets'].append({'id':target_id(path),'path':path,'owner_uid':owner_uid,'owner_gid':owner_gid}); save_config(cfg)
        return {'added':path}
    if cmd=='remove':
        path=os.path.realpath(os.path.abspath(os.path.expanduser(req.get('path','')))); cfg=load_config(); old=len(cfg['targets']); cfg['targets']=[t for t in cfg['targets'] if t['path']!=path]
        if len(cfg['targets'])==old: raise BackconfError(f'Target not found: {path}')
        save_config(cfg); return {'removed':path,'snapshots_kept':True}
    if cmd=='backup_now': return run_backups()
    if cmd=='watch_stop':
        cfg=load_config(); cfg['settings']['watch_enabled']=False; save_config(cfg); return {'watch_enabled':False}
    if cmd=='restore':
        if not operation_lock.acquire(blocking=False): raise BackconfError('Another backup or restore operation is running.')
        try:
            cfg=load_config(); paths=req.get('paths'); targets=cfg['targets'] if paths==['*'] else [t for t in cfg['targets'] if t['path'] in paths]
            if not targets: raise BackconfError('No matching configured targets.')
            results=[]; errors=[]
            for t in targets:
                try: results.append(restore_one(t))
                except Exception as e: errors.append(f'{t["path"]}: {e}')
            if errors: raise BackconfError('; '.join(errors))
            return results
        finally: operation_lock.release()
    if cmd=='doctor':
        cfg=load_config(); st=load_state(); return {'config_valid':True,'config_path':CONFIG,'snapshot_path':SNAPSHOTS,'targets':len(cfg['targets']),'last_error':st.get('last_error')}
    raise BackconfError(f'Unknown daemon command: {cmd}')

def client(conn):
    try:
        f=conn.makefile('rb'); line=f.readline(2*1024*1024+1)
        if not line or len(line)>2*1024*1024: raise BackconfError('Empty or oversized request.')
        req=json.loads(line.decode('utf-8')); result=handle(req); response={'ok':True,'result':result}
    except Exception as e:
        response={'ok':False,'error':str(e)}
        if not isinstance(e,BackconfError): log(f'request failed: {e}')
    try: conn.sendall((json.dumps(response,ensure_ascii=False)+'\n').encode())
    except OSError: pass
    finally: conn.close()

def scheduler():
    while running:
        try:
            cfg=load_config()
            if cfg['settings']['watch_enabled']:
                st=load_state(); last=st.get('last_run')
                # Use epoch state for scheduling to avoid repeatedly running after restarts.
                due=st.get('last_run_epoch',0)+cfg['settings']['interval_seconds'] <= time.time()
                if due:
                    try: run_backups()
                    except Exception as e: log(f'scheduled backup failed: {e}')
        except Exception as e: log(f'scheduler error: {e}')
        for _ in range(5):
            if not running: break
            time.sleep(1)

def main():
    global running
    if os.geteuid()!=0: print('backconfd must run as root through systemd.',file=sys.stderr); return 2
    for d in ['/etc/backconf','/var/lib/backconf',SNAPSHOTS,'/run/backconf']: os.makedirs(d,mode=0o750,exist_ok=True)
    try:
        grpinfo=grp.getgrnam(GROUP); os.chown('/run/backconf',0,grpinfo.gr_gid); os.chmod('/run/backconf',0o770)
    except KeyError: log('WARNING: backconf group is missing; run sudo backconf setup --repair');
    if not os.path.exists(CONFIG): atomic_json(CONFIG,default_config(),0o640)
    try: validate_config(read_json(CONFIG))
    except Exception as e: log(f'configuration invalid: {e}'); return 2
    try: os.unlink(SOCKET)
    except FileNotFoundError: pass
    srv=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); srv.bind(SOCKET)
    try:
        gid=grp.getgrnam(GROUP).gr_gid; os.chown(SOCKET,0,gid)
    except KeyError: pass
    os.chmod(SOCKET,0o660); srv.listen(32); srv.settimeout(1)
    def stop(*_):
        nonlocal srv
        global running
        running=False
        try: srv.close()
        except OSError: pass
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    threading.Thread(target=scheduler,daemon=True).start(); log('ready; socket permissions configured')
    while running:
        try: c,_=srv.accept()
        except socket.timeout: continue
        except OSError: break
        threading.Thread(target=client,args=(c,),daemon=True).start()
    try: os.unlink(SOCKET)
    except OSError: pass
    return 0
if __name__=='__main__': sys.exit(main())
