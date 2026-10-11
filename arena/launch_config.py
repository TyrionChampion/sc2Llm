"""Local launcher catalogue and strict configuration; never return credentials."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = 'deepseek/deepseek-flash'
RACES = ('terran', 'zerg', 'protoss')


def model_catalog(home=None):
    home = Path(home or Path.home())
    models = [dict(id=OFFICIAL, name='DeepSeek V4.1 Flash', source='DeepSeek 官方 API',
                   available=bool(os.environ.get('DEEPSEEK_API_KEY', '').strip()),
                   notice='使用项目 DEEPSEEK_API_KEY；双方上下文独立。')]
    notices = []
    try:
        auth = json.loads((home / '.local/share/opencode/auth.json').read_text())
        cache = json.loads((home / '.cache/opencode/models.json').read_text())
        for provider in sorted(auth):
            if provider not in cache:
                notices.append('OpenCode 已登录但本地目录缺少：' + provider)
                continue
            for name, info in cache[provider].get('models', {}).items():
                if info.get('status') == 'deprecated' or not info.get('tool_call'):
                    continue
                models.append(dict(id=provider + '/' + name, name=info.get('name', name),
                    source='OpenCode · ' + provider, available=True,
                    notice='使用现有 OpenCode 登录；目录可见不代表额度/地区可用，实际错误会显示。'))
    except (OSError, ValueError, TypeError) as error:
        notices.append('OpenCode 本地目录不可读取：' + str(error))
    preferred = [OFFICIAL, 'opencode-go/kimi-k3', 'opencode-go/qwen3.8-max',
                 'opencode-go/gpt-6-luna', 'opencode-go/deepseek-v4.1-flash']
    models.sort(key=lambda m: (preferred.index(m['id']) if m['id'] in preferred else 99, m['id']))
    return dict(models=models, notices=notices)


def validate_config(value, catalog=None):
    if not isinstance(value, dict) or set(value) != {'players', 'speed', 'minutes', 'reasoning', 'tactics', 'live_skills', 'combat_micro'}:
        raise ValueError('对局配置字段不完整或包含未知字段')
    choices = {m['id']: m for m in (catalog or model_catalog())['models']}
    players = value['players']
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError('必须选择两个玩家')
    for player in players:
        if not isinstance(player, dict) or set(player) != {'model', 'race'}:
            raise ValueError('玩家需要 model 和 race')
        if not isinstance(player['model'], str) or player['model'] not in choices:
            raise ValueError('模型不在当前本地目录中')
        if not choices[player['model']]['available']:
            raise ValueError('模型未配置凭据：' + player['model'])
        if player['race'] not in RACES:
            raise ValueError('种族必须为人族、虫族或神族')
    if value['speed'] not in ('slower', 'normal', 'faster'):
        raise ValueError('未知游戏速度')
    if type(value['minutes']) is not int or not 1 <= value['minutes'] <= 180:
        raise ValueError('时间上限必须为 1～180 分钟')
    if value['reasoning'] not in ('low', 'high'):
        raise ValueError('未知思考强度')
    for flag in ('tactics', 'live_skills', 'combat_micro'):
        if type(value[flag]) is not bool:
            raise ValueError(flag + ' 必须为布尔值')
    if value['tactics'] and any(p['model'] != OFFICIAL for p in players):
        raise ValueError('当前战斗副官只支持双方官方 DeepSeek；请关闭战斗 Agent 或更换模型')
    return json.loads(json.dumps(value))
