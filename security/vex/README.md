# VEX documents

[OpenVEX](https://openvex.dev) statements for scanner findings in images the chart deploys that are verified not to affect us. Scanners that read VEX (for example `trivy image --vex <file>`) suppress the listed findings and show them with `--show-suppressed`.

## laminar-postgres-gosu.openvex.json

Covers the 22 critical/high findings Trivy reports against `/usr/local/bin/gosu` in `postgres:15`, the database image of the Laminar subchart. `gosu` v1.19.0 is built with Go 1.24.6, so scanners flag Go standard-library CVEs by toolchain version.

- Binary checked: `gosu` from `postgres:15` (linux/amd64, `postgres@sha256:c961aa287d8698297cb26cdfadfbe9fd2cbaf77e53cfffe9636e8d8a1e4d842c`), sha256 `52c8749d0142edd234e9d6bd5237dff2d81e71f43537e2f4f66f75dd4b243dd0`.
- Evidence: `govulncheck -mode=binary` on that binary. 21 findings are in packages not compiled into `gosu` (`vulnerable_code_not_present`); CVE-2026-39822 is in `os`, which is linked, but the vulnerable function is never called (`vulnerable_code_not_in_execute_path`).
- Scope: statements match `pkg:golang/stdlib@v1.24.6` inside `postgres` images only. When `gosu` is rebuilt with a newer Go they stop matching.

Regenerate when `gosu` or its Go version changes, or when Trivy reports a new Go CVE against it:

```bash
C=$(docker create --platform linux/amd64 postgres:15)
docker cp "$C":/usr/local/bin/gosu ./gosu && docker rm "$C"
docker run --rm -v "$PWD":/w -w /w golang:1.26 sh -c \
  'go install golang.org/x/vuln/cmd/govulncheck@latest && govulncheck -mode=binary -format json gosu' > govulncheck.json
trivy image --scanners vuln --severity CRITICAL,HIGH --vex security/vex/laminar-postgres-gosu.openvex.json --show-suppressed postgres:15
```
