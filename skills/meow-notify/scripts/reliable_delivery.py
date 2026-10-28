"""Durable local MeoW queue. Never log endpoints, events or message text."""
import argparse,datetime,hashlib,json,os,subprocess,sys,time,urllib.error,urllib.request,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BACKOFF=(30,120,900)
MAX_ATTEMPTS=6
if os.name == 'nt':
    import msvcrt
    def acquire(lock): msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    def acquire(lock): fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def log(root,**values):
    with (root/'delivery-log.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(dict(at=stamp(),**values),ensure_ascii=False)+'\n')
def post(payload,root=None,timeout=15):
    root=Path(root or ROOT)
    try:
        cfg=read(root/'settings.json')
        from urllib.parse import urlsplit
        target=urlsplit(cfg['endpoint'])
        if target.scheme!='https' or target.hostname!='api.chuckfang.com' or target.username or target.password:
            return dict(accepted=False,error_type='InvalidEndpoint')
        request=urllib.request.Request(cfg['endpoint'],data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.build_opener(NoRedirect).open(request,timeout=timeout) as response:
            result=json.load(response);status=result.get('status')
            safe_status=int(status) if isinstance(status,(int,str)) and str(status).isdigit() else 'unrecognized'
            return dict(accepted=safe_status==200,http_status=response.status,application_status=safe_status)
    except urllib.error.HTTPError as error:return dict(accepted=False,error_type='HTTPError',http_status=error.code)
    except Exception as error:
        reason=getattr(error,'reason',error)
        return dict(accepted=False,error_type=type(error).__name__,reason_type=type(reason).__name__)
def enqueue(payload,key=None,root=None):
    root=Path(root or ROOT);queue=root/'queue';queue.mkdir(parents=True,exist_ok=True)
    job_id=hashlib.sha256((key or uuid.uuid4().hex).encode()).hexdigest()
    path=queue/(job_id+'.json')
    # Exclusive creation never resets an accepted event or a pending backoff.
    try:
        with path.open('x',encoding='utf-8') as f:
            json.dump(dict(job_id=job_id,payload=payload,status='pending',created_at=stamp(),attempts=0,next_attempt=0),f,ensure_ascii=False,indent=2)
        log(root,event='queued',job=job_id)
    except FileExistsError:pass
    return path
def process_job(path,sender=None,now=None,root=None):
    root=Path(root or ROOT);now=time.time() if now is None else now;job=read(path)
    if job['status'] in ('accepted','failed') or job['next_attempt']>now:return False
    result=sender(job['payload']) if sender else post(job['payload'],root=root)
    job['attempts']+=1;job['last_attempt_at']=stamp()
    if result.get('accepted'):
        job.update(status='accepted',accepted_at=stamp(),next_attempt=0);job.pop('last_error',None);state='accepted'
    else:
        delay=BACKOFF[min(job['attempts']-1,len(BACKOFF)-1)]
        job.update(status='failed' if job['attempts']>=MAX_ATTEMPTS else 'pending',last_error={k:v for k,v in result.items() if k!='accepted'},next_attempt=now+delay);state='failed' if job['status']=='failed' else 'failed-retrying'
    save(path,job)
    (root/'last-status.txt').write_text(stamp()+' '+state,encoding='utf-8')
    log(root,event=state,job=job['job_id'],attempt=job['attempts'],**{k:v for k,v in result.items() if k!='accepted'})
    return True
def start_worker(root=None):
    root=Path(root or ROOT)
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    try:
        proc=subprocess.Popen([sys.executable,str(root/'reliable_delivery.py'),'--worker'],cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags,start_new_session=os.name!='nt')
        return proc.pid
    except Exception as error:
        log(root,event='worker_launch_failed',error_type=type(error).__name__);return None
def worker(root=None):
    root=Path(root or ROOT);root.mkdir(parents=True,exist_ok=True)
    with (root/'delivery-worker.lock').open('a+b') as lock:
        lock.write(b'0');lock.flush();lock.seek(0)
        # Another worker may be exiting just as a new event is queued.
        # Briefly retry the lock so that boundary cannot strand the new event.
        for attempt in range(26):
            try:acquire(lock);break
            except OSError:
                if attempt==25:return 0
                time.sleep(.2)
        while True:
            pending=[]
            for path in sorted((root/'queue').glob('*.json')):
                try:
                    job=read(path)
                    if job['status']=='pending':pending.append((path,job))
                except (OSError,ValueError,KeyError):continue
            state=dict(pid=os.getpid(),checked_at=stamp(),running=bool(pending),pending=len(pending),next_attempt=min((z['next_attempt'] for _,z in pending),default=None))
            save(root/'worker-state.json',state)
            if not pending:return 0
            for path,job in pending:
                if job['next_attempt']<=time.time():
                    try:process_job(path,root=root)
                    except (OSError,ValueError,KeyError):log(root,event='queue_read_or_write_error',job=path.stem)
            time.sleep(5)
def main():
    p=argparse.ArgumentParser();p.add_argument('--worker',action='store_true');a=p.parse_args()
    return worker() if a.worker else 0
if __name__=='__main__':sys.exit(main())
