# Big-O and binary search

## Big-O notation

Big-O notation describes how the running time or memory use of an algorithm grows as its input grows, ignoring constant factors and smaller terms. An algorithm that takes 3n + 5 steps on an input of size n is O(n); one that takes n²/2 steps is O(n²). Big-O gives an upper bound on growth, so it says how an algorithm scales, not how fast it is on a particular small input: an O(n²) algorithm with small constants can beat an O(n log n) one on short lists.

Common classes, from slowest-growing to fastest-growing: O(1) constant, O(log n) logarithmic, O(n) linear, O(n log n), O(n²) quadratic, O(2ⁿ) exponential.

## Binary search

Binary search finds a value in a sorted array. It keeps a range that must contain the target if the target is present at all, starting with the whole array:

1. If the range is empty, the target is not present.
2. Look at the middle element of the range.
3. If it equals the target, stop: the target is found.
4. If the target is smaller than the middle element, keep only the left half; if it is larger, keep only the right half.
5. Go back to step 1.

Each pass halves the range. The array must be sorted; on unsorted data binary search can miss values that are present. A classic bug is computing the middle as (low + high) / 2 with fixed-width integers, which can overflow on very large arrays; low + (high − low) / 2 avoids it.

## Logarithmic time

An algorithm runs in logarithmic time, O(log n), when each step cuts the remaining problem by a constant factor. Binary search on a million sorted items needs at most about 20 comparisons, because 2²⁰ is about a million; doubling the array adds only one more comparison. Balanced search trees give the same logarithmic cost for lookups while also allowing fast inserts and deletes.

## Linear search

Linear search checks every element in turn. It needs no sorting and takes O(n) time. For a single lookup in unsorted data it is the right choice, because sorting first costs O(n log n). Sorting pays off when many lookups will follow.
