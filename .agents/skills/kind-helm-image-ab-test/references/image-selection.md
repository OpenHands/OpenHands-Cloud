# Choosing control vs treatment images for a same-DB swap

The whole method depends on control and treatment sharing the DB. That is only
safe when they share an alembic head and differ by exactly the PR commits.

## Derive the two SHAs
With the PR branch fetched in the enterprise checkout:
```bash
git fetch origin
git log --oneline <main-or-release>..<pr-head>     # inspect the PR's own commits
BASE=$(git rev-parse "<first-pr-commit>^")          # parent of the first PR commit
echo "control   = sha-$(git rev-parse --short=7 $BASE)"
echo "treatment = sha-$(git rev-parse --short=7 <pr-head>)"
git log --oneline $BASE..<pr-head>                  # must be ONLY the PR commits
```
Confirm the treatment tag matches the PR's CI image (PR body → "Enterprise server
image for this PR"). Verify both are pullable with `docker manifest inspect`.

## Prove they share a schema (no migration delta)
```bash
git diff --stat $BASE..<pr-head> -- migrations/     # expect empty
```
If empty, control and treatment have the same alembic head and swap cleanly on one
DB.

## Match the running DB revision
```bash
kubectl --context "$KCTX" exec -n <ns> <postgres-pod> -- sh -c \
  'PGPASSWORD=$POSTGRES_PASSWORD psql -U postgres -d <appdb> -tAc \
   "select version_num from alembic_version;"'
# both images must contain that revision:
git grep -l "<rev>" $BASE -- 'migrations/versions/*'
git grep -l "<rev>" <pr-head> -- 'migrations/versions/*'
```

## Why not the release tag as control
A release tag (e.g. `1.61.0`) is frequently **older** than the branch base (which
sits on a newer `main`). If the DB has already been migrated to a revision the
release tag does not know, the release tag's `migrate-db` fails
`Can't locate revision identified by '<rev>'` and the pod never boots — so it is
**not** a usable control on that DB. The branch-base SHA is the correct control:
it is exactly `treatment − PR commits` and carries the same migrations.

## Verifying the live build
After swapping, confirm the running code is what you intend by inspecting a
changed symbol (function signature, attribute) rather than trusting the tag alone.
