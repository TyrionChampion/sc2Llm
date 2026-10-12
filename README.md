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
