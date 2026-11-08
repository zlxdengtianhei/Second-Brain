A `partial` ingest job frozen by sb 7e42c7b (method `ingest/3`), with the vault it left: the invented source of
`tests/ingest_helpers.py` (lantern queues), one repair round, one card still pending. Made by running that version's
`ingest.prepare` and `ingest.advance` with `FakeExecutor`s (the scripted answers of its tests); the job's packets
(audit copies of the prompts) are left out. `tests/test_old_jobs.py` copies `vault/` to a temporary folder and
`job/` into its state root.
