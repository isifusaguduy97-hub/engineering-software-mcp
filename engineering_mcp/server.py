"""Engineering Software MCP: local stdio transport; logs never use stdout."""
from __future__ import annotations
import argparse
import json
from typing import Literal
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from .backend import Backend, finite
from .config import Config
from .jobs import Jobs

Software=Literal['comsol','fusion','labview']

def build_server(config: Config):
    backend=Backend(config);jobs=Jobs(config)
    server=FastMCP('Engineering Software MCP',instructions='Use licensed local engineering applications. Check status then ping before desktop jobs. Modeling tools return job_id immediately; poll engineering_job_status, inspect verification and artifacts. Do not retry a running/overdue job. Saved MPH alone is not proof of convergence. LabVIEW supports existing VI calls; arbitrary new diagrams/hardware are unverified. Advanced scripts are disabled by default and are not sandboxed.',log_level='WARNING')
    read=ToolAnnotations(readOnlyHint=True,destructiveHint=False,openWorldHint=False)
    write=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False,openWorldHint=False)

    @server.tool(annotations=read)
    def engineering_status(software: Literal['all','comsol','fusion','labview']='all') -> dict:
        """Read configured installation/heartbeat state. ready is not a verified ping or a solver/license test."""
        return {'software':backend.status(software),'enable_scripts':config.enable_scripts,'workspace':str(config.workspace),'version':'1.0.0'}

    @server.tool(annotations=read)
    def engineering_ping(software: Software) -> dict:
        """Handshake with a user-started desktop connector, or check the COMSOL launcher (not its solver/license)."""
        return backend.ping(software)

    @server.tool(annotations=write)
    def comsol_rod_heat(length_mm: float=100,conductivity_W_mK: float=200,left_C: float=100,right_C: float=20,sample_count: int=21,mesh_elements: int=100) -> dict:
        """Submit a native COMSOL 1D steady rod solve, fixed end temperatures, constant area, insulated sides, no heat source. Returns job_id; poll. Exports editable MPH, sampled CSV, SVG curve and analytical verification. Temperatures in degC; samples include endpoints."""
        length_mm=finite(length_mm,'length_mm',True);conductivity_W_mK=finite(conductivity_W_mK,'conductivity_W_mK',True)
        left_C=finite(left_C,'left_C');right_C=finite(right_C,'right_C')
        if min(left_C,right_C)<-273.15:raise ValueError('Temperature cannot be below absolute zero')
        if isinstance(sample_count,bool) or sample_count<3 or sample_count>1001 or sample_count%2!=1:raise ValueError('sample_count must be odd, 3..1001, to include the midpoint')
        if isinstance(mesh_elements,bool) or not 2<=mesh_elements<=10000:raise ValueError('mesh_elements must be 2..10000')
        p=dict(length_mm=length_mm,conductivity_W_mK=conductivity_W_mK,left_C=left_C,right_C=right_C,sample_count=sample_count,mesh_elements=mesh_elements)
        return jobs.submit('comsol','rod_heat',p,lambda folder:backend.rod(folder,p))

    @server.tool(annotations=write)
    def comsol_solve_mph(model_path: str,study_tag: str='std1') -> dict:
        """Submit a study on a trusted input MPH inside configured workspace. Saves a new MPH; convergence and physical validity still require log and numerical review. Never overwrites input."""
        import re
        model=config.input_file(model_path,('.mph',))
        if not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',study_tag):raise ValueError('Invalid study tag')
        return jobs.submit('comsol','solve_mph',{'input':str(model),'study_tag':study_tag},lambda folder:backend.solve_mph(folder,model,study_tag))

    @server.tool(annotations=write)
    def fusion_mounting_plate(length_mm: float=60,width_mm: float=40,thickness_mm: float=5,hole_diameter_mm: float=5,inset_x_mm: float=10,inset_y_mm: float=10,corner_radius_mm: float=3) -> dict:
        """Submit a parametric Fusion plate: underside lower-left origin, X length/Y width/+Z thickness, four through holes at edge insets, four external vertical R fillets. Exports local F3D/STEP/mm STL/isometric view and geometry checks. Fusion add-in must be running; no cloud save."""
        p={name:finite(value,name,True) for name,value in locals().copy().items() if name.endswith('_mm')}
        l,w,d,ix,iy,r=p['length_mm'],p['width_mm'],p['hole_diameter_mm'],p['inset_x_mm'],p['inset_y_mm'],p['corner_radius_mm']
        if ix<=d/2 or iy<=d/2 or l-2*ix<=d or w-2*iy<=d:raise ValueError('Hole insets must keep all four holes inside the plate and separate')
        if r>=min(l,w)/2 or r>min(ix,iy):raise ValueError('Corner radius must fit the plate and remain within both hole insets')
        return jobs.submit('fusion','mounting_plate',p,lambda folder:backend.scripted_template('fusion','fusion_plate.py',folder,p))

    @server.tool(annotations=write)
    def labview_mean(values: list[float]) -> dict:
        """Call installed NI Mean (DBL).vi through the user-started typed ActiveX connector. Exports input CSV and actual mean/error check. Does not create a new VI or invoke hardware."""
        if not 1<=len(values)<=100000:raise ValueError('Provide 1..100000 values')
        p={'values':[finite(v,'values') for v in values]}
        return jobs.submit('labview','mean',p,lambda folder:backend.scripted_template('labview','labview_mean.py',folder,p))

    @server.tool(annotations=read)
    def engineering_job_status(job_id: str) -> dict:
        """Poll a submitted job; completed includes actual results, failed includes error. An overdue task may still run: never automatically retry."""
        return jobs.status(job_id)

    @server.tool(annotations=read)
    def engineering_list_artifacts(job_id: str) -> list[dict]:
        """List a job's exported models/data, source and logs with local paths and byte sizes."""
        return jobs.artifacts(job_id)

    @server.tool(annotations=read)
    def engineering_read_text(job_id: str,relative_path: str,max_chars: int=12000) -> dict:
        """Read text verification, CSV, source or logs inside one job only. Binary models are opened via their file paths."""
        return jobs.read_text(job_id,relative_path,max_chars)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,openWorldHint=True))
    def engineering_submit_script(software: Software,source_code: str) -> dict:
        """ADVANCED TRUSTED CODE ONLY: disabled unless operator sets enable_scripts=true. Execute a reviewed Java COMSOL model or Python Fusion/LabVIEW automation. Runs with the user's permissions; path checks do not sandbox code. Exclusively for authorized engineering automation; no security/OS changes, unrelated shell tasks, license changes or unauthorized hardware/cloud actions. Python exposes run(context), COMSOL exposes public static Model run()."""
        if not config.enable_scripts:raise PermissionError('Advanced source execution disabled. An operator must explicitly set enable_scripts=true in config.local.json after reviewing its permissions.')
        if not 1<=len(source_code)<=1000000:raise ValueError('Source length must be 1..1000000 characters')
        return jobs.submit(software,'trusted_script',{'source_length':len(source_code)},lambda folder:backend.custom(software,source_code,folder))

    @server.resource('engineering://capabilities')
    def capabilities() -> str:
        return json.dumps({'version':'1.0.0','platform':'Windows','transport':'stdio','default_tools':'structured rod/plate/Mean operations; advanced source execution opt-in',
          'tested_native_versions':{'comsol':'6.4.0.429','fusion':'2705.1.30','labview':'2025 Q1 32-bit 25.1f0'},
          'limitations':['Commercial software and licenses not included','Other versions need validation','No remote HTTP service','No verified arbitrary LabVIEW diagram generation or hardware support','MPH saving is not proof of convergence']},ensure_ascii=False)

    @server.prompt()
    def rod_heat_example() -> str:
        return '请用 comsol_rod_heat 建立100 mm长均质杆，k=200 W/(m·K)，左端100℃、右端20℃，侧面绝热、无热源。21个等距点、100个单元。获得job_id后查询engineering_job_status直到完成，读取verification.json核对中点60℃和误差，提供MPH、CSV和SVG曲线路径。'
    # A misspelled engineering parameter must never silently use a default.
    # Strict SDK models also reject booleans disguised as numeric inputs.
    for tool in server._tool_manager.list_tools():
        model = tool.fn_metadata.arg_model
        model.model_config.update(extra='forbid', strict=True)
        model.model_rebuild(force=True)
        tool.parameters = model.model_json_schema()
    return server

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--sdk-transport',action='store_true',help='Use official asyncio transport instead of the default synchronous stdio transport')
    args=parser.parse_args()
    if hasattr(__import__('sys').stdout,'reconfigure'):
        import sys
        sys.stdout.reconfigure(encoding='utf-8',newline='\n')
        sys.stdin.reconfigure(encoding='utf-8')
    server=build_server(Config.load(args.config))
    if args.sdk_transport:server.run(transport='stdio')
    else:
        from .stdio import serve
        serve(server)

if __name__=='__main__':main()
