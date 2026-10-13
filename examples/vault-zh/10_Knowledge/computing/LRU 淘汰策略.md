---
id: kc_lru-eviction-policy
title: LRU 淘汰策略
kind: mechanism
domain: computing
pillar: 系统
topic: 缓存
tags:
- review
- flashcards/computing
sources:
- path: 40_Sources/948ef92b4909fda1/缓存.md
  sha256: 948ef92b4909fda1fdb38908d0638863713a62fdae6527e5b3cdab0cd991b11d
  locator: 缓存已满而又要存入新条目时，必须淘汰（evict）一个旧条目。最近最少使用（least recently used，LRU）淘汰策略移除最久没有被使用的条目，赌的是时间局部性。
relations:
- type: depends_on
  target: kc_locality-of-reference
  note: LRU 的合理性来自时间局部性。
- type: required_by
  target: kc_lru-hash-linked-list
  note: 该实现卡说明如何高效实现 LRU。
- type: required_by
  target: kc_lru-scan-weakness
  note: 扫描弱点是 LRU 的已知局限。
- type: contrasts_with
  target: kc_cache-invalidation
status: confirmed
created: 2026-10-13
---
## 本质
缓存已满要存新条目时必须淘汰旧条目，LRU 移除最久没有被使用的条目，赌的是时间局部性。

## 命题
- P1. 缓存已满而又要存入新条目时，必须淘汰（evict）一个旧条目。
- P2. 最近最少使用（least recently used，LRU）淘汰策略移除最久没有被使用的条目。
- P3. LRU 赌的是时间局部性：最近没用过的数据，不太可能很快再被用到。

## 直觉与易混对比
- 直觉：最久没碰过的东西，最不可能马上被需要。

## 相关
- depends_on:: [[访问局部性]]（LRU 的合理性来自时间局部性。）
- required_by:: [[LRU 的哈希表加双向链表实现]]（该实现卡说明如何高效实现 LRU。）
- required_by:: [[LRU 在大规模扫描下表现差]]（扫描弱点是 LRU 的已知局限。）
- contrasts_with:: [[缓存失效]]

## 复习卡
缓存什么时候必须淘汰条目？::缓存已满而又要存入新条目时。
LRU 淘汰哪个条目？::最久没有被使用的条目。
LRU 策略赌的是什么？::时间局部性。
