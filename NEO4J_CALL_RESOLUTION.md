# Java call resolution

Java parsing keeps the method name, receiver expression, declared receiver type,
argument count, and source line for each call. The existing `calls` string list
is retained for compatibility. `ErrorEvent` is unchanged.

`phases/java_calls.py` resolves declarations within the indexed source using
package/import identity and argument count. Calls through interfaces point to
interface declarations. Ambiguous overloads, duplicate qualified class names,
unknown/chained receiver types, and dependency methods whose source is absent
remain unresolved. No same-name fallback is used for Java. Non-Java resolution
retains its existing behavior and is not a fully typed call graph.

For MyRag, `updateClientInfo` calls `clientRepository.findById`. That method is
inherited from an external dependency, so no local `CALLS` edge should exist.
The repository import is still a valid file dependency. The locally declared
`ClientRepository.findByEmail` can be linked directly.

Graph tools return direct callers plus explicitly labelled
`possible_interface_dispatch` callers. Those candidates are not proof of the
runtime implementation selected by dependency injection. Speculative
same-name import inference has been removed. Genuine recursion is retained.

## Refresh an existing index

From the activated environment in `ams_monitoring-`:

```powershell
python -m phases.neo4j_ingest "C:\Users\SharvitNileshKashika\Downloads\MyRag" ffe4e073
```

Java caches are invalidated by source hash or parser version. This command can
regenerate Java summaries after invalidation, but it does not run vector ingestion.
Outgoing Java `CALLS` edges for the supplied files and knowledge ID are replaced
in one transaction. Failures roll back that replacement. No manual Cypher deletion
is needed. Other knowledge snapshots and graph nodes are not deleted. Any failed
relationship stage propagates an error instead of marking ingestion complete.

The operation is not an atomic rebuild of the entire knowledge graph. Avoid
concurrent ingestion of the same knowledge ID; production snapshot publishing
requires a separate concurrency/versioning design.

## Verify

```powershell
python -B -m unittest discover -s tests -p test_java_call_resolution.py -v
```

Rerun the existing graph-tool verification with `ffe4e073`. Expect no local
`findById` target for `updateClientInfo`, `ClientRepository.findByEmail` as the
target of the service's email lookup, and labelled possible callers via the
service interface. File dependencies and call dependencies are different:
importing a type alone does not establish a method call.
