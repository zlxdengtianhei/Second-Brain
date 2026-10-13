# Lantern queues

A lantern queue keeps every item in arrival order but lets a reader skip ahead by lit markers.

## Lit markers

A writer lights a marker every eight items. A reader that holds a marker can jump straight to it without scanning the items in between.

Markers cost one extra word of memory each, so a queue of 800 items carries 100 markers.

## Dimming

When the queue is compacted, markers before the cut are dimmed rather than deleted; dimmed markers stay readable but are never followed.

Thanks for reading, see you next week!
