# Durability & Recovery

- **Truth:** markdown in this repo. **Index:** pgvector (disposable).
- **Primary backup:** private git remote (`origin`). Commit + push regularly.
- **Secondary:** one-way Drive mirror via `scripts/mirror-to-drive.sh` (hourly launchd).
  Excludes `.git/` and infra to avoid corruption and churn.

## Restore / new machine
1. `git clone <private remote> ~/development/knowledge-base`
2. `cd ~/development/knowledge-base && make up`
3. `make reindex`   # rebuilds pgvector from markdown
4. `make health`    # healthy
The DB is never restored from backup — it is always rebuilt from markdown.
