"""Local launcher catalogue and strict configuration; never return credentials."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = 'deepseek/deepseek-flash'
RACES = ('terran', 'zerg', 'protoss')
MAX_PLAYER_PROMPT = 1200


def safe_text(text):
    for name, value in os.environ.items():
        if ('api_key' in name.lower() or name.upper().endswith('_TOKEN')) and value and len(value) > 6:
            text = text.replace(value, '[凭据已隐藏]')
    return text


def model_catalog(home=None):
    home = Path(home or Path.home())
    models = [dict(id=OFFICIAL, name='DeepSeek V4.1 Flash', source='DeepSeek 官方 API',
                   available=bool(os.environ.get('DEEPSEEK_API_KEY', '').strip()),
                   notice='使用项目 DEEPSEEK_API_KEY；双方上下文独立。')]
    notices = []
    if os.environ.get('open_api_key', '').strip():
        models.extend(dict(id='opencode-go/' + name, name='DeepSeek Go · ' + name,
            source='OpenCode Go API', available=True,
            notice='使用主机 open_api_key；独立会话，不自动切换模型。')
            for name in ('deepseek-v4.1-flash', 'deepseek-v4-pro'))
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
                if any(m['id'] == provider + '/' + name for m in models):
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
    fields = {'players', 'speed', 'minutes', 'reasoning', 'tactics', 'live_skills', 'combat_micro'}
    lan = isinstance(value, dict) and value.get('interface') == 'lan'
    if not isinstance(value, dict) or set(value) != fields | ({'interface', 'room_code'} if lan else set()):
        raise ValueError('对局配置字段不完整或包含未知字段')
    if lan and (not isinstance(value['room_code'], str) or len(value['room_code']) != 6
                or any(c not in 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789' for c in value['room_code'])):
        raise ValueError('房间码格式无效')
    choices = {m['id']: m for m in (catalog or model_catalog())['models']}
    players = value['players']
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError('必须选择两个玩家')
    for player in players:
        if not isinstance(player, dict) or set(player) != {'model', 'race'} | ({'prompt'} if lan else set()):
            raise ValueError('玩家需要 model 和 race')
        if not isinstance(player['model'], str) or player['model'] not in choices:
            raise ValueError('模型不在当前本地目录中')
        if not choices[player['model']]['available']:
            raise ValueError('模型未配置凭据：' + player['model'])
        if player['race'] not in RACES:
            raise ValueError('种族必须为人族、虫族或神族')
        if lan and (not isinstance(player['prompt'], str) or not player['prompt'].strip()
                    or len(player['prompt']) > MAX_PLAYER_PROMPT):
            raise ValueError('战术提示词须为 1～%d 字符' % MAX_PLAYER_PROMPT)
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
    if lan and (value['tactics'] or value['live_skills']):
        raise ValueError('房间模式使用单主指挥和内置工具，不启用后台技能开发或副官')
    return json.loads(json.dumps(value))
