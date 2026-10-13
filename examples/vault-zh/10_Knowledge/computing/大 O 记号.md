---
id: kc_big-o
title: 大 O 记号
kind: concept
domain: computing
pillar: 算法
topic: 复杂度
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/5dc5cc20ee6359f4/大 O 记号与二分查找.md
  sha256: 5dc5cc20ee6359f4698e2a2dc042226a1c1ffb859e7652c0fe3eaf5b1b464593
  locator: 大 O 记号（Big-O notation）描述算法的运行时间或内存占用怎样随输入规模增长，忽略常数因子和较小的项。
relations:
- type: has_part
  target: kc_complexity-classes-order
  note: 常见复杂度类别是大 O 记号的具体取值。
- type: required_by
  target: kc_logarithmic-time
status: confirmed
created: 2026-10-13
---
## 本质
大 O 记号用增长的上界描述算法的时间或内存怎样随输入规模增长，忽略常数因子和较小的项。

## 命题
- P1. 大 O 记号描述算法的运行时间或内存占用怎样随输入规模增长。
- P2. 大 O 忽略常数因子和较小的项：规模为 n 时需要 3n + 5 步的算法是 O(n)，需要 n²/2 步的算法是 O(n²)。
- P3. 大 O 给出的是增长的上界，说明算法如何随规模扩展，而不是它在某个具体小输入上有多快。
- P4. 常数小的 O(n²) 算法，在短列表上可能胜过 O(n log n) 算法。

## 直觉与易混对比
- 直觉：大 O 看的是规模变大后的趋势，不是某一次运行的秒数。
- 易混对比：大 O 不是具体输入上的实际速度：渐近更慢的算法在小输入上可能更快。

## 相关
- has_part:: [[常见复杂度类别的增长顺序]]（常见复杂度类别是大 O 记号的具体取值。）
- required_by:: [[对数时间]]

## 复习卡
大 O 记号描述算法的什么？::算法的运行时间或内存占用怎样随输入规模增长，忽略常数因子和较小的项。
需要 3n + 5 步的算法，它的大 O 是什么？::O(n)，因为常数因子和较小的项被忽略。
为什么常数小的 O(n²) 算法可能比 O(n log n) 算法更快？::大 O 只给增长的上界，说明随规模扩展的趋势；在短列表等小输入上，常数因子起主要作用。
