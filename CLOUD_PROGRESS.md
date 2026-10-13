# 本次云端续跑（2026-10-12 至 13）

目标已达成：第37局取得新的困难 Terran 引擎 Victory，战役自动停止。
模型仍为 OpenCode Go DeepSeek V4.1 Flash，未切换 Pro；双方 Slower、realtime。
`result_source=engine_player_result`，`termination=engine_end`，引擎结果为
玩家1 Victory、玩家2 Defeat；531.6游戏秒、1268.29墙钟秒，20次战斗请求全部成功，
引擎动作错误0，speed_check=ok，cleanup_complete=true。回放20207字节已保存。
Go已完成胜局复盘（category=win）。这是新实战胜局，不是复用历史第12局。

权威账本 `logs/autonomous-bot/20261012-093031-xhnbux9u/campaign.json`：
`state=finished`、`stop_reason=goal_reached`、`goal_reached=true`；累计840/962次，
battle785、review45、connectivity10。前一轮账本仍278/312；失败、探测、复盘重试、
独立试跑和旧编号全部保留，没有重置计数。第35/36局分别在时间上限结束，复盘后
累计794、819；第37局20次战斗加1次复盘后840。不再需要启动下一局达成本次目标。

胜局证据（同一原账本目录下）：
- `match-37-hard/match.json`：原始引擎结果、时间、用量、源代码SHA与清理状态。
- `match-37-hard/match.SC2Replay`：实际回放。
- `match-37-hard/victory-validation.json`：零额外API调用的独立核验，20份回复均返回
  deepseek-v4.1-flash 且使用 Go 接口；当前源码与实战SHA逐项相同。
- `match-37-hard/review.json`：Go胜局复盘与下一步优化建议。

回放按75689协议解码：玩家结果1/2，双方Terran、handicap100、正常迷雾，bot难度
数值5与API Hard=5一致，地图AcropolisSlower，11907 loops。回放bot显示名为
“A.I. 1 (Harder)”，保留原值而不改写；API创建参数仍为Hard。回放大厅的初始速度/
realtime字段不能代表运行时地图触发器设置，真实SC2Env realtime=True及实测
9.3896 loops/s（Slower目标9.6）一起验证运行速度。核验记录保留这些原始字段。

本局由模型自主选择单矿路线，实际建成3兵营、2工厂、双气，研究兴奋剂与战斗盾；
约351游戏秒显式命令19单位统一压敌主矿并持续增援，随后清剿建筑获胜。执行器仅
兑现模型指令与已授权微操，没有固定战略脚本或debug。单局胜利尚不能证明稳定胜率。

第31局到墙钟上限结束；Go完成复盘，原战术1918字符仅整理空白后1779，
非空白字符及英文词间分隔全部保留，未截断或改选路线。第32局原生json_schema格式
连续三次HTTP400，结果Unknown/model_unavailable，回放和错误均保留。第33局起恢复
已验证的json_object模式；第33–36局均以时间上限结束，均不算引擎胜利。

原生格式在两个小兼容测试曾成功，公开目录也标注structured_output；但真实比赛请求
返回invalid_request_error/format，因此默认关闭，不声称已稳定支持。两次格式探测、一
次健康检查以及失败的复盘/格式请求全部记账。新接口适配只机械搬移已显式选定的人数
到args.count、统一已观测单位名拼写和移除空args_extra，原模型回复及representation.json
同时留存；不猜缺失参数、不更改兵种/数量/目的地。原拒绝行为已复现，修复后通过。

战斗输入45,000字符上限保持；服务端输出语法元数据另行记录。第30/31局48份真实请求
已本地检查，world/当前反馈/活动任务/接触和告警逐项还原相等，最大输入43916字符，
没有额外模型调用。复盘仅在游戏清理后使用24000输出token以容纳思考与公开结果；
战斗仍8000输出token、60秒请求超时，每局仍24请求、1800游戏秒、1860墙钟秒。

已拉取并快进合并 `origin/codex/autonomous-go-handoff`（`046ab98`），阅读
`CLOUD_HANDOFF.md`。合并前的 Go 接入、评估入口、安装修复和新比赛日志完整保留。

交接归档校验并恢复了146个文件，没有覆盖新比赛。旧账本分别保持278次和451次请求。
随后把本会话独立试跑的24次请求（主指挥20、战斗4）及6次连通检查登记为
`additional_experiments`，本轮增至481/572，前一轮仍278；两个编号比赛历史均未重置。
独立试跑报告 `logs/go_hard_seed7.json` 为 error，未获引擎胜负结果；保留其原始反馈、
源文件摘要及进度。其防守动作上下文错误由交接分支已有回归修复覆盖，不算新胜局。

交接客户端实测 HTTP400 `MissingSessionID`，补充稳定的 `x-opencode-session` 后
`bin/cloud-go-status.py` 返回 available=true，实际返回 DeepSeek V4.1 Flash。
行为复现针对原交接代码失败、修复后通过。保持固定Go接口、代理、TLS和密钥引用；
不记录密钥、不切换供应商。Go用量同时保留原始OpenAI字段并映射账本需要的token字段。

续跑命令：

```bash
./run_sc2.sh .venv/bin/python autonomous_bot.py \
  --resume logs/autonomous-bot/20261012-093031-xhnbux9u \
  --until-hard-win --switch-model opencode-go/deepseek-v4.1-flash
```

第23–29局延续编号，主指挥和复盘均使用Go，low思考。第23、24、26、27局为
引擎Defeat，第25局为明显败势投降；第28、29局到墙钟上限投降，均不算胜利。
仅新的困难Terran引擎Victory算目标达成，历史参考胜局不能代替目标。

用户选择Slower后，第28局开始使用项目已有、SHA校验的真实速度地图。
第28/29局实测速率分别9.3860/9.3861 loops/s，目标9.6，两局speed_check=ok，
双方同速且realtime。1860墙钟秒实际约780游戏秒；每局24次战斗请求、1800游戏秒和
1860墙钟秒上限保持不变，无固定脚本开局或debug。速度变更登记前已用610次请求。

第29局战斗结束累计659次，复盘服务端length失败计第660次，重试成功后661。
第30局到墙钟上限结束，复盘后686。随后所有格式探测、失败及复盘均继续累加；
最终计数与结果以上方840及campaign.json为准。所有失败和新回放完整留存。

本会话修复包括：Go会话头、公开usage字段映射；子进程当前解释器；模型准备战术
从通用1200字符裁剪恢复为完整1800字符（仍受45000字符总输入限制）；复盘证据无损
对象池/字段名表（60000上限不变，逐项还原相等）；Slower墙钟剩余时间及预计游戏截止
时间进入模型上下文和自适应检查间隔。模型仍亲自决定生产、侦察、出击及部署。
第29局截止时间复现：原检查间隔250秒、修复后82.85秒，未增加战略指令或调用上限。
每局报告增加种子、速度/时间及运行源文件SHA-256；运行中修改仅归因于后续新工作进程。
第29局反馈764的大编队撤退误报也已修复：38单位中心距目标约1.52，所有单位在
本地接近范围内，37单位idle，却因半径4要求报停滞。仅显式move允许中心半径4内、
外围idle且距目标不超过6+自身半径的紧凑编队算到达；攻击/侦察、远处或仍移动的
外围成员继续原判定。真实位置回归原代码失败、修复后通过，相关83项检查通过。

验证：最终相关199项检查通过（`logs/final-go-regressions.log`），覆盖Go接口、
模型身份、表示转译、复盘、预算续跑/恢复、上下文、速度、队形、防守和投降。
此前较广的相关292项通过（`logs/current-focused-regressions.log`）；模型选项53项、
最后防守指令提示38项通过。Slower地图已生成并运行真实引擎速度探测。
旧全套仍有Bubblewrap命名空间、旧测试固定`.venv311`路径等环境条件问题，
不能称为全套通过。没有为最终核验追加模型请求。

完整续跑日志：`logs/autonomous-bot/go-continuation.log`；权威计数、编号和结果以
原目录`campaign.json`及各`match.json`为准。以下为保留的续跑入口；该账本已完成目标：

```bash
./run_sc2.sh .venv/bin/python autonomous_bot.py \
  --resume logs/autonomous-bot/20261012-093031-xhnbux9u --until-hard-win
```

未完成复盘需先完成原局复盘；Go判定feature_gap时先检查真实证据、复现并验证修复。
第31局起用完整scout JSON示例，清楚标示args.count；准备战术保留全文并标为
pre_match_plan/execution_evidence=false，本局编号进入上下文，旧局举例不算本局执行。
每次回复可排最多6个必要任务（执行器原本支持16），便于生产/补给/科技/侦察并排；
每局仍24次API请求，模型亲自选择所有任务。新增提示相关62项检查通过，总输入上限
45000不变。第30局四次漏侦察人数时，实际拒绝反馈已及时进入后续请求，反馈链路没有
漏发这些错误；当局原提示固定于进程启动时，新提示只归因于第31局及之后。
