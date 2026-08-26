# proofray_duckdb

Repository-owned host adapter over DuckDB's native C API. Every external
database is attached with `READ_ONLY`; identifiers are quoted before being
interpolated.

The Dart API is the MIT-licensed `dart_duckdb 1.4.4` source vendored in
`../dart_duckdb_core`. Its upstream platform download scripts are deliberately
excluded. This plugin owns native distribution instead:

- Linux x86_64 and Windows x86_64 use official DuckDB 1.4.2 release binaries;
- Android arm64 compiles the official 1.4.2 amalgamation with 16 KiB page support;
- every archive is HTTPS-fetched with a frozen SHA-256 before extraction;
- the app test runner uses `PROOFRAY_DUCKDB_LIBRARY` to test that same verified
  Linux binary without relying on a system installation.

Android, Linux and Windows must each pass the app feasibility matrix before a
release claim is opened.
