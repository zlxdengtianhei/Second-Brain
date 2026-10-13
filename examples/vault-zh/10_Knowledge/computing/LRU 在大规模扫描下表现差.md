---
id: kc_lru-scan-weakness
title: LRU 在大规模扫描下表现差
kind: fact
domain: computing
pillar: 系统
topic: 缓存
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/948ef92b4909fda1/缓存.md
  sha256: 948ef92b4909fda1fdb38908d0638863713a62fdae6527e5b3cdab0cd991b11d
  locator: 遇到一次把每个条目都访问一遍的大规模扫描，LRU 表现很差：这次扫描会把所有有用的条目都挤出去。
relations:
- type: depends_on
  target: kc_lru-eviction-policy
  note: 这是 LRU 策略的局限。
status: confirmed
created: 2026-10-13
---
## 本质
遇到把每个条目都访问一遍的大规模扫描，LRU 会把所有有用的条目都挤出缓存。

## 命题
- P1. 遇到一次把每个条目都访问一遍的大规模扫描，LRU 表现很差。
- P2. 原因是这次扫描会把所有有用的条目都挤出去。
- P3. 这是 LRU 的已知局限：扫描访问不符合 LRU 所赌的时间局部性。

## 直觉与易混对比
- 直觉：扫描里的每个条目都是“刚用过”的，却几乎不会再用，反而把真正常用的条目挤成“最久没用”。

## 相关
- depends_on:: [[LRU 淘汰策略]]（这是 LRU 策略的局限。）

## 复习卡
LRU 在什么访问模式下表现很差？::一次把每个条目都访问一遍的大规模扫描。
为什么大规模扫描会让 LRU 表现很差？::这次扫描会把所有有用的条目都挤出缓存。
