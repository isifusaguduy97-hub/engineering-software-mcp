from __future__ import annotations
import csv
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from .config import PROJECT
from .jobs import atomic_json

def finite(value, name, positive=False):
    if isinstance(value, bool) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(name + (' must be finite and positive' if positive else ' must be finite'))
    return float(value)

class Backend:
    def __init__(self, config): self.config = config

    def status(self, software='all'):
        result = {}
        if software in ('all', 'comsol'):
            root = self.config.comsol_root
            found = bool(root and (root/'bin/win64/comsolbatch.exe').is_file() and (root/'java/win64/jre/bin/javac.exe').is_file())
            result['comsol'] = {'state': 'installation_found' if found else 'not_configured',
                'root': str(root) if root else None, 'solver_verified': False,
                'note': 'Installation/launcher checks do not prove license availability, convergence or physical accuracy.'}
        for name, runtime in (('fusion',self.config.fusion_runtime),('labview',self.config.labview_runtime)):
            if software not in ('all',name): continue
            file = runtime/'status.json'
            try:
                state = json.loads(file.read_text(encoding='utf-8'))
                age = time.time()-float(state.get('timestamp',0))
                state.update(fresh=-5 <= age < 15, heartbeat_age_seconds=round(age,1), ping_verified=False)
                if not state['fresh']: state['note']='Stale heartbeat. Open the desktop connector/add-in and ping before execution.'
                result[name] = state
            except (OSError,ValueError,TypeError) as exc:
                result[name] = {'state':'not_started','fresh':False,'detail':str(exc)}
        return result

    def call(self, software, args, folder):
        scripts={'comsol':PROJECT/'bridges/comsol/comsol_bridge.py',
                 'fusion':PROJECT/'bridges/fusion/fusion_bridge.py',
                 'labview':PROJECT/'bridges/labview/labview_client.py'}
        command=[sys.executable,str(scripts[software])]
        if software=='comsol':
            if not self.config.comsol_root: raise RuntimeError('Set comsol_root in config.local.json; run Setup.cmd.')
            command += ['--comsol-root',str(self.config.comsol_root)]
        command += [str(x) for x in args]
        folder.mkdir(parents=True,exist_ok=True)
        out,err=folder/'bridge.stdout.log',folder/'bridge.stderr.log'
        # No shell and no invisible desktop activation. A long COMSOL task is
        # observed by the job manager; exceeding a budget never means cancelled.
        with out.open('w',encoding='utf-8') as stdout,err.open('w',encoding='utf-8') as stderr:
            process=subprocess.run(command,cwd=PROJECT,env=self.config.environment(),stdout=stdout,stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,check=False)
        output=out.read_text(encoding='utf-8',errors='replace')
        error=err.read_text(encoding='utf-8',errors='replace')
        if process.returncode: raise RuntimeError(f'{software} task failed: {error[-6000:] or output[-6000:]} Logs: {folder}')
        for line in reversed(output.splitlines()):
            try: return json.loads(line)
            except ValueError: pass
        raise RuntimeError(f'No JSON result from {software}; inspect {out}')

    def ping(self, software):
        folder=self.config.workspace/'work'/'mcp-checks'/str(time.time_ns())
        if software=='comsol': return self.call(software,['diagnose'],folder)
        response=self.call(software,['ping','--timeout','10'],folder)
        if not response.get('ok'): raise RuntimeError(str(response))
        return {'ping_verified':True,'software':software,'result':response['result']}

    def rod(self, folder, p):
        source=(PROJECT/'templates/RodSteadyHeat.java').read_text(encoding='utf-8')
        substitutions={'LENGTH':p['length_mm'],'K':p['conductivity_W_mK'],'LEFT_K':p['left_C']+273.15,
            'RIGHT_K':p['right_C']+273.15,'LEFT_C':p['left_C'],'RIGHT_C':p['right_C'],
            'SAMPLES':p['sample_count'],'ELEMENTS':p['mesh_elements'],'MID_INDEX':p['sample_count']//2}
        for name,value in substitutions.items(): source=source.replace('@@'+name+'@@',str(value))
        if '@@' in source: raise RuntimeError('Unresolved Java template field')
        java=folder/'RodSteadyHeat.java';java.write_text(source,encoding='utf-8')
        result=self.call('comsol',['run',java,'--output',folder/'rod.mph'],folder)
        logs=Path(result['logs'])
        transcript='\n'.join(f.read_text(encoding='utf-8',errors='replace') for f in logs.glob('*.log'))
        start=transcript.find('ROD_DATA_BEGIN')
        end=transcript.find('ROD_DATA_END',start)
        if start < 0 or end < 0 or 'ROD_CHECK_PASS' not in transcript:
            raise RuntimeError('Saved model lacks numerical verification markers; inspect COMSOL logs: '+str(logs))
        data=transcript[start+len('ROD_DATA_BEGIN'):end].strip()
        rows=list(csv.DictReader(io.StringIO(data)))
        if len(rows)!=p['sample_count']: raise RuntimeError('Unexpected sample count')
        numeric=[{key:float(value) for key,value in row.items()} for row in rows]
        if not all(math.isfinite(x) for row in numeric for x in row.values()): raise RuntimeError('Nonfinite solution samples')
        maximum=max(row['absolute_error_K'] for row in numeric)
        midpoint=numeric[len(numeric)//2]['temperature_C']
        expected=(p['left_C']+p['right_C'])/2
        if maximum>1e-6 or abs(midpoint-expected)>1e-6: raise RuntimeError('Temperature accuracy check failed')
        (folder/'temperature_21_points.csv' if len(rows)==21 else folder/'temperature_samples.csv').write_text(data+'\n',encoding='utf-8')
        check={'software':'COMSOL','parameters':p,'sample_count':len(rows),'midpoint_temperature_C':midpoint,
            'expected_midpoint_C':expected,'max_sample_error_K':maximum,'checks_passed':True,
            'heat_flux_W_m2':numeric[0]['heat_flux_W_m2'], 'comsol_logs':str(logs),
            'scope':'Uniform constant-cross-section rod; fixed end temperatures; no source or lateral loss. Native solve and analytic checks passed.'}
        atomic_json(folder/'verification.json',check)
        self._svg(folder/'temperature_profile.svg',numeric,p)
        return check

    def _svg(self, file, rows, p):
        low=min(p['left_C'],p['right_C']);high=max(p['left_C'],p['right_C']);span=max(high-low,1)
        if high==low: low-=0.5;high+=0.5
        def xy(row):return 90+650*row['position_mm']/p['length_mm'],380-280*(row['temperature_C']-low)/(high-low)
        points=' '.join(f'{x:.3f},{y:.3f}' for x,y in map(xy,rows))
        svg=['<svg xmlns="http://www.w3.org/2000/svg" width="820" height="480" viewBox="0 0 820 480">',
            '<rect width="820" height="480" fill="white"/><g font-family="Arial,sans-serif" fill="#18384b">',
            '<text x="90" y="40" font-size="23">COMSOL steady 1D rod temperature</text>',
            '<path d="M90 80V380H750" stroke="#415c70" fill="none"/>']
        for i in range(6):
            x=90+650*i/5;y=380-280*i/5
            svg += [f'<path d="M90 {y}H740" stroke="#e4ebef"/>',
                f'<text x="78" y="{y+5}" font-size="13" text-anchor="end">{low+(high-low)*i/5:.3g}</text>',
                f'<text x="{x}" y="405" font-size="13" text-anchor="middle">{p["length_mm"]*i/5:.3g}</text>']
        svg += [f'<polyline points="{points}" fill="none" stroke="#167d9e" stroke-width="3"/>']
        svg += [f'<circle cx="{x:.3f}" cy="{y:.3f}" r="3.5" fill="#d97824"/>' for x,y in map(xy,rows)]
        svg += ['<text x="420" y="450" font-size="16" text-anchor="middle">Position x (mm)</text>',
            '<text x="24" y="240" transform="rotate(-90 24 240)" font-size="16" text-anchor="middle">Temperature (degC)</text>',
            '<text x="100" y="70" font-size="13">Orange: native COMSOL samples; line: sample connection</text></g></svg>']
        file.write_text('\n'.join(svg),encoding='utf-8')

    def solve_mph(self, folder, model, study):
        result=self.call('comsol',['run',model,'--study',study,'--output',folder/'result.mph'],folder)
        result.update(verified=False,note='Output saved. Inspect batch logs/solution diagnostics and task-specific numerical checks before claiming convergence or correctness.')
        return result

    def scripted_template(self, software, name, folder, parameters):
        source=(PROJECT/'templates'/name).read_text(encoding='utf-8')
        source=source.replace('PARAMETERS = None','PARAMETERS = '+repr(parameters)).replace('OUTPUT_DIR = None','OUTPUT_DIR = '+repr(str(folder)))
        script=folder/'task.py';script.write_text(source,encoding='utf-8')
        reply=self.call(software,['run',script,'--timeout',str(self.config.max_job_seconds)],folder)
        if not reply.get('ok'):raise RuntimeError(str(reply))
        return reply['result']

    def custom(self,software,source,folder):
        if software=='comsol':
            import re
            match=re.search(r'public\s+class\s+([A-Za-z_][A-Za-z_0-9]*)',source)
            if not match:raise ValueError('Java requires a public class and public static Model run()')
            filename=match[1]+'.java'
        else:
            filename='task.py';compile(source,filename,'exec')
        script=folder/filename;script.write_text(source,encoding='utf-8')
        if software=='comsol':return self.call(software,['run',script,'--output',folder/'result.mph'],folder)
        return self.call(software,['run',script,'--timeout',str(self.config.max_job_seconds)],folder)
