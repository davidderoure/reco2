# OriginId lists

One file per user group, one OriginId per line (format `XXXX-XXXX`).
Blank lines and lines beginning with `#` are ignored.

| File       | user_type | Group             |
|------------|-----------|-------------------|
| ids_0.txt  | 0         | Trial participants |
| ids_1.txt  | 1         | Test accounts      |
| ids_2.txt  | 2         | Usability trial    |

These files are gitignored — they contain participant identifiers and
must not be committed. Obtain lists from the study team under the data
sharing arrangements.

The trial analysis tools (`trial.fetch`, `trial.analyse`, `trial.timeline`)
default to `ids_2.txt` during the usability trial phase. To use a different
file pass `--ids-file ids/ids_1.txt`, or use `--participants` for a single
ad-hoc lookup.
