---
id: f1-metric
title: 加 F1 指标并重算旧结果
kind: 执行
status: 进行中
about: a:2026-11-15-focal
scope: [src/eval/]
deliver: [runs 里的 f1 列]
budget: 1 卡时
sessions: [demo·f1-metric]
created: 2026-11-20
updated: 2026-11-21T02:00:00Z
---
## 要做什么
评测脚本加少数类 F1，旧运行重算一遍。

## 完成标准
r001–r003 的 run.json 都有 f1。

## 交接
- 做了什么：评测脚本已加 F1。
- 下一步：重算 r002、r003。
