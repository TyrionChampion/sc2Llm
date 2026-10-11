# 科技技能完善与并行开发记录（2026-10-09）

本轮用户要求：不影响另一对话添加功能，完善狗速、毒爆虫速度及其他必要技能。

## 实际核验

狗速 metabolic_boost、毒爆虫速度 centrifical_hooks 原先已有 research 接口，不是新增未存在的技能。上一局 official-runtime/match-krctza57 的P2原始反馈，在game_loop=11921（532.19游戏秒，约8:52）记录 research_confirmed/metabolic_boost。毒爆速度没有该局完成证据。原始提取 ../_probe/tech_skills_20261008/p2_research_before.json 保留日志事件，完整源为 logs/feedback/p2-vfvb0rzc.jsonl。

先判别再修复：三个科技ID碰撞气矿单位ID：cloaking_field=20、zerg_missile_weapons_level3=61、anabolic_synthesis=88。旧MacroRun在research里也按product数字走气矿分支，导致0动作、no unoccupied geyser near owned base。修正为仅production走气矿分支。同一合成观察修后均发出1条正确科研命令（790/1194/263）。狗速/毒爆速度也验证发令1253/1482。不是原生效果验收。

首个探针猜测blink受影响，实测blink正常发出1条命令，已保留before_blink.json并纠正跨对话消息；最终修复结论只依据实际复现的三个科技，不把最初假设当事实。

另一个旧问题：科研订单曾出现、后消失且升级一直不在观测里，任务永远显示running/awaiting observed upgrade completion。现保留8游戏秒观察宽限，升级仍缺失则明确失败（取消、打断或确认缺失，原因未知），不虚称完成；宽限内收到升级正常完成。

## 已落地

- arena/technology.py：按本族当前实际单位和科技目录，提供条件式升级用途/阵容建议；报告最多12个候选，区分已完成、正在研究、缺前置/空闲科研建筑、缺矿气、观测前置已满足。替代形态按本场alias匹配，显示实际单位名称且每个实体只计一次。
- arena/knowledge.py：主模型实际收到technology_playbook和technology_report。
- arena/context.py：动态technology_report移到变化后缀；固定说明仍在前缀。只读观察，不额外能力查询/API，不自动花钱、不代替模型选科技。
- arena/macro.py：上述科研ID冲突和订单消失后的假正常等待修复。
- test_technology.py：14项针对性检查；连同三族/上下文/运营既有回归共80项通过，1.551秒；一项内部遍历当前目录89种科研，确认可用能力fixture下都生成科研动作而非建造目标。全部合成观察/当前缓存原生元数据，未进行新的原生实战。

升级说明覆盖虫族狗速、毒爆速度、蟑螂速度/埋地、刺蛇射程/速度、潜伏者、王虫速度、雷兽、地面/空军攻防；人族兴奋剂、盾牌、震撼弹、机械/女妖等；神族冲锋、闪烁、风暴、巨像射程、折跃门等。主动技能部分明确研究与施放不同：女王注卵/菌毯/治疗、运输装卸、EMP/风暴等需已登记Python技能/可用能力接口，缺少时可请求现场开发，不假称本轮新增了所有主动施法执行器。

## 调用例子

```json
{"tasks":[{"skill":"research","args":{"upgrade":"metabolic_boost"}}],"reason":"跳虫阵容升级狗速"}
```
```json
{"tasks":[{"skill":"research","args":{"upgrade":"centrifical_hooks"}}],"reason":"满足虫穴和毒爆巢前置后升级毒爆速度"}
```
前置缺失、气不足时应明确安排本族气矿/gather_gas/建筑任务；先查看active_tasks避免重复。研究由实时引擎能力查询最终裁决，只有world.upgrades出现升级才算完成。报告的observed_prerequisites_ready不能当作引擎接受或预算可用的保证。

## 不影响另一对话

已与“检查虫族对局是否结束”发送文件范围协调。未改其控兵/集结/战术agent范围：commander.py、tasks.py、battle_plans.py、official_duel.py、native_llm.py、deepseek_planner.py、rally.py、combat_micro.py、defense.py。本轮也没改tech_tree.py。共享HANDOFF/STATUS/大手册未并发覆写，独立本文供合并引用。没有开/关比赛，没有模型调用，没有修改用户USB、引擎或pysc2。

说明纠正：另一对话的用户已说明上一局是其手动为虫族点投降。此前HANDOFF里“自行结束”只由结果/空shutdown_signals推断，表述不精确；正确是用户手动投降后引擎结算P1Victory/P2Defeat并正常清理，并非本对话主动结束，也不能宣称自然击毁获胜。原始match.surrender=null仅说明没有走我们专门的surrender请求记录。

## 证据

项目外 ../_probe/tech_skills_20261008/：
before_catalog.json、before_collisions.json、after_commands.json、before_cancelled_research.json、after_cancelled_research.json、early_prompt_fixture.json、later_prompt_fixture.json、checks_final.log。Prompt样本明确标注synthetic，不是付费模型实际请求。汇总 logs/technology_skills_summary.json。
