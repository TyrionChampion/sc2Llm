# SC2 LLM Arena

让两个大语言模型在 StarCraft II 中实时对战。支持人族、虫族和神族，
双方独立观察战场、维护上下文、发布任务并接收引擎执行反馈。

本项目从 `wolf/sc2-arena` 独立迁出，本机目录为
`/home/tyrion/Projects/sc2llm`。

## 功能与入口

- `arena_launcher.py`、`arena/launcher.html`：本地对战控制台，选择模型、
  种族和速度，启动/结束比赛，查看双方输出与状态。
- `official_duel.py`、`opencode_match.py`、`session_match.py`、`native_llm.py`：
  官方 API、OpenCode 会话和原生对战入口。
- `arena/commander.py`、`arena/battle_agent.py`、`arena/tasks.py`：模型指挥、
  战斗决策、任务调度、实时微操和反馈。
- `arena/economy.py`、`arena/technology.py`、`arena/tactics.py`、
  `arena/defense_orders.py`：运营、技术树、战术和防守。
- `arena/python_skills.py`、`arena/skill_lab.py`、`arena/live_skills.py`：
  Python 技能执行、隔离验收及现场技能开发。
- `arena/native_engine.py`、`arena/raw_engine.py`：原生 SC2 协议接口和观察解析。

控制台支持官方 DeepSeek 与已经登录的 OpenCode 模型混搭。其他 API 供应商
适配位于 `arena/llm.py`，具体入口的支持范围见对应命令帮助。

## 安装 Python 依赖

使用 Linux 和 Python 3.11。依赖版本记录自当前本机可用环境，PySC2 的本地
兼容修改随源码保留。新机器可以运行：

```bash
git clone https://github.com/TyrionChampion/sc2Llm.git
cd sc2Llm
python3.11 -m venv .venv311
.venv311/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 填入自己的模型密钥，或预先配置 OpenCode 登录。`.env` 不提交。
迁移后的本机已经保留原有 Python 环境及配置，无需重新复制 `.env`。

## 准备游戏运行环境

Git 仓库包含代码、技能契约和参考数据；本机游戏及运行工具需另外安装。

1. 将 Linux SC2 安装及地图放在 `engine/StarCraftII/`。默认地图使用
   `Maps/Ladder2019Season3/AcropolisLE.SC2Map`，其他速度的地图由项目生成。
2. 原生玩家窗口模式使用 Windows 版 SC2。通过 `.env` 中的
   `ARENA_SC2_SOURCE` 指定含 `Versions/`、`Support64/`、`Interfaces/`、
   `.build.info`、`.product.db` 的安装目录，并将完整 `SC2Data` 内容复制到
   `native-runtime/data-copy/`，其中必须存在 `data/` 子目录。
3. 安装 GE-Proton、Steam、Bubblewrap（`bwrap`）及可用显卡驱动。
   默认 GE-Proton 位置为
   `~/.local/share/Steam/compatibilitytools.d/GE-Proton11-7-x86_64`，可用
   `ARENA_PROTON_PATH` 和 `ARENA_STEAM_PATH` 覆盖。当前原生接口使用
   `Versions/Base97579/SC2_x64.exe`，需要匹配的游戏版本。
4. Linux RGB 观察入口还需要 `osmesa-lib/libOSMesa.so.8` 及相应动态库。
   隔离技能执行需要允许 Bubblewrap 创建命名空间。

迁移后的本机保留了这些已有资源。引擎、动态库、Proton、虚拟环境、
模型私有技能库、登录信息、对局日志和录像均在 `.gitignore` 中。

## 启动控制台

```bash
./start_arena.sh
```

浏览器地址为 <http://127.0.0.1:8767/>。控制台监听本机回环地址；点击开始
后才启动模型和游戏。详细行为、运行记录及模型限制见 [LAUNCHER.md](LAUNCHER.md)。

## 验证

### OpenCode Go DeepSeek 对战困难 bot（Linux 无窗口）

2026-10-13 已完成新的困难 Terran 引擎胜利：原账本第37局使用 Go DeepSeek V4.1 Flash、
双方 Slower、realtime，531.6游戏秒结束，20次战斗请求，引擎动作错误0。
[比赛报告](logs/autonomous-bot/20261012-093031-xhnbux9u/match-37-hard/match.json)、
[回放](logs/autonomous-bot/20261012-093031-xhnbux9u/match-37-hard/match.SC2Replay)及
[核验记录](logs/autonomous-bot/20261012-093031-xhnbux9u/match-37-hard/victory-validation.json)
均已保留。战役已自动停止；累计840次，前一轮278次计数保持。
完整续跑结果见 [CLOUD_PROGRESS.md](CLOUD_PROGRESS.md)。这是一次已核验胜利，尚未评估稳定胜率。

已有交接账本的自主续跑以 [CLOUD_HANDOFF.md](CLOUD_HANDOFF.md) 为准：

```bash
./run_sc2.sh .venv/bin/python autonomous_bot.py \
  --resume logs/autonomous-bot/20261012-093031-xhnbux9u \
  --until-hard-win --game-speed slower
```

继续原有比赛编号和计数，每局最多24次战斗请求、1800游戏秒、1860墙钟秒。
模型与复盘均使用 Go，推理强度 low。`--switch-model` 只在首次切换时需要；
后续续跑沿用账本模型。子进程使用当前 Python 解释器；无需固定 `.venv311`。
当前账本已切换为 Go。首次切换其他旧账本时加 `--switch-model opencode-go/deepseek-v4.1-flash`。
Slower 使用已校验的真实引擎速度地图，双方始终同速、保持 realtime；不会通过降低
观测频率模拟慢速。墙钟上限不变，Slower 下约覆盖 780 游戏秒，模型收到剩余墙钟时间
及预计游戏截止时间。省略 `--game-speed` 会保留账本已有速度。
Go 保持已验证的 `json_object` 格式；原生 `json_schema` 在实战被服务端拒绝，默认关闭。
执行前仅转译模型已明确给出的空间任务人数层级、已观测单位名拼写及空字段；原始响应和
转译记录分别保存在每次请求目录，数量、单位种类、目的地和攻守意图由模型决定。
回归测试额外依赖见 `requirements-dev.txt`；运行 Go 不需安装 JSON Schema 验证库。

使用 Go 套餐密钥环境变量 `open_api_key`。主指挥和独立战斗模型均请求
`https://opencode.ai/zen/go/v1/chat/completions`，默认 `deepseek-v4.1-flash`，
独立评估入口关闭思考以降低实时延迟；自主续跑入口沿用 low 思考。
不会自动改用官方 API、Zen 或 mock。自主续跑由同一 DeepSeek 指挥生产、科技、
侦察、防守和进攻；独立评估入口另有模型小队战术。
真实模型模式从开局开始由模型下令，不注入脚本开局。

```bash
./run_sc2.sh .venv/bin/python evaluate_realtime.py \
  --provider opencode_go --model deepseek-v4.1-flash \
  --difficulty hard --games 1 --seconds 1800 \
  --tactics --combat-micro --output logs/go_hard.json
```

需要 Linux SC2 引擎及 Acropolis 地图，不需要 GPU、Proton 或 OpenCode CLI。
结果 JSON 每 15 秒保存进度，记录种族、难度、种子、模型、用量和终局；
同目录 `*_feedback/seed_N/` 保存真实请求、公开回复和执行反馈，录像在
游戏的 `Replays/`。只有引擎返回 `Victory` 才计为胜利，超时和错误单独记录。
单局胜利不能代表稳定胜率，可用 `--games 3 --seed 7` 验证不同种子。

云端可用 Python 3.12 创建 `.venv`，安装随仓库的 PySC2 及依赖。
原生窗口模式仍按上文使用原机器的 Python 3.11 环境。

```bash
.venv311/bin/python run_unit_tests.py
```

该命令只运行基于 `unittest.TestCase` 的测试，排除会启动真实游戏或请求
模型的独立探针脚本。技能隔离测试需要可用的 Bubblewrap；游戏速度测试
需要本机地图。`verify_*`、`probe_*`、`smoke_*` 和部分独立 `test_*` 脚本
用于实际引擎/模型验证，运行前请阅读源码及参数。

## 进一步阅读

- [CLOUD_HANDOFF.md](CLOUD_HANDOFF.md)：自主困难 bot 对战的最新代码、提示词、复盘和账本续跑说明。
- [AI_SKILL_MANUAL.md](AI_SKILL_MANUAL.md)：模型技能契约。
- [SC2_GAME_KNOWLEDGE.md](SC2_GAME_KNOWLEDGE.md)：游戏知识和种族能力。
- [SCENARIOS.md](SCENARIOS.md)：战场场景覆盖及现有缺口。
- [BATTLE_AGENT.md](BATTLE_AGENT.md)、[TACTICAL_CONTROL.md](TACTICAL_CONTROL.md)、
  [SPATIAL_CONTROL.md](SPATIAL_CONTROL.md)：战斗、战术和空间控制。
- [THIRD_PARTY.md](THIRD_PARTY.md)：第三方源码、参考数据和许可证。

`STATUS.md` 和 `HANDOFF.md` 保留开发记录，其中旧路径和验证日志位置为历史记录。
