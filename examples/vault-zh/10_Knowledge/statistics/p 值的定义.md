---
id: kc_p-value-definition
title: p 值的定义
kind: concept
domain: statistics
pillar: 推断
topic: 假设检验
tags:
- review
- flashcards/statistics
sources:
- path: 40_Sources/20ee79291f293d22/置信区间与 p 值.md
  sha256: 20ee79291f293d22ac3e8e457666724f8d6acfbbadf6df24aada33e32cd7d446
  locator: p 值（p-value）是指：假定零假设（null hypothesis，通常是“没有效应”）成立时，得到至少与观测结果一样极端的结果的概率。
relations:
- type: required_by
  target: kc_p-value-misreadings
  note: 要纠正误读，先要知道 p 值的正确定义。
- type: contrasts_with
  target: kc_p-value-misreadings
  tier: extension
- type: required_by
  target: kc_multiple-comparisons-false-discoveries
- type: required_by
  target: kc_statistical-vs-practical-significance
status: confirmed
created: 2026-10-13
---
## 本质
p 值是假定零假设成立时，得到至少与观测结果一样极端的结果的概率。

## 命题
- P1. p 值是指：假定零假设成立时，得到至少与观测结果一样极端的结果的概率。
- P2. 零假设（null hypothesis）通常是“没有效应”。
- P3. p 值小，说明如果真的没有效应，这样的数据会让人意外。

## 直觉与易混对比
- 直觉：p 值回答的是“如果没有效应，数据像这样极端有多罕见”，而不是“有没有效应”。

## 相关
- required_by:: [[p 值的常见误读与 0.05 阈值]]（要纠正误读，先要知道 p 值的正确定义。）
- required_by:: [[多重比较导致虚假发现]]
- required_by:: [[统计显著性不等于实际显著性]]

<details><summary>拓展：可能存在关系（1 条）</summary>

- [[p 值的常见误读与 0.05 阈值]] — contrasts_with

</details>

## 复习卡
p 值的定义是什么？::假定零假设成立时，得到至少与观测结果一样极端的结果的概率。
零假设通常指什么？::通常是“没有效应”。
p 值很小意味着什么？::如果真的没有效应，这样的数据会让人意外。
