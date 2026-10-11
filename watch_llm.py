"""Read-only local viewer of actual LLM response content from a running match."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DeepSeek · 实时输出</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#101419;color:#e6edf3;font:15px/1.7 system-ui,sans-serif}header{position:sticky;top:0;background:#101419f5;border-bottom:1px solid #303842;padding:22px 28px;z-index:1}h1{font-size:22px;margin:0 0 6px}#status,.meta{color:#94a3b8;font-size:13px}#error{color:#ffb4a9;white-space:pre-wrap}main{max-width:1050px;margin:auto;padding:24px}article{background:#19212a;border:1px solid #303e4c;border-radius:12px;padding:20px 24px;margin:0 0 18px}.meta{display:flex;justify-content:space-between;margin-bottom:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font:14px/1.65 ui-monospace,monospace;color:#e3ecf5}label{font-size:13px;float:right;color:#acbdd0}#empty{color:#94a3b8;padding:40px 0}a{color:#8bbdff}input{accent-color:#72a8ff}</style>
<header><label><input id="follow" type="checkbox" checked> 新输出置顶</label><h1>DeepSeek · 实时输出</h1><div id="status">正在连接当前对局…</div><div id="error" role="alert"></div></header>
<main><div id="empty">等待模型返回正文…</div><div id="feed"></div></main>
<script>
let cursor=0,queued=[],seen=new Set();
function insert(item){if(seen.has(item.id))return;seen.add(item.id);let card=document.createElement('article'),meta=document.createElement('div'),text=document.createElement('pre');meta.className='meta';meta.textContent=`第 ${item.id} 次回复 · ${item.game_s.toFixed(1)} 游戏秒 · 耗时 ${item.latency_s.toFixed(2)} 秒`;text.textContent=item.content;card.append(meta,text);document.getElementById('feed').prepend(card);document.getElementById('empty').hidden=true;}
async function refresh(){try{const response=await fetch('/api/outputs?after='+cursor,{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||response.statusText);document.getElementById('status').textContent=`${data.model} · ${data.outcome==='running'?'对局进行中':data.outcome} · 已返回 ${data.total} 条正文 · 每秒刷新`;document.getElementById('error').textContent=data.warning||'';queued.push(...data.outputs);cursor=Math.max(cursor,...data.outputs.map(x=>x.id));if(document.getElementById('follow').checked){queued.forEach(insert);queued=[];}}catch(e){document.getElementById('error').textContent='读取失败：'+e.message+'；将继续尝试，不显示伪造输出。';}finally{setTimeout(refresh,1000)}}refresh();
</script></html>'''


def read_outputs(report_path, after=0):
    report=json.loads(report_path.read_text())
    states=report.get('commanders') or ((report.get('samples') or [{}])[-1].get('commanders',[]))
    if not states:raise ValueError('当前对局尚未建立模型反馈记录')
    journal=Path(states[0]['feedback']['journal'])
    content=journal.read_text(); lines=content.splitlines()
    # Appends may be in progress. Expose the incomplete tail instead of calling it a complete response.
    incomplete=bool(content and not content.endswith('\n'))
    if incomplete:lines=lines[:-1]
    requests={};outputs=[]; failures=[]
    for line in lines:
        row=json.loads(line)
        if row['record']=='request_queued':requests[row['request_id']]=row
        if row['record']=='request_finished':
            if row['api_returned'] and isinstance(row.get('response'),str):
                prompt=requests.get(row['request_id'],{}).get('prompt',{})
                outputs.append(dict(id=row['request_id'],game_s=prompt.get('world',{}).get('game_s',0),
                                    latency_s=row['latency_s'],content=row['response']))
            elif not row['api_returned']:failures.append(row)
    warning='正在等待一条日志写入完成。' if incomplete else ''
    if failures and (not outputs or failures[-1]['request_id']>outputs[-1]['id']):
        warning+=' 最新模型请求失败：'+str(failures[-1].get('error'))
    return dict(model=report['identities'][0]['model'],outcome=report['outcome'],total=len(outputs),
                outputs=[o for o in outputs if o['id']>after],warning=warning)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--port',type=int,default=8766)
    args=p.parse_args();report_path=args.report.resolve()
    if not report_path.is_file():p.error('match report does not exist: '+str(report_path))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=urlparse(self.path)
            if path.path=='/':body=HTML.encode();mime='text/html; charset=utf-8';status=200
            elif path.path=='/api/outputs':
                try:
                    after=int(parse_qs(path.query).get('after',['0'])[0]);data=read_outputs(report_path,after);status=200
                except Exception as e:data={'error':str(e)};status=503
                body=json.dumps(data,ensure_ascii=False).encode();mime='application/json; charset=utf-8'
            else:body=b'Not found';mime='text/plain';status=404
            self.send_response(status);self.send_header('Content-Type',mime)
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'LLM_OUTPUT_VIEWER http://127.0.0.1:{args.port} report={report_path}',flush=True)
    try:server.serve_forever()
    finally:server.server_close()

if __name__=='__main__':main()
