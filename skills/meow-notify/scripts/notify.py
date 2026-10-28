"""Completion hook: preserve existing notifier and launch isolated semantic summary."""
import json,os,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parent

def main():
    if os.environ.get('MEOW_SUMMARY_WORKER')=='1':return 0
    cfg=json.loads((ROOT/'settings.json').read_text(encoding='utf-8-sig'))
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    original=cfg.get('previous_notify',[])
    if original and '--test' not in sys.argv:
        try:subprocess.run(original+sys.argv[1:],timeout=15,capture_output=True,creationflags=flags)
        except Exception:pass
    if '--test' in sys.argv:
        event={'type':'agent-turn-complete','input-messages':['将通知改为真实摘要'],
               'last-assistant-message':'已将通知从固定关键词文案改为根据本轮用户请求与最终回复生成简短脱敏摘要。'}
    else:
        try:event=json.loads(sys.argv[-1])
        except (IndexError,ValueError):return 0
        if event.get('type')!='agent-turn-complete':return 0
    if cfg.get('mode') == 'status':
        from reliable_delivery import enqueue, start_worker
        from semantic_notify import event_key, payload_for
        enqueue(payload_for('本轮回复已生成，请返回对话查看。'), event_key(event))
        start_worker()
        return 0
    # Send only current-turn text to the local worker, without paths, account or routing metadata.
    data={key:event.get(key) for key in ('thread-id','turn-id','input-messages','last-assistant-message')}
    worker_flags=flags|getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    proc=subprocess.Popen([sys.executable,'-X','utf8',str(ROOT/'semantic_notify.py')],stdin=subprocess.PIPE,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=worker_flags,cwd=ROOT,start_new_session=os.name!='nt')
    try:proc.stdin.write(json.dumps(data,ensure_ascii=False).encode('utf-8'));proc.stdin.close()
    except (BrokenPipeError,OSError):return 1
    if '--test' in sys.argv:print('Semantic notification worker launched.')
    return 0

if __name__=='__main__':sys.exit(main())
