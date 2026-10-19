"""Fork-only probe: compile and stress an application using only Lean's UV APIs."""
from pathlib import Path
import json, os, shutil, socket, subprocess, sys, threading
root=Path(__file__).resolve().parent
logs=root/'logs';logs.mkdir(exist_ok=True)
lean=Path(subprocess.check_output(['elan','which','lean'],text=True).strip()).parent
build_env=dict(os.environ,PATH=str(lean)+os.pathsep+os.environ['PATH'],LAKE_ARTIFACT_CACHE='false')
subprocess.run([lean/'lake.exe','-d',root,'--no-cache','build','uv-client'],env=build_env,check=True,timeout=180)
exe=root/'.lake/build/bin/uv-client.exe'
env=dict(build_env,PATH=str(lean)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32'))
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(.2)
stop=threading.Event();errors=[]
def serve():
 while not stop.is_set():
  try:conn,_=listener.accept()
  except socket.timeout:continue
  with conn:
   conn.settimeout(5)
   try:
    data=b''
    while len(data)<4:
     chunk=conn.recv(4-len(data))
     if not chunk:break
     data+=chunk
    if data!=b'ping':raise AssertionError(data)
    conn.sendall(b'pong')
   except Exception as e:errors.append(repr(e))
thread=threading.Thread(target=serve);thread.start()
results=[]
try:
 for i in range(256):
  command=[str(exe),str(listener.getsockname()[1])]
  result=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
  results.append({'iteration':i,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
  print('minimal compiled client',i,'exit',result.returncode,flush=True)
  if result.returncode:
   gdb=Path(shutil.which('gdb')).resolve()
   for repeat in range(64):
    prefix=logs/f'gdb-{repeat:03}'
    script=prefix.with_suffix('.commands')
    script.write_text('\n'.join(['set pagination off','set confirm off','set print thread-events off','set breakpoint pending on','break __stack_chk_fail','run '+command[1],'python print("BEAM_DIAG_EXIT",gdb.parse_and_eval("$_exitcode"))','thread apply all bt 40','info sharedlibrary','info registers','x/24i $pc-32'])+'\n')
    probe=subprocess.run([gdb,'-nx','-q','-batch','-x',script,'--args',exe],env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    prefix.with_suffix('.log').write_text(probe.stdout+probe.stderr,encoding='utf-8')
    if 'BEAM_DIAG_EXIT 0' not in probe.stdout:break
   break
finally:
 stop.set();thread.join(timeout=6);listener.close()
 (logs/'result.json').write_text(json.dumps({'results':results,'serverErrors':errors},indent=2)+'\n')
assert len(results)==256 and all(x['returncode']==0 for x in results) and not errors,results[-1]
