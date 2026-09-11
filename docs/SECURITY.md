# SAT-SA — Security

A submission arrives from an external entity and is **untrusted input**.
It will sometimes be malformed, truncated, wrongly packaged, or hostile.

## Nothing in a dataset is ever executed

No submission content is imported, evaluated, deserialised into objects,
or used to resolve a module or path. Parsing is `pandas.read_csv`,
`json.load`, and a read-only SQLite connection — all pure parsers. There
is no code path by which dataset contents become executable.

## Archive extraction

`ZipFile.extractall` is **not used**. Python's own documentation warns it
can write outside the destination, and it has no size budget, so a zip
bomb succeeds until the disk is full. Every member is checked **before a
byte is written**.

| Check | Blocks |
|---|---|
| Parent-directory traversal | `../../../etc/passwd` |
| Absolute paths | `/etc/passwd` |
| Windows drive letters | `C:/Windows/…` — *not* absolute under POSIX `os.path.isabs` |
| UNC paths | `//host/share/…` |
| Backslash separators | `..\..\evil.csv` |
| Control characters / null bytes | a name truncated at a null by a C-level call can differ from what Python validated |
| Symlinks and special files | the standard way out of an extraction root once a path check passes |
| Post-`realpath` destination | the check that actually holds |
| Member count | denial of service by entry count |
| Per-member size | one enormous file |
| Total expansion | 2 GB default, configurable |
| Compression ratio | 1000:1 — not reachable by CSV or JSON |
| Streaming byte count | the header's size is attacker-controlled and can lie |
| Extension allow-list | only `.csv`, `.json`, `.sqlite`, `.db`, `.sqlite3` are extracted |

### Verified by attack

Real malicious archives are built and run in `tests/test_ingestion.py`:

```
path traversal ../         BLOCKED    symlink member      BLOCKED
absolute path              BLOCKED    zip bomb (1029x)    BLOCKED
windows drive letter       BLOCKED    10,050 members      BLOCKED
UNC path                   BLOCKED    no data files       REJECTED
backslash traversal        BLOCKED    corrupt zip         REJECTED
→ /tmp/pwned.csv exists: False    /etc/pwned.csv exists: False
```

Roughly half the ingestion tests are adversarial.

### Configurable limits cannot disable a guard

Raising the archive size budget in Settings does not raise the
compression-ratio bound. A configurable limit must not become a way to
switch a protection off; a test pins that.

## Other input handling

- **SQLite** is opened read-only through a `file:…?mode=ro` URI, so
  assessing a submission cannot modify it. Extension loading is left
  disabled (the `sqlite3` default), so a crafted database cannot load a
  shared library.
- **Table names in SQL** come from SAT-SA's own allow-list, never from
  the file, so they cannot carry injected SQL.
- **Encoding**: non-UTF-8 input is reported as such rather than
  mangled.
- **Size**: single files are bounded like archives.
- **Malformed content** — bad JSON, unparseable CSV, a file that is not
  a database — produces a supervisor-readable `IngestionError`, never a
  raw `OSError` or a stack trace.

## Dataset contents cannot control behaviour

Rules, thresholds and capability mappings come from
`config/assessment_rules.yaml`, never from a submission. A dataset
cannot introduce a rule, change a weight, or alter how it is assessed.

## AI safety boundary

A language model receives a **defensive deep copy** of a finding and can
write nothing back. See [AI.md](AI.md).

## Data handling

- **Nothing leaves the machine.** The only URL anywhere in the codebase
  is `http://localhost`, for an optional local model.
- Assessments are written to local directories chosen by the operator.
- Settings are a plain JSON file under the user's config directory —
  auditable with `cat`.
- No telemetry, no analytics, no update check, no crash reporting.

## Reporting a vulnerability

This is a Smart India Hackathon project (SIH26157) and is not
production-deployed. Raise security findings as GitHub issues.
