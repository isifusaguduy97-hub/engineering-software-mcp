"""Newline-delimited MCP stdio transport for synchronous local tools.

Uses the official SDK's tool schemas, argument validation and result models.
No event-loop wake-up TCP sockets or network listener are needed. Tool calls
that run engineering applications submit background jobs and return promptly.
"""
from __future__ import annotations
import json
import sys
from mcp import types

SUPPORTED=('2024-11-05','2025-03-26','2025-06-18','2025-11-25')
MAX_MESSAGE_BYTES=8*1024*1024

class RpcError(Exception):
    def __init__(self, code, message):self.code,self.message=code,message

def serve(server, input_stream=None, output_stream=None):
    incoming=input_stream or sys.stdin
    outgoing=output_stream or sys.stdout
    initialized=False
    def send(value):
        outgoing.write(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
        outgoing.flush()
    def tool_list():
        values=[]
        for tool in server._tool_manager.list_tools():
            data={'name':tool.name,'description':tool.description,'inputSchema':tool.parameters}
            if tool.output_schema is not None:data['outputSchema']=tool.output_schema
            if tool.annotations is not None:data['annotations']=tool.annotations.model_dump(mode='json',exclude_none=True)
            values.append(data)
        return {'tools':values}
    def dispatch(method,params):
        nonlocal initialized
        if method=='initialize':
            if not isinstance(params.get('clientInfo'),dict) or not isinstance(params.get('protocolVersion'),str):raise RpcError(-32602,'initialize requires clientInfo and protocolVersion')
            initialized=True
            version=params['protocolVersion'] if params['protocolVersion'] in SUPPORTED else types.LATEST_PROTOCOL_VERSION
            return {'protocolVersion':version,'serverInfo':{'name':'engineering-software-mcp','version':'1.0.0'},
                'capabilities':{'tools':{'listChanged':False},'resources':{'subscribe':False,'listChanged':False},'prompts':{'listChanged':False}},
                'instructions':server.instructions}
        if method=='ping':return {}
        if not initialized:raise RpcError(-32000,'Initialize the MCP connection first')
        if method=='tools/list':return tool_list()
        if method=='tools/call':
            name=params.get('name')
            tool=next((tool for tool in server._tool_manager.list_tools() if tool.name==name),None)
            if tool is None:raise RpcError(-32602,'Unknown tool: '+str(name))
            try:
                arguments=params.get('arguments',{})
                if not isinstance(arguments,dict):raise ValueError('arguments must be an object')
                pre=tool.fn_metadata.pre_parse_json(arguments)
                parsed=tool.fn_metadata.arg_model.model_validate(pre).model_dump_one_level()
                value=tool.fn(**parsed)
                converted=tool.fn_metadata.convert_result(value)
                if isinstance(converted,types.CallToolResult):result=converted
                elif isinstance(converted,tuple):result=types.CallToolResult(content=converted[0],structuredContent=converted[1],isError=False)
                else:result=types.CallToolResult(content=converted,isError=False)
                return result.model_dump(mode='json',by_alias=True,exclude_none=True)
            except Exception as exc:
                return types.CallToolResult(content=[types.TextContent(type='text',text=f'{type(exc).__name__}: {exc}')],isError=True).model_dump(mode='json',by_alias=True,exclude_none=True)
        if method=='resources/list':
            return {'resources':[{'uri':str(x.uri),'name':x.name,'description':x.description or '', 'mimeType':x.mime_type or 'text/plain'} for x in server._resource_manager.list_resources()]}
        if method=='resources/templates/list':return {'resourceTemplates':[]}
        if method=='resources/read':
            resource=next((x for x in server._resource_manager.list_resources() if str(x.uri)==params.get('uri')),None)
            if resource is None:raise RpcError(-32602,'Unknown resource URI')
            return {'contents':[{'uri':str(resource.uri),'mimeType':resource.mime_type or 'text/plain','text':str(resource.fn())}]}
        if method=='prompts/list':
            return {'prompts':[{'name':p.name,'description':p.description or '',
                'arguments':[a.model_dump(exclude_none=True) for a in (p.arguments or [])]} for p in server._prompt_manager.list_prompts()]}
        if method=='prompts/get':
            prompt=next((x for x in server._prompt_manager.list_prompts() if x.name==params.get('name')),None)
            if prompt is None:raise RpcError(-32602,'Unknown prompt')
            arguments=params.get('arguments',{})
            allowed={a.name for a in (prompt.arguments or [])}
            if not isinstance(arguments,dict) or set(arguments)-allowed:raise RpcError(-32602,'Invalid prompt arguments')
            if any(not isinstance(v,str) for v in arguments.values()):raise RpcError(-32602,'Prompt arguments must be strings')
            if any(a.required and a.name not in arguments for a in (prompt.arguments or [])):raise RpcError(-32602,'Missing required prompt argument')
            return {'description':prompt.description or '', 'messages':[{'role':'user','content':{'type':'text','text':str(prompt.fn(**arguments))}}]}
        if method=='completion/complete':return {'completion':{'values':[],'total':0,'hasMore':False}}
        if method=='logging/setLevel':return {}
        raise RpcError(-32601,'Method not found: '+str(method))

    while True:
        line=incoming.readline(MAX_MESSAGE_BYTES+1)
        if not line:break
        request=None
        ident=None
        try:
            if len(line.encode('utf-8'))>MAX_MESSAGE_BYTES:raise RpcError(-32600,'Message too large')
            try:request=json.loads(line,parse_constant=lambda value:(_ for _ in ()).throw(ValueError(value)))
            except ValueError:raise RpcError(-32700,'Invalid JSON')
            if not isinstance(request,dict) or request.get('jsonrpc')!='2.0' or not isinstance(request.get('method'),str):raise RpcError(-32600,'Invalid JSON-RPC request')
            ident=request.get('id')
            if 'id' not in request:
                # Job execution is separate from a call's lifetime. A cancelled
                # tool request does not kill a native application's running job.
                continue
            if isinstance(ident,bool) or not isinstance(ident,(int,str)):raise RpcError(-32600,'Invalid request id')
            params=request.get('params',{})
            if not isinstance(params,dict):raise RpcError(-32602,'params must be an object')
            result=dispatch(request['method'],params)
            send({'jsonrpc':'2.0','id':ident,'result':result})
        except RpcError as exc:
            send({'jsonrpc':'2.0','id':ident,'error':{'code':exc.code,'message':exc.message}})
        except Exception as exc:
            send({'jsonrpc':'2.0','id':ident,'error':{'code':-32603,'message':str(exc)}})
