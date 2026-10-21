"""Unit and stdio integration checks. Never invokes engineering applications.

Run with the project's SDK environment:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'tools'))
SCRATCH = PROJECT / 'work'
SCRATCH.mkdir(parents=True, exist_ok=True)

from engineering_mcp.backend import Backend, finite
from engineering_mcp.config import Config
from engineering_mcp.jobs import Jobs, atomic_json
from engineering_mcp.server import build_server
from engineering_mcp.stdio import serve
from protocol_client import ProtocolClient, result_data


def configuration(root: Path, **kwargs) -> Config:
    return Config(root, None, None, root / 'work/fusion-runtime',
                  root / 'work/labview-runtime', **kwargs)


class ScratchCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=SCRATCH, prefix='mcp-unit-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.config = configuration(self.root)


class ConfigTests(ScratchCase):
    def write_config(self, **changes):
        data = {'workspace': 'workspace', 'enable_scripts': False}
        data.update(changes)
        filename = self.root / 'config.json'
        filename.write_text(json.dumps(data), encoding='utf-8-sig')
        return filename

    def test_relative_paths_and_defaults(self):
        config = Config.load(self.write_config(comsol_root='software/comsol'))
        self.assertEqual(config.workspace, self.root / 'workspace')
        self.assertEqual(config.comsol_root, self.root / 'software/comsol')
        self.assertEqual(config.fusion_runtime, self.root / 'workspace/work/fusion-runtime')
        self.assertFalse(config.enable_scripts)

    def test_missing_configuration(self):
        with self.assertRaises(FileNotFoundError):
            Config.load(self.root / 'missing.json')

    def test_scripts_flag_requires_boolean(self):
        for value in ('false', 0, None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Config.load(self.write_config(enable_scripts=value))

    def test_job_budget_limits(self):
        for value in (29, 86401, True, '60', 60.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Config.load(self.write_config(max_job_seconds=value))

    def test_supported_input_inside_workspace(self):
        filename = self.root / 'inputs/model.MPH'
        filename.parent.mkdir()
        filename.write_bytes(b'trusted input placeholder')
        self.assertEqual(self.config.input_file('inputs/model.MPH', ('.mph',)), filename)

    def test_input_traversal_absolute_escape_and_suffix(self):
        outside = self.root.parent / (self.root.name + '-outside.mph')
        outside.write_text('placeholder', encoding='utf-8')
        self.addCleanup(outside.unlink)
        for name in (str(outside), '../' + outside.name):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.config.input_file(name, ('.mph',))
        wrong = self.root / 'model.txt'
        wrong.write_text('placeholder', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.config.input_file(str(wrong), ('.mph',))

    def test_environment_is_local_and_explicit(self):
        environment = self.config.environment()
        self.assertEqual(environment['ENGINEERING_MCP_WORKSPACE'], str(self.root))
        self.assertEqual(environment['ENGINEERING_MCP_FUSION_RUNTIME'], str(self.config.fusion_runtime))
        self.assertEqual(environment['PYTHONUTF8'], '1')


class JobsTests(ScratchCase):
    def setUp(self):
        super().setUp()
        self.jobs = Jobs(self.config)
        self.addCleanup(self.jobs.pool.shutdown, wait=True)

    def wait(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = self.jobs.status(job_id)
            if state['state'] in ('completed', 'failed'):
                # State publication intentionally precedes lock release.
                while self.jobs._locks[state['software']].locked() and time.monotonic() < deadline:
                    time.sleep(0.005)
                return state
            time.sleep(0.005)
        self.fail(f'Background job did not finish: {self.jobs.status(job_id)}')

    def test_worker_persists_immutable_running_and_completed_states(self):
        submitted = self.jobs.submit('comsol', 'mock', {}, lambda folder: {'mean': 4.0})
        state = self.wait(submitted['job_id'])
        self.assertEqual(state['state'], 'completed')
        self.assertEqual(state['result'], {'mean': 4.0})
        folder = self.jobs.directory(submitted['job_id'])
        self.assertEqual(json.loads((folder / 'job.json').read_text())['state'], 'queued')
        snapshots = [json.loads(p.read_text()) for p in sorted((folder / 'states').glob('*.json'))]
        self.assertEqual([item['state'] for item in snapshots], ['running', 'completed'])
        self.assertEqual([item['revision'] for item in snapshots], [1, 2])

    def test_failure_has_diagnostic_and_releases_backend(self):
        def fail(folder):
            raise RuntimeError('mock solver rejected its input')
        state = self.wait(self.jobs.submit('comsol', 'mock', {}, fail)['job_id'])
        self.assertEqual(state['state'], 'failed')
        self.assertIn('mock solver', state['error'])
        self.assertIn('RuntimeError', (self.jobs.directory(state['job_id']) / 'error.log').read_text())
        next_state = self.wait(self.jobs.submit('comsol', 'mock', {}, lambda folder: {})['job_id'])
        self.assertEqual(next_state['state'], 'completed')

    def test_nonserializable_results_fail_without_leaking_lock(self):
        for value in ({'temperature': math.nan}, {'handle': object()}):
            with self.subTest(value=repr(value)):
                state = self.wait(self.jobs.submit('fusion', 'mock', {}, lambda folder: value)['job_id'])
                self.assertEqual(state['state'], 'failed')
                self.assertFalse(self.jobs._locks['fusion'].locked())

    def test_same_backend_is_exclusive_other_backend_can_progress(self):
        started, release = threading.Event(), threading.Event()
        def blocking(folder):
            started.set()
            if not release.wait(3):
                raise RuntimeError('unit test release deadline exceeded')
            return {'ok': True}
        first = self.jobs.submit('fusion', 'mock', {}, blocking)
        try:
            self.assertTrue(started.wait(2))
            self.assertEqual(self.jobs.status(first['job_id'])['state'], 'running')
            with self.assertRaisesRegex(RuntimeError, 'already executing'):
                self.jobs.submit('fusion', 'mock', {}, lambda folder: {})
            second = self.jobs.submit('labview', 'mock', {}, lambda folder: {'mean': 3})
            self.assertEqual(self.wait(second['job_id'])['state'], 'completed')
        finally:
            release.set()
        self.assertEqual(self.wait(first['job_id'])['state'], 'completed')

    def test_corrupt_newer_snapshot_does_not_hide_valid_result(self):
        state = self.wait(self.jobs.submit('comsol', 'mock', {}, lambda folder: {})['job_id'])
        folder = self.jobs.directory(state['job_id'])
        (folder / 'states/999999-incomplete.json').write_text('{', encoding='utf-8')
        self.assertEqual(self.jobs.status(state['job_id'])['state'], 'completed')

    def test_state_write_failure_still_releases_thread_lock(self):
        original = atomic_json
        def failing_snapshot(path, data):
            if path.parent.name == 'states' and 'finished_at' in data:
                raise PermissionError('mock filesystem refusal')
            return original(path, data)
        with mock.patch('engineering_mcp.jobs.atomic_json', side_effect=failing_snapshot):
            submitted = self.jobs.submit('comsol', 'mock', {}, lambda folder: {})
            deadline = time.monotonic() + 3
            while self.jobs._locks['comsol'].locked() and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertFalse(self.jobs._locks['comsol'].locked())
        state = self.wait(self.jobs.submit('comsol', 'mock', {}, lambda folder: {})['job_id'])
        self.assertEqual(state['state'], 'completed')

    def test_job_identifier_rejects_paths(self):
        for name in ('../outside', '', 'g' * 32, '/absolute', 'a' * 33):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.jobs.status(name)
        with self.assertRaises(FileNotFoundError):
            self.jobs.status('a' * 32)

    def test_job_text_limits_and_paths(self):
        state = self.wait(self.jobs.submit('comsol', 'mock', {}, lambda folder: {})['job_id'])
        folder = self.jobs.directory(state['job_id'])
        (folder / 'sample.csv').write_text('0123456789', encoding='utf-8')
        value = self.jobs.read_text(state['job_id'], 'sample.csv', 4)
        self.assertEqual(value['text'], '0123')
        self.assertTrue(value['truncated'])
        outside = self.root / 'outside.txt'
        outside.write_text('private', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.jobs.read_text(state['job_id'], str(outside))
        for limit in (0, 50001):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.jobs.read_text(state['job_id'], 'sample.csv', limit)
        (folder / 'model.mph').write_bytes(b'binary')
        with self.assertRaises(ValueError):
            self.jobs.read_text(state['job_id'], 'model.mph')


class ProtocolTests(ScratchCase):
    def setUp(self):
        super().setUp()
        self.backend = mock.Mock(spec=Backend)
        self.backend.status.return_value = {'comsol': {'state': 'not_configured'}}
        self.backend.ping.return_value = {'ping_verified': True}
        self.jobs = mock.Mock(spec=Jobs)
        self.jobs.submit.return_value = {'job_id': 'a' * 32, 'state': 'queued'}
        self.jobs.status.return_value = {'job_id': 'a' * 32, 'state': 'completed', 'result': {'mean': 4}}
        self.jobs.artifacts.return_value = [{'name': 'result.csv', 'path': str(self.root / 'result.csv'), 'bytes': 8}]
        self.jobs.read_text.return_value = {'text': 'x,y', 'truncated': False, 'path': str(self.root / 'result.csv')}
        with mock.patch('engineering_mcp.server.Backend', return_value=self.backend), \
             mock.patch('engineering_mcp.server.Jobs', return_value=self.jobs):
            self.server = build_server(self.config)

    @staticmethod
    def request(method, params=None, ident=1):
        value = {'jsonrpc': '2.0', 'id': ident, 'method': method}
        if params is not None:
            value['params'] = params
        return value

    def exchange(self, messages, initialize=True):
        if initialize:
            messages = [self.request('initialize', {'protocolVersion': '2025-11-25',
                'capabilities': {}, 'clientInfo': {'name': 'unittest', 'version': '1'}})] + messages
        incoming = io.StringIO(''.join(json.dumps(value, allow_nan=False) + '\n' for value in messages))
        outgoing = io.StringIO()
        serve(self.server, incoming, outgoing)
        replies = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        return replies[1:] if initialize else replies

    def call(self, name, arguments):
        return self.exchange([self.request('tools/call', {'name': name, 'arguments': arguments})])[0]['result']

    def test_initialize_and_protocol_fallback(self):
        replies = self.exchange([self.request('initialize', {'protocolVersion': 'future-version',
            'capabilities': {}, 'clientInfo': {'name': 'unit', 'version': '1'}})], initialize=False)
        self.assertIn('tools', replies[0]['result']['capabilities'])
        self.assertNotEqual(replies[0]['result']['protocolVersion'], 'future-version')

    def test_initialize_required_and_ping_available(self):
        reply = self.exchange([self.request('tools/list')], initialize=False)[0]
        self.assertEqual(reply['error']['code'], -32000)
        reply = self.exchange([self.request('ping')], initialize=False)[0]
        self.assertEqual(reply['result'], {})

    def test_tool_listing_contains_strict_schemas(self):
        tools = self.exchange([self.request('tools/list')])[0]['result']['tools']
        self.assertEqual(len(tools), 10)
        self.assertIn('comsol_rod_heat', {tool['name'] for tool in tools})
        self.assertTrue(all(tool['inputSchema'].get('additionalProperties') is False for tool in tools))

    def test_status_ping_and_job_read_tools(self):
        self.assertFalse(result_data(self.call('engineering_status', {}))['enable_scripts'])
        self.assertTrue(result_data(self.call('engineering_ping', {'software': 'fusion'}))['ping_verified'])
        self.assertEqual(result_data(self.call('engineering_job_status', {'job_id': 'a' * 32}))['state'], 'completed')
        self.assertIn('result', self.call('engineering_list_artifacts', {'job_id': 'a' * 32})['structuredContent'])
        self.assertEqual(result_data(self.call('engineering_read_text', {'job_id': 'a' * 32, 'relative_path': 'result.csv'}))['text'], 'x,y')

    def test_valid_structured_model_calls_submit_only_to_mock(self):
        for name, arguments, software in (
            ('comsol_rod_heat', {}, 'comsol'),
            ('fusion_mounting_plate', {}, 'fusion'),
            ('labview_mean', {'values': [2, 4, 6]}, 'labview'),
        ):
            with self.subTest(name=name):
                self.assertEqual(result_data(self.call(name, arguments))['state'], 'queued')
                self.assertEqual(self.jobs.submit.call_args.args[0], software)
        self.backend.rod.assert_not_called()
        self.backend.scripted_template.assert_not_called()

    def test_invalid_physical_inputs_never_submit(self):
        examples = (
            ('comsol_rod_heat', {'length_mm': -1}),
            ('comsol_rod_heat', {'conductivity_W_mK': 0}),
            ('comsol_rod_heat', {'left_C': -274}),
            ('comsol_rod_heat', {'sample_count': 4}),
            ('comsol_rod_heat', {'sample_count': 1003}),
            ('comsol_rod_heat', {'mesh_elements': 1}),
            ('comsol_rod_heat', {'mesh_elements': 3.5}),
            ('fusion_mounting_plate', {'hole_diameter_mm': 25}),
            ('fusion_mounting_plate', {'corner_radius_mm': 30}),
            ('labview_mean', {'values': []}),
        )
        for name, arguments in examples:
            with self.subTest(name=name, arguments=arguments):
                self.assertTrue(self.call(name, arguments)['isError'])
        self.jobs.submit.assert_not_called()

    def test_unknown_parameters_and_boolean_numbers_rejected(self):
        for arguments in ({'lenght_mm': 100}, {'length_mm': True}, {'mesh_elements': True}, {'length_mm': '100'}):
            with self.subTest(arguments=arguments):
                self.assertTrue(self.call('comsol_rod_heat', arguments)['isError'])
        self.assertTrue(self.call('labview_mean', {'values': [True, 3]})['isError'])
        self.jobs.submit.assert_not_called()

    def test_default_source_gate_is_enforced(self):
        result = self.call('engineering_submit_script', {'software': 'labview', 'source_code': 'def run(context): return {}'})
        self.assertTrue(result['isError'])
        self.assertIn('PermissionError', result['content'][0]['text'])
        self.jobs.submit.assert_not_called()

    def test_mph_path_and_study_validation_precede_submission(self):
        self.assertTrue(self.call('comsol_solve_mph', {'model_path': '../outside.mph'})['isError'])
        model = self.root / 'input.mph'
        model.write_bytes(b'model placeholder')
        self.assertTrue(self.call('comsol_solve_mph', {'model_path': str(model), 'study_tag': '../std'})['isError'])
        self.jobs.submit.assert_not_called()
        self.assertEqual(result_data(self.call('comsol_solve_mph', {'model_path': str(model)}))['state'], 'queued')

    def test_resources_and_prompts(self):
        listing = self.exchange([self.request('resources/list')])[0]['result']['resources']
        self.assertEqual(listing[0]['uri'], 'engineering://capabilities')
        data = self.exchange([self.request('resources/read', {'uri': 'engineering://capabilities'})])[0]['result']
        self.assertEqual(json.loads(data['contents'][0]['text'])['transport'], 'stdio')
        prompts = self.exchange([self.request('prompts/list')])[0]['result']['prompts']
        self.assertEqual(prompts[0]['name'], 'rod_heat_example')
        prompt = self.exchange([self.request('prompts/get', {'name': 'rod_heat_example'})])[0]['result']
        self.assertIn('60', prompt['messages'][0]['content']['text'])

    def test_none_prompt_arguments_supported(self):
        prompt = self.server._prompt_manager.list_prompts()[0]
        original = prompt.arguments
        try:
            prompt.arguments = None
            self.assertEqual(self.exchange([self.request('prompts/list')])[0]['result']['prompts'][0]['arguments'], [])
        finally:
            prompt.arguments = original

    def test_rpc_and_lookup_errors(self):
        for method, params, code in (
            ('does/not/exist', {}, -32601),
            ('tools/call', {'name': 'missing', 'arguments': {}}, -32602),
            ('resources/read', {'uri': 'engineering://missing'}, -32602),
            ('prompts/get', {'name': 'missing'}, -32602),
            ('prompts/get', {'name': 'rod_heat_example', 'arguments': {'typo': 'x'}}, -32602),
            ('prompts/get', {'name': 'rod_heat_example', 'arguments': []}, -32602),
        ):
            with self.subTest(method=method, params=params):
                self.assertEqual(self.exchange([self.request(method, params)])[0]['error']['code'], code)
        self.assertTrue(self.call('engineering_status', [])['isError'])

    def test_invalid_json_and_notifications(self):
        for line, code in (('{broken}\n', -32700), ('[]\n', -32600),
                           ('{"jsonrpc":"2.0","id":1,"method":"ping","params":{"v":NaN}}\n', -32700)):
            outgoing = io.StringIO()
            serve(self.server, io.StringIO(line), outgoing)
            self.assertEqual(json.loads(outgoing.getvalue())['error']['code'], code)
        outgoing = io.StringIO()
        serve(self.server, io.StringIO('{"jsonrpc":"2.0","method":"notifications/initialized"}\n'), outgoing)
        self.assertEqual(outgoing.getvalue(), '')


class BackendTests(ScratchCase):
    def test_finite_rejects_boolean_nonfinite_and_nonpositive(self):
        for value in (True, math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                finite(value, 'parameter')
        for value in (0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                finite(value, 'parameter', positive=True)
        self.assertEqual(finite(5, 'parameter', positive=True), 5.0)

    def test_generalized_rod_template_and_native_result_processing(self):
        for index, (length, conductivity, left, right, count) in enumerate((
            (100.0, 200.0, 100.0, 20.0, 21),
            (250.0, 50.0, -10.0, 60.0, 5),
            (30.0, 120.0, 22.0, 22.0, 3),
        )):
            with self.subTest(left=left, right=right, count=count):
                folder = self.root / f'rod-{index}'
                folder.mkdir()
                logs = folder / 'mock-native-logs'
                logs.mkdir()
                text = io.StringIO()
                writer = csv.writer(text, lineterminator='\n')
                writer.writerow(['position_mm', 'temperature_C', 'temperature_K', 'analytical_temperature_C', 'absolute_error_K', 'heat_flux_W_m2'])
                flux = conductivity * (left - right) / (length * 0.001)
                for sample in range(count):
                    fraction = sample / (count - 1)
                    temperature = left + (right - left) * fraction
                    writer.writerow([length * fraction, temperature, temperature + 273.15, temperature, 0, flux])
                (logs / 'batch.log').write_text('ROD_DATA_BEGIN\n' + text.getvalue() + 'ROD_DATA_END\nROD_CHECK_PASS\n', encoding='utf-8')
                parameters = {'length_mm': length, 'conductivity_W_mK': conductivity,
                    'left_C': left, 'right_C': right, 'sample_count': count, 'mesh_elements': 10}
                backend = Backend(self.config)
                with mock.patch.object(backend, 'call', return_value={'logs': str(logs)}) as call:
                    result = backend.rod(folder, parameters)
                self.assertTrue(result['checks_passed'])
                self.assertAlmostEqual(result['midpoint_temperature_C'], (left + right) / 2)
                self.assertAlmostEqual(result['heat_flux_W_m2'], flux)
                java = (folder / 'RodSteadyHeat.java').read_text(encoding='utf-8')
                self.assertNotIn('@@', java)
                self.assertIn(f'new double[{count}]', java)
                self.assertIn(f'rows[{count // 2}][2]', java)
                self.assertIn(f'"{length}[mm]"', java)
                self.assertEqual(call.call_args.args[0], 'comsol')
                ET.parse(folder / 'temperature_profile.svg')
                filename = 'temperature_21_points.csv' if count == 21 else 'temperature_samples.csv'
                with (folder / filename).open(newline='', encoding='utf-8') as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), count)
                self.assertEqual(float(rows[0]['position_mm']), 0)
                self.assertEqual(float(rows[-1]['position_mm']), length)


class ClientTests(ScratchCase):
    def test_structured_result_does_not_eagerly_parse_content(self):
        self.assertEqual(result_data({'structuredContent': {'ok': True}, 'content': []}), {'ok': True})
        self.assertEqual(result_data({'structuredContent': {}, 'content': [{'text': 'not JSON'}]}), {})
        self.assertEqual(result_data({'content': [{'text': '{"mean":4}'}]}), {'mean': 4})
        with self.assertRaises(RuntimeError):
            result_data({'isError': True, 'content': [{'text': 'failed'}]})

    def test_real_subprocess_stdio_and_default_self_test_do_not_run_models(self):
        filename = self.root / 'config.json'
        filename.write_text(json.dumps({'workspace': str(self.root), 'enable_scripts': False}), encoding='utf-8')
        with ProtocolClient(PROJECT, filename) as client:
            self.assertEqual(client.initialize()['serverInfo']['name'], 'engineering-software-mcp')
            self.assertEqual(len(client.request('tools/list')['tools']), 10)
            self.assertFalse(result_data(client.call('engineering_status', {}))['enable_scripts'])
            self.assertTrue(client.call('comsol_rod_heat', {'length_mm': 0})['isError'])
            self.assertTrue(client.request('resources/list')['resources'])
            self.assertTrue(client.request('prompts/list')['prompts'])
        client.reader.join(timeout=1)
        client.process.stdout.close()
        result = subprocess.run([sys.executable, str(PROJECT / 'tools/self_test.py'), '--config', str(filename)],
            cwd=PROJECT, text=True, encoding='utf-8', capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['mcp_initialized'])
        self.assertEqual(report['tool_count'], 10)
        self.assertFalse(list((self.root / 'outputs/mcp-runs').glob('*/job.json')))


if __name__ == '__main__':
    unittest.main()
