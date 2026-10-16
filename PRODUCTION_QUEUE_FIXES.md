# 资源积压后的执行接口修复

这次修改发生在虫族四局结束后，不改变原比赛结果，也不证明模型下一局必然正确暴兵。
模型仍自行选择兵种、数量、人口、产能、注卵及出击；每局24次请求、历史账本和既有总预算保持不变。

## 行为

- train_worker/train_unit新增可选args.queue_mode=append，count明确表示新增1..200只。
  已有活动批次则增加目标数量，保留在途订单、出生记录、资源校验及原任务期限；否则创建有限批次。
  默认coalesce仍不追加，现在反馈原task_id、requested_count、added_quantity=0及实际生产数量。
- active_tasks.production_quantity列出requested/issued/observed_completed/unissued/awaiting_completion/remaining。
  没有根据预计时间假定出生；跳虫成对生产仍可能超过奇数目标。
- 同产品每计划只允许一次append，活动批次总量≤200；同来源/请求/产品重复处理不重复购买，
  保留最近128个追加收据。建筑、形态、弹药不支持append；非法计划仍整批拒绝，不部分执行。
- 修复整批拒绝前就标记旧防守令superseded的状态污染；失败计划不改变原防守任务。
- 修复旧Terran适配器已发足数量但产品尚未出生时继续发单的问题，追加后才有新额度。
- 普通空闲产能检查按实际游戏/墙钟期限及剩余请求分配25～60秒间隔，
  前180秒和紧急交战仍为25秒。纯空闲产能的790游戏秒模拟，最后一次请求从提前耗尽推迟至约753秒，
  留约37秒执行；这只是调度单测，不是实战成效。硬上限不绕过，网络失败仍计数。
- 注卵知识和Go提示明确使用inject_larva，不再误称必须Python；单次命令不自动循环。
  提示文字压缩后原45,000字符输入边界回归通过，没有删当前事实、放宽预算或改模型。

示例：

```json
{"tasks":[{"skill":"train_unit","count":8,"args":{"unit":"roach","queue_mode":"append"}}],"reason":"显式补充8只蟑螂"}
```

## 验证

`test_production_queue.py`含14项针对性检查，包括三族追加、在途保留、准确结束、重复请求、
数量界限、非法混合计划、输出schema、知识进入实际输入和请求节奏。

原生验收：

```sh
./run_sc2.sh .venv/bin/python verify_larva_confirmation_live.py --label <新标签> --append-count 1
```

`logs/larva-confirmation-production-append-native/result.json`及回放保存结果：
首只工蜂发单后追加1只，重复处理同一追加请求；最终实际新增2只、同一任务、在途订单保留、动作错误0。
没有debug、fast-build或模型调用。首轮沙箱尝试因本机端口绑定不可用失败，记录另保留于
`logs/larva-confirmation-production-append/result.json`，随后允许本机端口的原生测试通过。

相关30模块合计310项检查：305通过，5项既有现场Python开发检查因bwrap不能创建
NETLINK_ROUTE socket（Operation not permitted）受当前环境阻塞，外层放行后仍同样失败。
另补充旧Terran适配器不超买的检查，最新14项针对性检查全部通过，记录在
`logs/production-queue-targeted.log`；与上述检查合计306项通过、5项环境阻塞。
没有关闭程序的技能隔离以绕过检查。原始失败和放行后的结果分别保存于
`logs/production-queue-regressions-sandbox.log`和`logs/production-queue-regressions.log`。
本轮原生验收及回归不增加真实DeepSeek请求；虫族四局账本仍195/200。
