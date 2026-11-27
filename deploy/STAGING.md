# Staging: the v2 test copy on the same server

Staging runs the `staging` branch (BRANCHING.md) at:

| Name | What |
| --- | --- |
| `https://staging.securepaysg.me` | The marketing website from the `staging` branch (Vercel) |
| `https://gateway.staging.securepaysg.me` | Gateway, checkout, dashboard, status data |
| `https://shop.staging.securepaysg.me` | Demo shop |
| `https://mail.staging.securepaysg.me` | Staging demo inbox (its own password) |

It is on the production VM but kept apart from production:

- **Own Compose project** `securepay-staging`, so its own containers, private networks and
  volumes: its own PostgreSQL data, its own Caddy CA, its own demo inbox.
- **Own clone** at `~/securepay-staging` (branch `staging`) with its own `deploy/.env` and
  fresh `deploy/secrets/`: none of production's keys, passwords or API keys.
- **No published ports.** Production's Caddy owns ports 80 and 443. When `STAGING_DOMAIN` is
  set in production's `deploy/.env`, it gets certificates for the three staging names and
  passes their requests to the staging Caddy over one private network
  (`securepay-staging-link`, `deploy/caddy/staging.caddy.tmpl`). Only the two Caddy
  containers are on it. The staging Caddy then applies exactly production's routes and
  headers (`deploy/caddy/staging-caddyfile.sh` makes its config from `Caddyfile.prod`).
- **No real email.** No mail provider, whatever its `.env` says (`compose.staging.yaml`
  forces it): emails to demo addresses (`@example.com` and so on) land in the staging inbox,
  emails to real addresses are not sent at all. Test with demo addresses.
- **Memory caps** on every staging container and fewer gunicorn workers, so staging cannot
  take production's memory. A staging deploy refuses to build when the server is short of
  memory, and never runs during a demo freeze or at the same time as a production deploy.
- Search engines are told not to index the staging names.

If staging is stopped, its names answer 502. Production does not notice.

## Memory: check before turning it on

The VM is an e2-medium: 4 GB of memory and 2 shared vCPUs. Our estimate (not measured on the
server yet): production uses roughly 1.5 to 2 GB, staging adds about 1.4 GB, and building
staging's images (the checkout and dashboard bundles are built on the server) needs another
0.8 GB or so for a few minutes. That is close to 4 GB, with no swap. Run the check on the
server, once this change is on `main` and deployed:

```bash
bash ~/csit321-secure-pay/deploy/headroom.sh
```

It prints real numbers for both stacks and a verdict. Our recommendation if it says "too
tight": **upgrade the VM to e2-standard-2** (8 GB, 2 full vCPUs, builds get faster too), at
about twice the e2-medium price, paid from the free-trial credit until billing moves to paid.
It needs a stop and start, so about 2 to 3 minutes of downtime, and never during a demo
freeze. The static IP and all data stay.

Google Cloud console > **Compute Engine > VM instances** > `securepay` > **Stop** (wait until
it says stopped) > **Edit** > **Machine configuration**: series E2, machine type
**e2-standard-2** > **Save** > **Start**. The containers start by themselves
(`restart: unless-stopped`); after a minute, `bash deploy/smoke-check.sh securepaysg.me`.
Check the budget alert still fits the new price.

Disk: each stack's images take a few GB of the 30 GB disk. `headroom.sh` shows how much is
free; `staging-deploy.sh` removes old image layers after each build.

## Set-up (once, by hand)

### 1. DNS (Namecheap, about 5 minutes to take effect)

Domain List > `securepaysg.me` > **Manage** > **Advanced DNS** > **Add new record**, four times:

| Type | Host | Value | TTL |
| --- | --- | --- | --- |
| A Record | `gateway.staging` | `35.213.144.96` | 5 min |
| A Record | `shop.staging` | `35.213.144.96` | 5 min |
| A Record | `mail.staging` | `35.213.144.96` | 5 min |
| CNAME Record | `staging` | the value Vercel shows in step 6 (for example `cname.vercel-dns.com.`) | 5 min |

Check from a laptop: `dig +short gateway.staging.securepaysg.me` prints `35.213.144.96`.

### 2. Deploy this change to production

Once the pull request that adds staging is merged into `main` (CI or local checks green),
deploy as usual:

```bash
bash ~/csit321-secure-pay/deploy/auto-deploy.sh
```

It changes nothing that users see: production's Caddy is rebuilt and restarts once (a few
seconds), now able to forward staging names, but it forwards nothing until step 4. Then bring
the change into `staging` and `dev` (BRANCHING.md, "A v1 fix", the sync pull requests), so
the staging clone has `deploy/compose.staging.yaml`.

### 3. Make the staging clone (on the server, as `securepay`)

```bash
bash ~/csit321-secure-pay/deploy/headroom.sh        # the memory check above, first
bash ~/csit321-secure-pay/deploy/staging-setup.sh
```

It clones the repository into `~/securepay-staging` on the `staging` branch, makes its own
`deploy/.env` (`DOMAIN=staging.securepaysg.me`, `COMPOSE_PROJECT_NAME=securepay-staging`)
and fresh secrets, and copies production's fraud model folder. It prints the **staging inbox
password once**: put it in the team password manager next to production's (it is a
different one). Production is not touched.

### 4. Let production's Caddy forward the staging names

The one change to production's settings: add a line to production's `deploy/.env` and
restart only its Caddy (a few seconds):

```bash
echo 'STAGING_DOMAIN=staging.securepaysg.me' >> ~/csit321-secure-pay/deploy/.env
docker compose -f ~/csit321-secure-pay/deploy/compose.prod.yaml up -d caddy
docker compose -f ~/csit321-secure-pay/deploy/compose.prod.yaml logs --tail 30 caddy
```

The log shows "certificate obtained successfully" for the three staging names once DNS
(step 1) has taken effect. To undo: delete the line and run the same `up -d caddy`.

### 5. Start staging and register its demo shop

```bash
bash ~/securepay-staging/deploy/staging-deploy.sh   # first build: about 10 minutes
```

It ends with the smoke check against `staging.securepaysg.me`. Then the demo shop and staff,
as in RUNBOOK.md step 7 but with the staging project, from the staging clone:

```bash
cd ~/securepay-staging
P="docker compose -p securepay-staging -f deploy/compose.prod.yaml -f deploy/compose.staging.yaml"
$P exec gateway python manage.py create_merchant "Demo Shop" \
  --webhook-url "https://shop.staging.securepaysg.me/webhooks/securepay" \
  | tee /dev/stderr | awk '/API key:/{print $3 > "deploy/secrets/securepay_api_key"}
                           /Signing secret:/{print $3 > "deploy/secrets/securepay_signing_secret"}'
$P restart merchant
$P exec gateway python manage.py create_staff admin@securepay.sg --role administrator --name "Mei Tan"
$P exec merchant python manage.py restock         # demo stock, as on demo mornings
```

Staff sign in at `https://gateway.staging.securepaysg.me/dashboard/`; their emails land in
`https://mail.staging.securepaysg.me`. Use demo addresses (`@example.com`, `@securepay.sg`)
for every staging account.

### 6. The website's staging copy (Vercel)

Vercel already builds a preview of the website for every branch. To give `staging` its own
address and point it at the staging server, in the website's Vercel project:

1. **Settings > Domains > Add Domain**: `staging.securepaysg.me`. Choose **Connect to an
   environment: Preview**, and Git branch **`staging`**. Vercel shows the CNAME value for
   step 1.
2. **Settings > Environment Variables > Add**:
   `VITE_GATEWAY_URL` = `https://gateway.staging.securepaysg.me` and
   `VITE_SHOP_URL` = `https://shop.staging.securepaysg.me`, environment **Preview** only
   (all preview branches, so `dev` previews use staging too and never production's server).
   Leave the Production values as they are.
3. **Settings > Git**: check that **Production Branch** is `main`.
4. **Deployments**: redeploy the newest `staging` deployment so it picks up the variables.

`dev` then has a preview link on every push (shown on its pull requests and under
Deployments), and `staging.securepaysg.me` always shows the newest `staging`. Production
(`www.securepaysg.me`) still deploys from `main` only.

### 7. Branch protection

Once GitHub Pro is active: BRANCHING.md, "Branch protection" (one ruleset for `main`,
`staging` and `dev`, requiring `ci-ok`).

### 8. Optional

- Claude's cloud environment: add `gateway.staging.securepaysg.me`,
  `shop.staging.securepaysg.me` and `mail.staging.securepaysg.me` to its allowed domains, so
  a Claude thread can check staging.
- No uptime monitor needed: staging may be down.

## Daily use

```bash
bash ~/securepay-staging/deploy/staging-deploy.sh            # deploy the newest staging
bash ~/securepay-staging/deploy/staging-deploy.sh <commit>   # one commit on staging
bash ~/securepay-staging/deploy/staging-deploy.sh --status   # containers and memory
bash ~/securepay-staging/deploy/staging-deploy.sh --stop     # stop, keep its data
```

Logs: `cd ~/securepay-staging && $P logs --tail 100 gateway` (with `P` as in step 5).
Integration tests against staging: `$P run --rm integration-tests`.
Each deploy is logged in `~/staging-deploy.log`; the running commit is in
`~/staging-deployed-revision`.

Before a demo: `--stop` staging along with the merge freeze (RUNBOOK.md section 10a), so the
demo has all the memory and CPU. Start it again afterwards with a normal deploy.

### Checking it

After a promotion, besides the smoke check: pay in `https://shop.staging.securepaysg.me` with
`4242 4242 4242 4242` (the order turns paid within seconds), read the code in the staging
inbox, and open the payment in the staging dashboard.

### Starting over

Staging data can be thrown away at any time (for example after reworking a v2 migration):

```bash
cd ~/securepay-staging
$P down -v              # deletes staging's data only: its volumes are securepay-staging_*
bash deploy/staging-deploy.sh
```

then step 5 again (shop and staff). Never run `down -v` in `~/csit321-secure-pay`: that is
production.

## Safety rules built in

- Every `docker compose` command in `~/securepay-staging` uses the staging project, because
  its `deploy/.env` says `COMPOSE_PROJECT_NAME=securepay-staging` (keep that line).
  `staging-deploy.sh` refuses to run without it, from a clone not on `staging`, or with
  production's domain.
- Staging's database image has its own tag (`securepay-staging-db:local`), so a staging build
  never replaces the image production's database runs from.
- Stop staging before `docker compose ... down` on production: the shared link network cannot
  be removed while the staging Caddy is on it (production's containers still stop).
