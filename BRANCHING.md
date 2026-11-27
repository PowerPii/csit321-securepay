# Branches and environments

From 27 Nov 2026 we build v2 next to v1. v1.0.0 stays exactly as it is on `main` and on the
live server until after the M4 prototype demo on 19 Dec 2026 (our supervisor asked us to keep
v1 as it is). v2 work lands on `dev`, the staging server runs `dev`, and v2 reaches `main`
only after M4.

```
feature branch ──PR──> dev ──────────────PR (after 19 Dec 2026)──> main
   (v2 work)        (v2 builds up here;                       (production, v1.0.0
                     staging.securepaysg.me runs it)            until v2 ships)

hotfix/* from main ──PR──> main, then main is merged back into dev
```

One pull request per feature. There is no separate `staging` branch to promote into
(decided 27 Nov 2026: the second pull request was friction for a six-person team, and
staging is a test box, not a release gate). The old `staging` branch is kept, frozen, only
for history; nothing merges into it any more.

## The two branches

| Branch | What it holds | Where it runs | How it gets there | Email |
| --- | --- | --- | --- | --- |
| `main` | Production: v1.0.0 (plus v1 fixes and docs) until v2 ships | `securepaysg.me` (website on Vercel), `gateway.`, `shop.`, `mail.securepaysg.me` (server) | Automatic deploy after CI passes on `main` (while Actions minutes are used up: `bash ~/csit321-secure-pay/deploy/auto-deploy.sh`) | Real (Resend) for real addresses |
| `dev` | v2, feature by feature | Your laptop (`deploy/compose.yaml`); the staging stack on the server, `gateway.`, `shop.`, `mail.staging.securepaysg.me`, deployed by hand; the website at `staging.securepaysg.me` (Vercel, newest `dev`) and a Vercel preview for every push | Merging pull requests | Staging: never leaves the server (staging inbox). Laptop: local Mailpit |

Both only change through pull requests, and every pull request needs a green `ci-ok` check
(or, while Actions minutes are used up until 1 Dec 2026, the same checks run locally and
named in the pull request).

## Day to day

**v2 work (most of us, from now on).** Branch from `dev`, open the pull request into `dev`:

```bash
git fetch origin && git switch -c feat/ledger-core origin/dev
# ... work, commit ...
git push -u origin feat/ledger-core      # then open the PR with base: dev
```

Squash-merging a feature pull request into `dev` is fine.

**Testing on staging.** Staging runs `dev`. After merging a feature (or a few), deploy
the newest `dev` on the server and test there:

```bash
bash ~/securepay-staging/deploy/staging-deploy.sh            # newest dev
bash ~/securepay-staging/deploy/staging-deploy.sh <commit>   # or one commit on dev
```

Anyone with access to the server can do it. Merge only finished work into `dev`: whatever is
on `dev` reaches staging on the next deploy. A problem found on staging is fixed on `dev`
with a normal feature pull request.

**Releasing v2 (after M4, 19 Dec 2026 at the earliest).** A pull request from `dev` into
`main`, merged with a merge commit, by Fifa. The merge deploys production as usual. Tag it
(`v2.0.0`) and write its release notes, as for v1.0.0. Take a backup first (RUNBOOK.md
section 9): v2's database migrations run on production's data for the first time here.

**A v1 fix before then (the M3/M4 documents, a demo bug).** Branch from `main`
(`hotfix/<short-name>` or `docs/<short-name>`), pull request into `main`. Then bring the fix
into `dev` so they do not drift:

```bash
# Pull request: base dev, compare main ("Sync main into dev"), merge commit.
```

**Never** merge `dev` into `main` before M4, and never open a v2 pull request
against `main`. The merge freeze before a demo (RUNBOOK.md section 10a) only blocks pull
requests into `main`; `dev` keeps going, but staging deploys pause while the
server's freeze is on, and staging is stopped for the demo itself.

## Database migrations

v2 will add tables and migrations (the ledger, test and live mode, and so on). They run on
the staging database first, which may be reset at any time
(`deploy/STAGING.md`, "Starting over"). Once a migration is merged into `dev`, do not edit
it: add a new one (staging may already have run it). Production runs them only when v2 is merged into `main`.

## CI

`.github/workflows/ci.yml` runs on every pull request, whatever its base, and on every push
to `main`, `staging` and `dev` (that is, after each merge; `staging` is retired). The single check to require is
`ci-ok`. Only `main` deploys from CI (`.github/workflows/deploy.yml`); staging is deployed by
hand.

## Branch protection (once GitHub Pro is active)

GitHub, repository **Settings > Rules > Rulesets** (or **Branches**, if `main` already has a
classic rule: add the same rule for `staging` and `dev`). One ruleset is enough:

1. **New ruleset > New branch ruleset**, name `long-lived branches`, enforcement **Active**.
2. **Target branches > Add target > Include by pattern**: `main`, `staging`, `dev`.
3. Tick **Restrict deletions** and **Block force pushes**.
4. Tick **Require a pull request before merging**, with **Required approvals: 0** (so one
   person can merge their own pull request; teammates still review).
5. Tick **Require status checks to pass**, add the check **`ci-ok`**.
6. Leave the bypass list empty, or add only Fifa (useful while Actions minutes are used up:
   `ci-ok` cannot run, so nothing could merge).
7. **Create**.

## Who does what

- Anyone: feature pull requests into `dev`.
- Whoever leads a sprint (or anyone, after merging): the staging deploy of `dev` and a quick
  check (`deploy/STAGING.md`, "Checking it").
- Fifa: the `dev` to `main` release after M4, and production deploys.
