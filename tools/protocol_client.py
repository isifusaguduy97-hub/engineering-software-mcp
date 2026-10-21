"""Small synchronous MCP test client for local stdio integration checks."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

class ProtocolClient:
    def __init__(self,project: Path,config: Path):
        env=os.environ.copy();env.update(PYTHONPATH=str(project),PYTHONUTF8='1')
        self.process=subprocess.Popen([sys.executable,'-u','-m','engineering_mcp.server','--config',str(config.resolve())],
            cwd=project,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=None,
            encoding='utf-8',bufsize=1,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.queue=queue.Queue();self.counter=0
        def read():
            for line in self.process.stdout:self.queue.put(line)
            self.queue.put(None)
        self.reader=threading.Thread(target=read,daemon=True);self.reader.start()
    def request(self,method,params=None,timeout=40):
        self.counter+=1
        message={'jsonrpc':'2.0','id':self.counter,'method':method}
        if params is not None:message['params']=params
        self.process.stdin.write(json.dumps(message,ensure_ascii=False,allow_nan=False)+'\n');self.process.stdin.flush()
        while True:
            line=self.queue.get(timeout=timeout)
            if line is None:raise RuntimeError('MCP server exited before replying')
            reply=json.loads(line)
            if reply.get('id')==self.counter:
                if 'error' in reply:raise RuntimeError(str(reply['error']))
                return reply['result']
    def initialize(self):
        result=self.request('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'engineering-protocol-test','version':'1.0.0'}})
        self.process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n');self.process.stdin.flush()
        return result
    def call(self,name,args):return self.request('tools/call',{'name':name,'arguments':args})
    def close(self):
        self.process.stdin.close()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # A running native modeling task must not be killed because a test
            # client stopped waiting. Its job file will receive the result.
            print('Server still finishing a native task; check its job before retrying.',file=sys.stderr)
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def result_data(result):
    if result.get('isError'):raise RuntimeError('\n'.join(x.get('text','') for x in result['content']))
    if result.get('structuredContent') is not None:return result['structuredContent']
    return json.loads(result['content'][0]['text'])
