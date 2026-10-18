# 交接：AI 对战的进攻执行力与非实时时间膨胀

- 日期：2026-10-18
- 基线：`1f9e325 Fix surplus worker transfers and explicit all-army attacks` + 当前工作区未提交改动（见文末「交接状态」）
- 适用范围：`native_llm.py --no-realtime --tactics`，双 LLM 原生对战
- 本文只做诊断与改造方案，**不包含已实施的代码改动**
- 后续 P0 实施与验证见 [2026-10-18-ai-duel-p0-validation.md](2026-10-18-ai-duel-p0-validation.md)；下文代码描述保留为诊断时基线。

## 1. 背景与目标

目标是让不同厂商的模型在 SC2 原生环境里互相对战，用作模型能力评测。参战双方都是真实 LLM 玩家，没有内置 bot。

被诊断的对局：

| 项目 | 值 |
| --- | --- |
| 目录 | `logs/native-go-duel-20261017/` |
| P1 | OpenCode Go `deepseek-v4.1-flash`，人族 |
| P2 | OpenCode Go `glm-5.3-flash`，人族 |
| 地图 / 种子 | Acropolis / 7 |
| 模式 | 非实时，`--step-loops 8`，headless，正常战争迷雾 |
| 结果 | 用户在游戏 12:49（769.3s）手动停止，无胜负 |
| 现实耗时 | 约 2h15m（8090s） |

这场暴露出两个独立但相互放大的问题：

1. **进攻执行力**：P1 在 8:16 下达全军进攻后，到 12:49 都没打下 P2 主矿，没有形成第二次有效的大规模进攻。
2. **非实时时间膨胀**：战斗期的现实/游戏时间比从 1.5–2 飙到 22–26，导致一局 12 分钟游戏时间要打 2 个多小时。

## 2. 结论摘要

### 2.1 进攻打不动的根因：`move_out scope=all` 把战术 LLM 关掉了

主指挥下达 `move_out scope=all` 后，`BattleAgent` 进入 `all_army_attack` 模式：

- `arena/battle_agent.py` 的 `_request()` 第一行直接 `return`，**战术 LLM 不再被调用**；
- `_receive()` 里 all scope 的回复也会被当作过期丢弃；
- `_assign_all_army()` 把所有可控单位 attack-move 到固定的 `enemy_start` 坐标，没有阶段目标、没有集结、没有路线选择；
- `_report_all_army()` 只统计 `stalled`，不触发任何动作。

与此同时，主指挥在 prompt 里看到 `mode=all_army_attack`，以为“战术 AI 在持续交战”，于是反复输出“由战术AI持续，不重复下令”。这是**静默回退**：系统看起来有战术 AI，实际上 8:16 之后只剩机械平推。

### 2.2 非实时变慢的根因：新情报把主指挥冷却重置为 `-inf`

`arena/intelligence.py` 的 `Intelligence.update()` 会在以下情况增加 revision：

- 新敌人 tag 或敌人类型变化 → `enemy_sighted`
- 敌人死亡 → `enemy_death_observed`
- 己方单位死亡 → `own_unit_death_observed`

`arena/commander.py` 的 `ingest_feedback()`：

```python
if self.intelligence.update(w) and self.defense is None:
    self.last_request = float("-inf")  # Next decision gets new intel
```

主指挥的节流条件是 `w.seconds - self.last_request >= self.interval`，而 `interval` 在有战术 AI 时是 20 游戏秒。只要 `Intelligence.update()` 返回 True，`last_request` 就被设成 `-inf`，下一 tick 立即重新请求，**20 秒冷却被完全绕过**。

战斗期双方不断有单位进入视野、死亡、增援出现，几乎每个 step 都触发一次，于是主指挥从“每 16.5 游戏秒一次”变成“每 1 游戏秒一次”；每次请求要等 10–20 秒现实，而游戏只推进 0.357 秒（`step_loops=8`）。

### 2.3 第二重放大

- `plan_rejected` / `plan_expired` 也会执行 `self.last_request = float("-inf")`。本场 P1 的 42 次拒绝有 38 次在 8–12 分钟，P2 的 49 次有 42 次在 8–12 分钟。每次拒绝立即重试，形成“请求 → 参数错误 → 立即再请求”的循环。
- `native_llm.py` 用 `advance=not planners_thinking(commanders)` 推进引擎，**任意一个 planner 在思考就暂停整局**，主指挥、战术 AI、防守 AI 串行等待。

## 3. 证据

### 3.1 代码位置（当前工作区）

| 问题 | 文件 / 函数 | 当前行为 |
| --- | --- | --- |
| all scope 关闭战术 LLM | `arena/battle_agent.py:258` `_request` | `if self.strategy.get('scope')=='all': return` |
| all scope 丢弃回复 | `arena/battle_agent.py:311` `_receive` | 回复直接判 `tactical_plan_expired` |
| all scope 机械执行 | `arena/battle_agent.py:453` `_assign_all_army` | 所有单位去固定 `enemy_start` |
| stalled 只上报 | `arena/battle_agent.py:474` `_report_all_army` | 无触发器、无重组 |
| 情报唤醒 | `arena/commander.py:213-220` `ingest_feedback` | `last_request=-inf` |
| 拒绝/过期唤醒 | `arena/commander.py:330,338` | `last_request=-inf` |
| 主决策节流 | `arena/commander.py:440` | `w.seconds - last_request >= interval` |
| 情报触发条件 | `arena/intelligence.py:53-97` `update` | 新敌人/敌人死亡/己方死亡都会返回 True |
| 全局暂停 | `native_llm.py` `planners_thinking` + `arena.step(..., advance=...)` | 任一 thinking 即暂停 |
| 自动架坦克说明 | `arena/battle_plans.py:6` `COMBAT_MICRO_PROTOCOL` | 宣称 executor 自动处理 stim/siege |
| 架坦克实际条件 | `arena/combat_micro.py:42` `apply()` | 只在开火或到达目标后架；13 格对峙时不架 |

注意：当前工作区已经给 `BattleAgent._request` 加了 6/12 游戏秒的非实时节流（`last_request_game_s`），但 **all scope 的 `return` 还在**，所以对这场的问题没有作用。

### 3.2 时间膨胀数据

按游戏时间分段，计算现实耗时 / 游戏秒：

| 游戏时间 | 现实耗时 / 游戏秒 |
| --- | --- |
| 0–360s | 1.5–2.0 |
| 360–420s | 4.85 |
| 420–480s | 6.11 |
| 480–540s | 22.54 |
| 540–600s | 21.97 |
| 600–660s | 25.74 |
| 660–720s | 23.29 |

相邻样本中 `wall/game > 15` 的有 186 个（共 807 个），中位数 **28.1**；这些样本每次只推进 **0.357 游戏秒**，正好是一个 `step_loops=8`。

P1 主指挥累计调用数（`match.json` 的 `llm_calls`）：

| 游戏时间 | 累计调用 |
| --- | --- |
| 463s | 28 |
| 520s | 69 |
| 601s | 155 |
| 761s | 304 |

463s 之后 298 游戏秒内调用 276 次，平均 **每 1.08 游戏秒一次**，与 20 秒 `interval` 完全不符。

### 3.3 进攻数据

P1 的 `all_army.counts` 时间线：

- 496s：28 个单位全部 dispatched/departed；
- 510–520s：撞上 P2 防线，总单位从 31 掉到 17，`engaged` 从 2 升到 12；
- 520s 之后：增援持续加入，总单位回到 31–37，但 `stalled` 长期 3–5 个，`engaged` 只有 1–4 个；
- 764s：`departed 27, stalled 4`，没有 `at_target`。

最后时刻 P1 有 9 辆 `SiegeTank` 未架、1 辆架起；P2 有 3 辆 `SiegeTankSieged` 守坡口。P1 的坦克在 13 格外排队，只有前排少量单位在交战。

P1 的 19 次战术 LLM 请求在首攻前后（510s 前）已全部用完，`army_tactics.requests` 从 510s 起一直停在 19。**510s 之后到 12:49 的 4 分多钟内，战术 AI 一次都没有被调用。**

## 4. 改造方案

按优先级排列。P0 两项建议先做最小实验，再动 P1。

### P0-A：让 all_army 不再关闭战术 LLM

目标：全军进攻授权只表示“允许打、允许持续投入增援”，不表示“取消战术大脑”。

建议改法：

1. `_request()` 去掉 `scope=='all'` 的直接 return，改成 all 模式下**周期性 review**：
   - 正常每 10–15 游戏秒一次；
   - 出现 `stalled`、单位大量死亡、到达集结点、发现新敌方基地时提前。
2. `_receive()` 去掉 all scope 的直接过期分支；回复仍然要过 generation/age 校验。
3. all 模式下限制模型权限：只能调整目标、路线、集结点、撤退重组、空投；不能取消全军授权。
4. 在 `public()` / 主指挥 payload 里明确 `mode=all_army_attack` 是“机械执行 + 周期 review”，避免主指挥误以为战术 AI 全权接管。
5. 如果决定保留“机械执行、不让 LLM 介入”的设计，必须反过来修主指挥的认知：在 prompt 里写明 all scope 下战术 AI 不思考，`stalled` 需要主指挥自己处理，并且不要在 reason 里写“由战术AI持续”。

验收：

- 8:16 之后 `army_tactics.requests` 继续增长，而不是停在 19；
- `all_army.counts` 出现 `at_target`，或 `engaged` 峰值 ≥ 10；
- P2 主矿在 2–3 分钟游戏时间内掉血/被拆。

风险：

- 模型可能在“打/撤/换路”之间反复横跳。需要用 `next_review` 节流，并要求每次 review 至少维持 8–10 游戏秒。
- all scope 下模型不能取消授权，避免一看到坦克阵就全军撤退。

### P0-B：情报唤醒加下限，不要重置为 `-inf`

最小改动（`arena/commander.py` `ingest_feedback`）：

```python
if self.intelligence.update(w) and self.defense is None:
    # 有新情报时最多把下一次决策提前到 2 游戏秒后，不允许每 step 一次
    self.last_request = max(self.last_request, w.seconds - self.interval + 2)
```

`plan_rejected` / `plan_expired` 同理：

```python
self.last_request = max(self.last_request, w.seconds - self.interval + 3)
```

进一步（建议同一批做）：

- **情报分级**：只有高价值情报允许提前唤醒——敌方主力集群、新基地、己方基地受袭、科技建筑、关键单位；单个农民/侦察兵、远处单位死亡只记录，等下一次定时决策。
- **决策预算**：每 N 游戏秒最多一次主决策，硬上限；情报只影响“下一次决策看到什么”，不改变时机。
- **战斗期反向调度**：主指挥低频（20–30 游戏秒），战术 AI 高频（5–10 游戏秒）。现在是反的。

验收：

- 480s 之后 wall/game 从 22–26 降到 < 5；
- 主指挥调用从“每 1 游戏秒一次”回到“每 10–20 游戏秒一次”；
- 防守反应没有明显变慢（用 base alert → 防守令的时间衡量）。

风险：

- 节流过度会错过防守窗口。保留“己方基地受袭/主力被灭”的快速通道。
- 情报分级需要真实对局验证，不要用假设的 fixture 直接定阈值。

### P1-A：all_army 目标动态化 + 分阶段

当前 `_assign_all_army()` 写死 `enemy_start`。建议：

1. 目标优先级：**当前可见敌方基地 > 最后已知敌方基地 > `enemy_start`**。
2. 加阶段状态：
   - `assemble`：先到 `forward_assembly`，等 70% 单位到达或兵力过阈值；
   - `clear_approach`：清坡口/坦克阵；
   - `kill_main`：打主矿；
   - `regroup`：交换比不利时后撤重组。
3. 不要把 30+ 单位塞到同一个点。按类型分层：坦克后排、步兵前排、医疗机跟随；或分 2–3 个攻击点。
4. 分兵清分矿。本场 P2 丢主矿压力后仍有 40 农民在分矿采矿，因为 P1 只盯 `enemy_start`。

### P1-B：`stalled` 触发器

`_report_all_army()` 已经有 `stalled` 统计，但只上报。建议：

- `stalled >= 30%` 持续 8 游戏秒 → 触发一次战术 review；
- 或自动后撤到集结点，等增援再上；
- 记录“第几次进攻尝试、每次交换比”，避免无限送兵。

### P1-C：坦克架起推进

`combat_micro` 现在只在“正在开火”或“到达目标且无敌人”时架坦克。P1 的坦克卡在 13 格外，不满足任一条件，所以 9 辆一直不架；P2 的 3 辆在坡口架着打。

建议二选一：

- 在 `combat_micro` 里加“接近敌方防线 13–15 格先架一轮，打完再收架前进”的 leapfrog 逻辑；
- 或者让战术 LLM 能显式下 `siege` / `unsiege`，而不是只靠 executor 启发式。

### P1-D：兵种克制提示

P1 有星港、有 6 架医疗机，但整局没出维京、解放者、女妖、雷神，也没有空投。打“架起坦克阵 + 坡口”这种局面，标准解法是空中/远程/空投。建议在主指挥 prompt 里加一条：

> 敌方架起坦克阵且地面推不动时，优先考虑维京/解放者/女妖，或用医疗机空投主矿；不要用纯地面单位反复撞坡口。

### P1-E：提高单次决策的时间收益

- 把 `--step-loops` 从 8 提到 16 或 32。同样的决策次数下游戏时间推进 2–4 倍，wall/game 直接降 2–4 倍。需要观察决策质量。
- 主指挥和战斗 AI 并行请求；引擎等两边都完成再推进，而不是任一 thinking 就停。
- 战斗模型 40s 超时，本场 7:49–7:56 连续 4 次 `ReadTimeout` 正好卡在首攻窗口。加一次重试或降级到主指挥，别让超时吞掉进攻窗口。

### P2：参数与评测

- `plan_rejected` 降噪：P1 42 次（9 次生产队列超 200、8 次 `macro_plan` 参数越界），P2 49 次（把坦克写成 `siege_tank`、4 次 `response needs tasks`）。在 prompt 里把 count 上限、合法 skill 名写得更硬，或解析时 clamp + 留日志。
- 评测改为“固定游戏时间 + 优势判定”，不要打到死。
- 同一对模型打 3–5 局，固定地图/种子/提示词。
- 记录可对比指标：首次进攻时间、首次有效交战时间、单位交换比、`stalled` 次数、`all_army` 到达率、战斗期 wall/game。

## 5. 建议的最小实验顺序

1. **只改 P0-B（情报唤醒节流）**，其他不动。跑同一对模型 5 分钟游戏时间，测：
   - 480s 后 wall/game；
   - 主指挥调用频率；
   - 防守反应时间。
2. **再改 P0-A（all scope 周期 review）**，跑 15 分钟，测：
   - `army_tactics.requests` 是否继续增长；
   - `engaged` / `at_target` 峰值；
   - P2 主矿是否掉血。
3. **最后上 P1-A/B/C**，用同一回放做对照，确认不是“模型偶尔打得好”。
4. `step_loops` / 并行属于性能优化，放在功能验证之后。

## 6. 验收标准

| 指标 | 基线（本场） | 目标 | 测法 |
| --- | --- | --- | --- |
| 战斗期 wall/game | 22–26 | < 5 | `match.json` samples 的 `wall_s`/`game_s` |
| 主决策频率 | 每 1.08 游戏秒 1 次 | ≤ 每 10 游戏秒 1 次 | `llm_calls` 对 `game_s` |
| `plan_rejected` 占比 | 13.5% / 15.8% | < 5% | feedback `plan_rejected` / 请求数 |
| 首次进攻后战术请求 | 停在 19 | 持续增长 | `army_tactics.requests` |
| 进攻到达 | 无 `at_target` | 出现 `at_target` 或主矿掉血 | `all_army.counts` / 回放 |
| 单局现实时长 | 2h15m / 12:49 游戏 | 同等游戏时间 < 45m | 启动/停止时间 |

## 7. 不要做的事 / 陷阱

- **不要保留静默回退**：all scope 关闭战术 AI 必须在状态、日志、主指挥 prompt 里可见；否则主指挥会一直误判。
- **不要用假设的 fixture 验证节流**：用真实对局样本或真实请求回放；情报分级阈值必须来自真实数据。
- **不要把节流做成永远不响应**：保留“基地受袭 / 主力被灭 / 新敌方基地”的快速通道。
- **不要只调 prompt 不改调度**：模型再聪明，也救不了每 0.357 游戏秒一次的重规划。
- **不要用单局胜负下结论**：这是评测基础设施，至少 3 局 + 固定条件。
- **不要改 `_assign_all_army` 的目标却不改 `_report_all_army`**：报告的 `goal`、`requested_point`、`at_target` 判定要跟着改，否则又是“看起来在打，其实没到”。

## 8. 相关文件与数据

代码：

- `arena/battle_agent.py` — 战术 LLM、all_army 执行、stalled 上报
- `arena/combat_micro.py` — stim/siege 自动执行
- `arena/commander.py` — 主指挥调度、情报唤醒、plan 校验
- `arena/intelligence.py` — 视野/死亡情报
- `arena/battle_plans.py` — 主指挥 system prompt、COMBAT_MICRO_PROTOCOL
- `native_llm.py` — 非实时主循环、`planners_thinking`、`step_loops`
- `arena/native_engine.py` — `step(..., advance=...)`、观察数据

对局数据：

- `logs/native-go-duel-20261017/README.md` — 启动配置与结论
- `logs/native-go-duel-20261017/match.json` — 808 samples、611 llm_replies、commanders
- `logs/native-go-duel-20261017/match.SC2Replay` — 停止时回放（非完整胜负局）
- `logs/native-go-duel-20261017/feedback/p1-roix0vvp.jsonl` — P1 feedback
- `logs/native-go-duel-20261017/feedback/p2-dt7f30p1.jsonl` — P2 feedback
- `logs/native-go-duel-20261017/p1/main/request-*/`、`p2/main/request-*/` — 主指挥请求/回复
- `logs/native-go-duel-20261017/p1/battle/request-*/`、`p2/battle/request-*/` — 战术请求/回复

参考：

- `logs/native-step-go-20261017/REPORT.md` — 上一场 DeepSeek vs bot 的同类问题记录（进攻保守、参数错误、决策频繁）
- `logs/native-step-go-20261017/analyze_match.py` — 汇总脚本，可复用

## 9. 交接状态

当前工作区未提交改动（`git status`）：

```
M README.md
M arena/autonomous_bot.py
M arena/battle_agent.py
M arena/battle_plans.py
M arena/go_planner.py
M arena/native_engine.py
M arena/native_runtime.py
M llm_output_window.py
M native_llm.py
M test_go_planner.py
?? test_native_step_mode.py
```

其中 `arena/battle_agent.py` 已包含非实时节流（`last_request_game_s`、`same_frame` 处理），但本文 P0-A 的 `scope=='all'` return 仍在，**不要把它当成已修复**。

建议接手顺序：

1. 先跑一次本文第 5 节的基线实验，确认当前工作区仍复现 wall/game 22–26；
2. 按 P0-B → P0-A → P1 的顺序改；
3. 每步保留请求日志和 `match.json`，用第 6 节的指标验收；
4. 不要把 P0 和 P1 混在一个 commit 里，否则无法归因。
