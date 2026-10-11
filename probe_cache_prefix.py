"""受控实验:DeepSeek 前缀缓存到底按什么触发(只读 usage,不做决策)。

目的(回答一个具体问题):payload 的公共前缀已经有 22,840 字符(54.2%),但真实对局的
输入缓存命中率只有 38%。所以"逐字节前缀"这个模型不足以解释实际行为。这里固定 SYSTEM、
只改 USER,逐变量测 cached_tokens。

变量设计(每个变体只动一处):
  same        逐字节完全相同            → 期望全命中(缓存是否可用)
  tail        只改结尾(text 改变)       → 期望命中前面大部分(前缀复用)
  move_cold   把 374 字符的 world 移到最前 → 期望大幅掉(前缀被打断)
  move_hot    把 world 移到末尾          → 期望回升(证明顺序本身就是变量)
  head        改最前面的字符             → 期望基本不命中

先做一次预热(缓存是异步构建的),再逐个按固定间隔发。
输出:logs/cache_prefix_experiment.json
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENDPOINT = 'https://api.deepseek.com/v1/chat/completions'
MODEL = 'deepseek-flash'
INTERVAL_S = float(os.environ.get('PROBE_INTERVAL_S', '6'))
MAX_TOKENS = 1
PROMPT_SOURCE = 'logs/feedback/p1-37uc9yfk.jsonl'


def load_real_prompt():
    """挑一条真实 request_queued:取 system 和 payload 最大的那次。"""
    best = None
    for line in open(ROOT / PROMPT_SOURCE, encoding='utf-8', errors='replace'):
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get('record') != 'request_queued' or not isinstance(row.get('prompt'), dict):
            continue
        size = len(json.dumps(row['prompt'], ensure_ascii=False))
        if best is None or size > best[0]:
            best = (size, row.get('system') or '', row['prompt'])
    if best is None:
        raise SystemExit('no request_queued found in ' + PROMPT_SOURCE)
    return best[1], best[2]


def serialize(payload, cold_first):
    """cold_first=True 时把易变的 world 放到最前(模拟旧顺序)。"""
    if not cold_first:
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    reordered = {}
    if 'world' in payload:
        reordered['world'] = payload['world']
    reordered.update({k: v for k, v in payload.items() if k != 'world'})
    return json.dumps(reordered, ensure_ascii=False, separators=(',', ':'))


def call(key, system, user):
    body = json.dumps({
        'model': MODEL,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'max_tokens': MAX_TOKENS,
        'thinking': {'type': 'disabled'},
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def main():
    key = os.environ.get('DEEPSEEK_API_KEY') or ''
    if not key:
        raise SystemExit('DEEPSEEK_API_KEY missing; source .env first')
    system, payload = load_real_prompt()
    base = serialize(payload, cold_first=False)
    cold = serialize(payload, cold_first=True)

    # tail:只改 payload 最后 40 个字符
    tail = base[:-40] + ('"tail_marker":"%s"}' % ('x' * 20))
    # head:只改 USER 开头 20 个字符
    head = base[:20] + 'z' * 8 + base[20:]

    variants = [
        ('warmup', base),
        ('same', base),
        ('tail', tail),
        ('move_cold', cold),
        ('move_hot', base),
        ('head', head),
        ('same_again', base),
    ]
    report = dict(model=MODEL, endpoint=ENDPOINT, interval_s=INTERVAL_S,
                  prompt_chars=len(base), cold_reordered_chars=len(cold),
                  system_chars=len(system), system_shared_chars=len(os.path.commonprefix([system, system])),
                  variants=[])
    for name, user in variants:
        t0 = time.time()
        try:
            result = call(key, system, user)
            usage = result.get('usage') or {}
            hit = usage.get('prompt_cache_hit_tokens', 0)
            miss = usage.get('prompt_cache_miss_tokens', usage.get('prompt_tokens', 0) - hit)
            entry = dict(name=name, user_chars=len(user), prompt_tokens=usage.get('prompt_tokens'),
                         cache_hit_tokens=hit, cache_miss_tokens=miss,
                         hit_fraction=round(hit / usage['prompt_tokens'], 4) if usage.get('prompt_tokens') else None,
                         latency_s=round(time.time() - t0, 2))
        except Exception as error:
            entry = dict(name=name, user_chars=len(user), error='%s: %s' % (type(error).__name__, error))
        report['variants'].append(entry)
        print(name, json.dumps(entry, ensure_ascii=False), flush=True)
        time.sleep(INTERVAL_S)

    out = ROOT / 'logs/cache_prefix_experiment.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('WROTE', out, flush=True)


if __name__ == '__main__':
    sys.exit(main())
