#!/usr/bin/env python3
"""Backconf command-line interface."""
import argparse, json, os, sys, subprocess, shutil
from backconf_core import BackconfError, rpc, parse_duration, format_duration, CONFIG, SNAPSHOTS
VERSION='3.0.0'

HELP='''Backconf — local, versioned backups for important files

USAGE
  backconf COMMAND [OPTIONS]
  backconf --watch -t 30m

COMMANDS
  setup                 Show setup status; --repair repairs installation
  status                Show daemon, schedule, and backup status
  list                  List configured targets and their backup state
  add PATH              Add a file or directory to backups
  remove PATH           Remove a target but retain its snapshots
  backup-now            Create and verify a snapshot for each target
  restore PATH          Restore the latest snapshot for one target
  restore --all         Restore every configured target
  config list           Print the complete configuration
  config get KEY        Read a setting (interval, watch, targets)
  config set KEY VALUE  Change interval or watch setting
  watch-stop            Disable periodic backups
  logs [-n N]           Show recent daemon journal entries
  doctor                Check daemon and configuration health
  help [COMMAND]        Show this guide or command-specific help
  version               Print version

QUICK START
  sudo ./install.sh
  backconf add ~/.config
  backconf backup-now
  backconf --watch -t 30m
  backconf status

CONFIGURATION
  backconf config list
  backconf config get interval
  backconf config set interval 45m
  backconf config set watch true

RESTORE
  backconf restore ~/.config
  backconf restore --all

Restores merge files and do not delete unrelated extra files. Type 'restore'
to confirm overwrites. Use --yes only when you explicitly accept that risk.
'''

def parser():
    p=argparse.ArgumentParser(prog='backconf',description='Backconf — local, versioned backups for important files',add_help=True,formatter_class=argparse.RawDescriptionHelpFormatter,epilog='Quick start:\n  backconf add ~/.config\n  backconf backup-now\n  backconf --watch -t 30m\n  backconf status')
    p.add_argument('--version',action='version',version=f'Backconf {VERSION}')
    p.add_argument('--watch',action='store_true',help='enable periodic backups')
    p.add_argument('-t','--time',default='30m',metavar='DURATION',help='watch interval (default: 30m; examples: 30m, 2h, 1d)')
    sub=p.add_subparsers(dest='cmd')
    sub.add_parser('setup',help='show setup instructions or repair installation').add_argument('--repair',action='store_true',help='repair service, permissions, and configuration')
    sub.add_parser('status',help='show daemon, schedule, and backup status')
    sub.add_parser('list',help='list configured backup targets')
    sub.add_parser('backup-now',help='create a snapshot of every configured target')
    sub.add_parser('watch-stop',help='disable periodic backups')
    sub.add_parser('doctor',help='check daemon and configuration health')
    lp=sub.add_parser('logs',help='show recent daemon logs'); lp.add_argument('-n',type=int,default=50,help='number of lines (default: 50)')
    for name,helptext in [('add','add a file or directory to backups'),('remove','stop backing up a target; retain snapshots')]:
        q=sub.add_parser(name,help=helptext); q.add_argument('path')
    c=sub.add_parser('config',help='view and edit Backconf configuration'); cs=c.add_subparsers(dest='config_cmd')
    cs.add_parser('list',help='show complete configuration')
    g=cs.add_parser('get',help='read a configuration value'); g.add_argument('key')
    s=cs.add_parser('set',help='update a configuration value'); s.add_argument('key'); s.add_argument('value')
    r=sub.add_parser('restore',help='restore latest snapshot; merges files without deleting extras'); r.add_argument('path',nargs='?'); r.add_argument('--all',action='store_true',help='restore all configured targets'); r.add_argument('--yes',action='store_true',help='skip interactive confirmation')
    return p

def output(value): print(json.dumps(value,indent=2,ensure_ascii=False) if isinstance(value,(dict,list)) else value)
def human_status(d):
    print(f"Backconf daemon: {d['daemon']}")
    print(f"Periodic backups: {'enabled' if d['watch_enabled'] else 'disabled'}")
    print(f"Interval: {format_duration(d['interval_seconds'])} ({d['interval_seconds']} seconds)")
    print(f"Targets: {d['targets']}")
    print(f"Last run: {d.get('last_run') or 'never'}")
    print(f"Last successful run: {d.get('last_success') or 'never'}")
    print(f"Last error: {d.get('last_error') or 'none'}")
def confirm():
    try: answer=input("WARNING: matching files may be overwritten; unrelated extra files will not be deleted. Type 'restore' to confirm: ")
    except (EOFError,KeyboardInterrupt): print('\nCancelled.',file=sys.stderr); return False
    return answer=='restore'
def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0]=='help':
        if len(argv)>1: print(f"Help topic: {argv[1]}\nRun `backconf {argv[1]} --help` for command options.")
        else: print(HELP)
        return 0
    if argv and argv[0]=='version': print(f'Backconf {VERSION}'); return 0
    # argparse normally accepts global options only before subcommands; support the documented global watch form.
    args=parser().parse_args(argv)
    try:
        if args.watch:
            seconds=parse_duration(args.time)
            rpc({'cmd':'config_set','key':'interval','value':args.time})
            rpc({'cmd':'config_set','key':'watch','value':'true'})
            print(f'Periodic backups enabled; interval: {format_duration(seconds)}'); return 0
        if args.cmd is None: print(HELP); return 0
        if args.cmd=='setup':
            if args.repair:
                if os.geteuid()!=0: raise BackconfError('Repair requires root. Run: sudo backconf setup --repair')
                script='/usr/local/lib/backconf/install.sh'
                if os.path.isfile(script): return subprocess.call([script,'--repair'])
                raise BackconfError('Installer repair helper is missing. Re-extract the project and run `sudo ./install.sh --repair`.')
            print('Backconf setup'); print(f'Configuration: {CONFIG}'); print('Run `backconf doctor` to check the installation.'); return 0
        if args.cmd=='logs':
            if args.n<1 or args.n>10000: raise BackconfError('-n must be between 1 and 10000')
            return subprocess.call(['journalctl','-u','backconfd.service','-n',str(args.n),'--no-pager'])
        if args.cmd=='status': human_status(rpc({'cmd':'status'})); return 0
        if args.cmd=='list':
            items=rpc({'cmd':'list'})
            if not items: print('No backup targets configured. Add one with: backconf add PATH'); return 0
            for t in items:
                print(f"[{t['status']}] {t['path']} — last backup: {t.get('last_backup') or 'never'}")
                if t.get('error'): print(f"  error: {t['error']}")
            return 0
        if args.cmd=='add':
            path=os.path.abspath(os.path.expanduser(args.path))
            output(rpc({'cmd':'add','path':path})); return 0
        if args.cmd=='remove':
            path=os.path.abspath(os.path.expanduser(args.path))
            output(rpc({'cmd':'remove','path':path})); return 0
        if args.cmd=='backup-now': output(rpc({'cmd':'backup_now'})); return 0
        if args.cmd=='watch-stop': output(rpc({'cmd':'watch_stop'})); print('Periodic backups disabled.'); return 0
        if args.cmd=='doctor':
            d=rpc({'cmd':'doctor'}); print('Backconf health: OK'); output(d); return 0
        if args.cmd=='config':
            if args.config_cmd is None: print('Usage: backconf config {list,get,set} ...'); return 2
            if args.config_cmd=='list': output(rpc({'cmd':'config_list'})); return 0
            if args.config_cmd=='get': output(rpc({'cmd':'config_get','key':args.key})); return 0
            output(rpc({'cmd':'config_set','key':args.key,'value':args.value})); return 0
        if args.cmd=='restore':
            if bool(args.all)==bool(args.path): raise BackconfError('Choose exactly one: `backconf restore PATH` or `backconf restore --all`.')
            if not args.yes and not confirm(): print('Restore cancelled.'); return 1
            result=rpc({'cmd':'restore','paths':['*'] if args.all else [os.path.realpath(os.path.abspath(os.path.expanduser(args.path)))]},timeout=600)
            for item in result: print(f"[restored] {item['path']} <- {item['snapshot']}")
            return 0
    except BackconfError as e: print(f'backconf: {e}',file=sys.stderr); return 2
    except KeyboardInterrupt: print('\nCancelled.',file=sys.stderr); return 130
    return 0
if __name__=='__main__': sys.exit(main())
