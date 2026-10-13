# Caches

## What a cache is

A cache is a small, fast store that keeps copies of data from a larger, slower store, so that repeated requests can be served quickly. CPUs have caches in front of main memory, web browsers cache pages, and databases cache disk blocks in RAM. A request that is found in the cache is a hit; one that must go to the slow store is a miss.

## Hit ratio

The hit ratio is the share of requests served from the cache. The average access time is roughly hit ratio × cache time + (1 − hit ratio) × slow-store time. Because the slow store can be a hundred times slower, small changes in the hit ratio matter a lot: with a 1 ns cache in front of a 100 ns memory, a 90 percent hit ratio gives about 10.9 ns per access on average, and a 99 percent hit ratio gives about 2 ns.

## Locality of reference

Caches work because programs do not access data at random. Temporal locality: data used recently is likely to be used again soon. Spatial locality: data stored near recently used data is likely to be used soon, which is why caches load whole blocks rather than single bytes. A loop over an array benefits from both. A program that jumps at random through a large data set gets little benefit from any cache.

## Eviction and LRU

When the cache is full and a new item must be stored, an old item has to be evicted. Least recently used (LRU) eviction removes the item that has gone unused for the longest time, betting on temporal locality. A common implementation combines a hash table, which finds an item in constant time, with a doubly linked list ordered by last use. On a hit, the item moves to the front of the list. On a miss with a full cache, the item at the back of the list is evicted and the new item is added at the front. LRU does badly on one large scan that touches every item once: the scan pushes out everything useful.

## Cache invalidation

A cached copy becomes stale when the original changes. Systems either give each copy a time-to-live after which it is discarded, or have the original notify the caches when it changes. Getting this wrong serves outdated data, which is why cache invalidation has a reputation as one of the hard problems in computing.
