"""Fork-only reduction of the Windows client crash; no Beam imports or native helpers."""
from pathlib import Path
import hashlib, json, os, shutil, socket, subprocess, threading
root=Path(__file__).resolve().parent
logs=root/'logs';logs.mkdir(exist_ok=True)
lean=Path(subprocess.check_output(['elan','which','lean'],text=True).strip()).parent
build_env=dict(os.environ,PATH=str(lean)+os.pathsep+os.environ['PATH'],LAKE_ARTIFACT_CACHE='false')
subprocess.run([lean/'lake.exe','-d',root,'--no-cache','build','uv-client'],env=build_env,check=True,timeout=180)
exe=root/'.lake/build/bin/uv-client.exe'
env=dict(build_env,PATH=str(lean)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32'))
identity={'version':subprocess.check_output([lean/'lean.exe','--version'],text=True),
          'githash':subprocess.check_output([lean/'lean.exe','--githash'],text=True),
          'hashes':{p.name:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
                    for p in [exe,*lean.glob('lib*shared*.dll')]}}
(logs/'identity.json').write_text(json.dumps(identity,indent=2)+'\n')
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(.2)
stop=threading.Event();errors=[]
def serve():
 while not stop.is_set():
  try:conn,_=listener.accept()
  except socket.timeout:continue
  with conn:
   conn.settimeout(5); data=b'';phase='read'
   try:
    while len(data)<4:
     chunk=conn.recv(4-len(data))
     if not chunk:break
     data+=chunk
    if data!=b'ping':raise AssertionError(data)
    phase='write';conn.sendall(b'pong')
   except Exception as e:errors.append({'phase':phase,'bytes':data.hex(),'error':repr(e)})
thread=threading.Thread(target=serve);thread.start()
results=[];failures=[];debug=[]
try:
 for mode in ['empty','signal','tcp','both']:
  for i in range(512):
   command=[str(exe),mode,str(listener.getsockname()[1])]
   result=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
   item={'mode':mode,'iteration':i,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
   results.append(item)
   if i%64==0 or result.returncode:print('minimal compiled client',mode,i,'exit',result.returncode,flush=True)
   if result.returncode:failures.append((command,item));break
  print('native phase finished:',mode,flush=True)
 gdb=Path(shutil.which('gdb')).resolve()
 captured=False
 for command,failure in failures:
  if 'stack smashing' not in failure['stderr']:continue
  for repeat in range(1024):
   prefix=logs/f"gdb-{failure['mode']}-{repeat:04}"
   script=prefix.with_suffix('.commands')
   script.write_text('\n'.join(['set pagination off','set confirm off','set print thread-events off','set breakpoint pending on','break __stack_chk_fail','run '+' '.join(command[1:]),'python print("BEAM_DIAG_EXIT",gdb.parse_and_eval("$_exitcode"))','thread apply all bt 40','info sharedlibrary','info registers','x/24i $pc-32'])+'\n')
   probe=subprocess.run([gdb,'-nx','-q','-batch','-x',script,'--args',exe],env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
   prefix.with_suffix('.log').write_text(probe.stdout+probe.stderr,encoding='utf-8')
   debug.append({'mode':failure['mode'],'repeat':repeat,'normal':'BEAM_DIAG_EXIT 0' in probe.stdout})
   if repeat%64==0:print('debugger phase',failure['mode'],repeat,flush=True)
   if 'BEAM_DIAG_EXIT 0' not in probe.stdout:
    print(probe.stdout+probe.stderr,flush=True);captured=True;break
  if captured:break
finally:
 stop.set();thread.join(timeout=6);listener.close()
 (logs/'result.json').write_text(json.dumps({'results':results,'serverErrors':errors,'debugger':debug},indent=2)+'\n')
assert not failures, [x[1] for x in failures]
