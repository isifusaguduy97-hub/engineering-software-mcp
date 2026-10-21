"""Initialize, list tools and read status over the actual MCP stdio protocol.

Default behavior never launches a modeling job. --demo explicitly opts into a
small native-software demo; it can take several minutes and writes new outputs.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import time

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))

def unpack(result):
    if result.isError:
        raise RuntimeError('\n'.join(x.text for x in result.content if hasattr(x,'text')))
    if result.structuredContent is not None:return result.structuredContent
    text='\n'.join(x.text for x in result.content if hasattr(x,'text'))
    return json.loads(text)

async def check(args):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env=os.environ.copy();env['PYTHONPATH']=str(PROJECT);env['PYTHONUTF8']='1'
    params=StdioServerParameters(command=sys.executable,args=['-m','engineering_mcp.server','--config',str(args.config.resolve())],cwd=str(PROJECT),env=env)
    print('Starting MCP stdio connection...',file=sys.stderr,flush=True)
    async with stdio_client(params) as (read,write):
        print('MCP subprocess created; initializing...',file=sys.stderr,flush=True)
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=40)) as session:
            initialized=await session.initialize()
            print('MCP initialized; listing tools...',file=sys.stderr,flush=True)
            listing=await session.list_tools()
            names={x.name for x in listing.tools}
            required={'engineering_status','engineering_ping','comsol_rod_heat','fusion_mounting_plate','labview_mean','engineering_job_status','engineering_read_text','engineering_list_artifacts','comsol_solve_mph','engineering_submit_script'}
            if not required <= names:raise AssertionError('Missing tools: '+str(required-names))
            status=unpack(await session.call_tool('engineering_status',{}))
            if not status['enable_scripts']:
                disabled=await session.call_tool('engineering_submit_script',{'software':'labview','source_code':'def run(context): return {}'})
                if not disabled.isError:raise AssertionError('Advanced code execution gate did not reject source')
            # Invalid physical inputs must fail before creating/launching a job.
            invalid=await session.call_tool('comsol_rod_heat',{'length_mm':-1})
            if not invalid.isError:raise AssertionError('Invalid length accepted')
            traversal=await session.call_tool('engineering_job_status',{'job_id':'../outside'})
            if not traversal.isError:raise AssertionError('Path traversal accepted')
            result={'mcp_initialized':True,'protocol_version':initialized.protocolVersion,
                'server':initialized.serverInfo.model_dump(),'tools':sorted(names),'tool_count':len(names),
                'invalid_input_rejected':True,'path_traversal_rejected':True,
                'advanced_scripts_disabled':not status['enable_scripts'],'status':status}
            if args.demo:
                software=args.demo
                result['ping']=unpack(await session.call_tool('engineering_ping',{'software':software}))
                name,parameters={'comsol':('comsol_rod_heat',{}),'fusion':('fusion_mounting_plate',{}),
                    'labview':('labview_mean',{'values':[10,20,30,40,50]})}[software]
                submitted=unpack(await session.call_tool(name,parameters))
                result['submitted']=submitted
                print(json.dumps({'submitted':submitted},ensure_ascii=False),flush=True)
                deadline=time.monotonic()+args.wait
                while time.monotonic()<deadline:
                    state=unpack(await session.call_tool('engineering_job_status',{'job_id':submitted['job_id']}))
                    if state['state'] in ('completed','failed'):
                        result['demo']=state
                        if state['state']!='completed':raise RuntimeError(json.dumps(state,ensure_ascii=False))
                        break
                    await asyncio.sleep(2)
                else:raise TimeoutError('Demo is not finished. Do not resubmit; inspect job '+submitted['job_id'])
            if args.output:
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False,indent=2))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=PROJECT/'config.local.json')
    parser.add_argument('--demo',choices=['comsol','fusion','labview'])
    parser.add_argument('--wait',type=int,default=600)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--official-sdk-client',action='store_true',help='Use the official asynchronous SDK client instead of the default synchronous protocol client')
    args=parser.parse_args()
    if args.official_sdk_client:asyncio.run(check(args))
    else:check_sync(args)

def check_sync(args):
    from protocol_client import ProtocolClient,result_data
    with ProtocolClient(PROJECT,args.config) as client:
        init=client.initialize()
        listing=client.request('tools/list')
        names={x['name'] for x in listing['tools']}
        required={'engineering_status','engineering_ping','comsol_rod_heat','comsol_solve_mph','fusion_mounting_plate','labview_mean','engineering_job_status','engineering_list_artifacts','engineering_read_text','engineering_submit_script'}
        assert required==names
        status=result_data(client.call('engineering_status',{}))
        if not status['enable_scripts']:
            assert client.call('engineering_submit_script',{'software':'labview','source_code':'def run(context): return {}'})['isError']
        assert client.call('comsol_rod_heat',{'length_mm':-1})['isError']
        assert client.call('engineering_job_status',{'job_id':'../outside'})['isError']
        assert client.request('resources/list')['resources']
        assert client.request('resources/read',{'uri':'engineering://capabilities'})['contents']
        assert client.request('prompts/get',{'name':'rod_heat_example'})['messages']
        report={'mcp_initialized':True,'transport':'stdio','protocol_version':init['protocolVersion'],
            'server':init['serverInfo'],'tools':sorted(names),'tool_count':len(names),
            'invalid_input_rejected':True,'path_traversal_rejected':True,
            'advanced_scripts_disabled':not status['enable_scripts'],'status':status}
        if args.demo:
            report['ping']=result_data(client.call('engineering_ping',{'software':args.demo}))
            name,parameters={'comsol':('comsol_rod_heat',{}),'fusion':('fusion_mounting_plate',{}),'labview':('labview_mean',{'values':[10,20,30,40,50]})}[args.demo]
            submitted=result_data(client.call(name,parameters));report['submitted']=submitted
            print(json.dumps({'submitted':submitted},ensure_ascii=False),flush=True)
            deadline=time.monotonic()+args.wait
            while time.monotonic()<deadline:
                state=result_data(client.call('engineering_job_status',{'job_id':submitted['job_id']}))
                if state['state'] in ('completed','failed'):
                    report['demo']=state
                    if state['state']!='completed':raise RuntimeError(json.dumps(state,ensure_ascii=False))
                    break
                time.sleep(2)
            else:raise TimeoutError('Job still running. Do not retry; inspect '+submitted['job_id'])
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
