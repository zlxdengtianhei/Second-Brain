# Hash tables and Bloom filters

## Hash functions

A hash function maps a key, such as a string, to a fixed-size number. A good hash function for a table is fast, deterministic (the same key always gives the same number) and spreads keys evenly over its output range, so that similar keys land in unrelated places. Hash functions for tables do not need to be cryptographic; cryptographic hashes such as SHA-256 add resistance against deliberately constructed collisions, at a higher cost.

## Hash tables

A hash table stores key-value pairs in an array of buckets. To find a key, compute its hash, reduce the hash to a bucket index (for example the hash modulo the number of buckets), and look in that bucket. When keys are spread evenly, a lookup, insert or delete takes constant time on average, O(1), no matter how many items are stored. In the worst case, when many keys fall into the same bucket, a lookup degrades to O(n).

## Collisions

Two different keys can map to the same bucket. This is a collision, and it cannot be avoided, because there are more possible keys than buckets. Separate chaining keeps a small list in each bucket and searches that list. Open addressing stores every item in the array itself and, on a collision, probes other buckets in a fixed order until it finds a free one.

## Load factor and resizing

The load factor is the number of stored items divided by the number of buckets. As it rises, collisions become more frequent and lookups slow down. Most implementations grow the array, often by doubling it, when the load factor passes a threshold such as 0.75, and then move every item to its new bucket. One resize costs O(n), but because resizes are rare the average cost per insert stays O(1). This is called amortised constant time.

## Bloom filters

A Bloom filter answers the question "is this item in the set?" with much less memory than storing the items. It is a bit array plus k hash functions. To add an item, set the k bits that its hashes point to. To check an item, look at those k bits: if any of them is 0, the item is definitely not in the set; if all of them are 1, the item is probably in the set. A Bloom filter has no false negatives but can have false positives, and the false positive rate grows as more items are added. Items cannot be removed from a basic Bloom filter.

Bloom filters are used as a cheap first check in front of a slow lookup, for example before reading from disk. When the items being checked are rarely in the set, a large share of the "probably yes" answers can be false positives, for the same reason that a positive screening test for a rare disease is often wrong. The slow lookup then settles the answer.
