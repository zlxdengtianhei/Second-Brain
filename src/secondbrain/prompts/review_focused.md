## This is a repair round: judge the fixes

`previous_findings` lists what the review of the round before sent back. For each card it names, say in `fixed` whether each finding is fixed: `[{"finding": "<the finding, short>", "fixed": true | false}]`. Send a card back (`revise`) only when a finding is not fixed, or for a blocking problem: one of the kinds of `notes` that send a card back (`fact`, `unreadable`, `quote`, `not_a_unit`, `repeats`), named in `blocking`. Any other problem you see is `minor`: write it in `notes`; it is recorded for the owner and does not send the card back.
