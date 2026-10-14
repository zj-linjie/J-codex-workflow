#!/usr/bin/env python3
"""Loopback-only UI fixture host. This is NOT a Codex/MCP integration proof."""
import argparse
import copy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.Lock()
BASE = {
    'id': 'development-fixture-only', 'title': '方案调研与独立评审',
    'goal': '核对能力边界，形成可验证的实施建议。', 'planVersion': 1,
    'roles': [
        {'id': 'researcher', 'name': '能力调研员', 'kind': 'native', 'instructions': '核查官方依据，明确已验证与待验证能力。'},
        {'id': 'reviewer', 'name': '独立评审员', 'kind': 'native', 'instructions': '独立检查方案依据和风险。'},
        {'id': 'owner', 'name': '交付验收员', 'kind': 'primary', 'instructions': '验收结果并交付结论。'}],
    'phases': [{'id': 'research', 'name': '并行调研'}, {'id': 'review', 'name': '独立评审'}, {'id': 'delivery', 'name': '验收交付'}],
    'tasks': [
        {'id': 'capability', 'name': '能力核查', 'roleId': 'researcher', 'phaseId': 'research', 'dependsOn': [], 'acceptanceCriteria': '结论可追溯到官方文档或实测。'},
        {'id': 'boundary', 'name': '边界分析', 'roleId': 'researcher', 'phaseId': 'research', 'dependsOn': [], 'acceptanceCriteria': '列出尚未验证的边界。'},
        {'id': 'review', 'name': '方案评审', 'roleId': 'reviewer', 'phaseId': 'review', 'dependsOn': ['capability', 'boundary'], 'acceptanceCriteria': '独立核对依据和实现约束。'},
        {'id': 'delivery', 'name': '结果验收', 'roleId': 'owner', 'phaseId': 'delivery', 'dependsOn': ['review'], 'acceptanceCriteria': '建议覆盖目标并有清晰证据。'}],
    'events': [], 'pauseReason': None}


def fixture(name, revision):
    data = copy.deepcopy(BASE)
    data.update(revision=revision, status='draft', createdAt=datetime.now(timezone.utc).isoformat(), updatedAt=datetime.now(timezone.utc).isoformat())
    for task in data['tasks']:
        task.update(status='pending', nativeId=None, message='', summary='', artifacts=[], acceptance='', attempt=1)
    if name != 'draft':
        data['status'] = 'running'
        data['tasks'][0].update(status='running', nativeId='development-fixture/research', message='正在核对文档与实测依据')
        data['tasks'][1].update(status='running', nativeId='development-fixture/boundary', message='正在列出能力边界')
    if name in ('submitted', 'paused', 'completed', 'partial', 'unavailable', 'usage-error', 'no-speed'):
        data['tasks'][0].update(status='submitted', summary='已核对核心入口，结果等待主代理验收。', artifacts=['docs/能力核查.md'])
        data['tasks'][1].update(status='done', summary='已列出三项能力边界。', acceptance='依据与限制已核验。')
    if name == 'paused':
        data['status'] = 'paused'
        data['pauseReason'] = '能力核查缺少运行时证据，等待你的决定。'
        data['tasks'][0].update(status='failed', message='评审未通过：缺少运行时证据。')
    if name in ('completed', 'partial', 'unavailable', 'usage-error', 'no-speed'):
        data['status'] = 'completed'
        for task in data['tasks']:
            task.update(status='done', summary='结果已提交。', acceptance='结果与验收标准一致。')
    return data


def usage_fixture(name):
    """Synthetic numbers for UI checks only; never read real session logs."""
    stamp = datetime.now(timezone.utc).isoformat()
    def row(model, inputs, cached, outputs, reasoning, speed):
        return {'model': model, 'inputTokens': inputs, 'cachedInputTokens': cached,
                'uncachedInputTokens': inputs-cached, 'outputTokens': outputs,
                'reasoningOutputTokens': reasoning, 'totalTokens': inputs+outputs, 'tokenEvents': 25,
                'speed': None if name == 'no-speed' else {'samples': 20, 'meanTokensPerSecond': speed,
                    'medianTokensPerSecond': speed, 'p10TokensPerSecond': speed-10, 'p90TokensPerSecond': speed+10}}
    models = [row('gpt-6.1-sol', 100000, 80000, 10000, 6000, 45.2),
              row('gpt-6-luna', 60000, 40000, 20000, 10000, 80)]
    totals = {key: sum(item[key] for item in models) for key in ('inputTokens', 'cachedInputTokens', 'uncachedInputTokens', 'outputTokens', 'reasoningOutputTokens', 'totalTokens', 'tokenEvents')}
    data = {'version': 1, 'status': 'partial' if name == 'partial' else 'available',
            'scope': 'session_tree', 'source': 'development_fixture', 'sessionId': 'fixture-main',
            'capturedAt': stamp, 'lastEventAt': stamp, 'totals': totals,
            'speed': None if name == 'no-speed' else {'samples': 40, 'meanTokensPerSecond': 62.6,
                'medianTokensPerSecond': 62.6, 'p10TokensPerSecond': 35.2, 'p90TokensPerSecond': 90},
            'models': models, 'sessions': [{**models[0], 'sessionId': 'fixture-main', 'parentSessionId': None, 'agentRole': 'main'},
                {**models[1], 'sessionId': 'fixture-child', 'parentSessionId': 'fixture-main', 'agentRole': 'WorkflowExecutor'}],
            'coverage': {'sessionsFound': 3 if name == 'partial' else 2, 'sessionsWithUsage': 2,
                         'malformedLines': 0, 'duplicateFiles': 0, 'skippedDuplicateEvents': 0}, 'notes': []}
    if name == 'unavailable':
        data.update(status='unavailable', totals=None, speed=None, models=[], sessions=[], notes=['missing_session_id'])
    return data


STATE = {'run': fixture('draft', 0), 'offline': False, 'polls': 0, 'receipts': [],
         'scene': 'draft', 'usageRequests': 0, 'usageCollections': 0}
HOST = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Workflow 开发预览</title>
<style>body{margin:0;background:#171717;color:#ddd;font:14px system-ui}header{padding:16px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}strong{margin-right:auto}button{background:#333;color:#ddd;border:1px solid #555;border-radius:6px;padding:7px 12px}iframe{border:0;width:100%;height:800px}p{margin:0 16px 12px;color:#aaa}</style>
<header><strong>开发预览 · 非 Codex 宿主</strong><button data-scene="draft">草案</button><button data-scene="running">执行中</button><button data-scene="submitted">待验收</button><button data-scene="paused">暂停</button><button data-scene="completed">完成</button><button data-scene="partial">部分统计</button><button data-scene="unavailable">统计不可用</button><button data-scene="no-speed">无速率样本</button><button data-scene="usage-error">统计重试</button><button id="offline">模拟断连</button><button id="reopen">重新打开卡片</button></header><p>仅使用标注的开发样例，含合成统计数字，测试布局、自动更新和断连提示。终态后需重新打开卡片才能继续预览。</p><iframe id="app" title="工作流看板" src="/board.html"></iframe>
<script>
const frame=document.querySelector('iframe');
addEventListener('message',async e=>{if(e.source!==frame.contentWindow||e.data?.jsonrpc!=='2.0')return;const m=e.data;
 const send=(result,error)=>frame.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,...(error?{error}:{result})},location.origin);
 if(m.method==='ui/initialize'){send({protocolVersion:'2026-01-26',hostInfo:{name:'development-fixture-host',version:'0.1'},hostCapabilities:{},hostContext:{theme:'dark'}});return}
 if(m.method==='ui/notifications/initialized'){const s=await(await fetch('/snapshot')).json();frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{structuredContent:s,content:[]}},location.origin);return}
 if(m.method==='tools/call'){try{const r=await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(m.params)});send(await r.json())}catch(err){send(null,{code:-32603,message:err.message})}}
});
document.querySelectorAll('[data-scene]').forEach(b=>b.onclick=()=>fetch('/scenario',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:b.dataset.scene})}));
document.querySelector('#offline').onclick=()=>fetch('/offline',{method:'POST'});
document.querySelector('#reopen').onclick=()=>{frame.src='/board.html?reopen='+Date.now()};
</script></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, value, content_type='application/json'):
        payload = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/':
            self.send(HOST, 'text/html')
        elif path == '/board.html':
            self.send((ROOT / 'plugin/ui/board.html').read_text(), 'text/html')
        elif path == '/snapshot':
            with LOCK:
                self.send({'run': STATE['run']})
        elif path == '/metrics':
            with LOCK:
                self.send({key: STATE[key] for key in ('polls', 'receipts', 'offline', 'usageRequests', 'usageCollections')})
        else:
            self.send_error(404)

    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 200000:
            self.send_error(413)
            return
        try:
            body = json.loads(self.rfile.read(size) or b'{}')
        except (ValueError, UnicodeDecodeError):
            self.send_error(400)
            return
        with LOCK:
            if self.path == '/scenario':
                if body.get('name') not in ('draft', 'running', 'submitted', 'paused', 'completed', 'partial', 'unavailable', 'usage-error', 'no-speed'):
                    self.send_error(400)
                    return
                STATE['run'] = fixture(body['name'], STATE['run']['revision'] + 1)
                STATE['scene'] = body['name']
                self.send({'run': STATE['run']})
            elif self.path == '/offline':
                STATE['offline'] = not STATE['offline']
                self.send({'offline': STATE['offline']})
            elif self.path == '/rpc':
                if STATE['offline']:
                    self.send({'isError': True, 'content': [{'type': 'text', 'text': '开发样例模拟断连'}]})
                    return
                name, args = body.get('name'), body.get('arguments', {})
                if args.get('runId') != STATE['run']['id']:
                    self.send({'isError': True, 'content': [{'type': 'text', 'text': '运行 ID 不匹配'}]})
                elif name == 'workflow_get':
                    STATE['polls'] += 1
                    self.send({'structuredContent': {'run': STATE['run']}, 'content': []})
                elif name == 'workflow_observed':
                    STATE['receipts'].append(args)
                    STATE['receipts'] = STATE['receipts'][-200:]
                    self.send({'structuredContent': {'acknowledged': True}, 'content': []})
                elif name == 'workflow_usage':
                    STATE['usageRequests'] += 1
                    if STATE['scene'] == 'usage-error':
                        STATE['scene'] = 'completed'
                        self.send({'isError': True, 'content': []})
                    elif STATE['run']['status'] != 'completed':
                        self.send({'isError': True, 'content': []})
                    else:
                        if not STATE['run'].get('usage'):
                            STATE['run']['usage'] = usage_fixture(STATE['scene'])
                            STATE['usageCollections'] += 1
                        self.send({'structuredContent': {'run': STATE['run']}, 'content': []})
                else:
                    self.send({'isError': True, 'content': [{'type': 'text', 'text': '开发宿主仅支持只读工具'}]})
            else:
                self.send_error(404)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print('DEVELOPMENT FIXTURE ONLY: http://127.0.0.1:' + str(httpd.server_port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
