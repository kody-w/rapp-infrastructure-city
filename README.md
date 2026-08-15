# Rappter Infrastructure City

Your real infrastructure as a live Minecraft city.

- Every GitHub repository is a tower.
- Every workflow is a colored roof light.
- Every tailnet machine, supervised daemon, and sentinel has a building.
- Health changes recolor or rebuild only the affected structures.
- `!inspect` reveals the evidence behind the nearest building.
- `!repair` creates a one-time, expiring approval request. It never repairs
  anything from inside Minecraft.

## Scale

The first live snapshot contains:

- 565 repositories
- 546 workflows
- 583 buildings
- 1,129 evidence-backed entities

The generated district occupies the reserved north world zone
`x=-220..220, y=4..40, z=64..370`; existing append-only AIVM structures remain
at `z=-24..24`.

## Legend

| Status | Block |
|---|---|
| Healthy | Emerald |
| Active/running | Diamond |
| Warning | Gold |
| Critical | Redstone |
| Offline | Coal |
| Unknown | Quartz |

Repository towers use one roof light per workflow. Critical buildings also
raise a redstone-lamp alarm mast.

## Safety

The city does not expose raw Minecraft commands.

- The bridge accepts only typed `fill`/`setblock` operations.
- Every coordinate must be inside both its building bounds and the dedicated
  city zone.
- Blocks come from a fixed allowlist.
- Plans are capped at 750 structures, 1,500 features, and 5,000 operations.
- Changed structures are applied through score-one mutation proofs.
- Chunks are loaded temporarily; only chunks added by this operation are
  removed afterwards.
- The bridge stores append-only reservation/completion/failure evidence.
- Repairs are shell-free allowlisted operations and require an external token.

## In Minecraft

Stand near a building or include its name:

```text
!city
!inspect
!inspect localFirstTools-main
!repair CI
!cityhelp
```

`!repair` only writes a pending request and displays a six-character token.
Approve outside Minecraft:

```bash
python3 ~/.rapp/hub/minecraft/infrastructure-city/runtime/repair_approval.py list
python3 ~/.rapp/hub/minecraft/infrastructure-city/runtime/repair_approval.py approve TOKEN
```

Tokens expire after ten minutes and transition to `executing` before the
external command runs, so concurrent approvals cannot execute twice.

## Install

Requires the infrastructure-city bridge contract merged in
[`rbox-rappters-2026/rapphub#25`](https://github.com/rbox-rappters-2026/rapphub/pull/25).

```bash
curl -fsSL \
  https://raw.githubusercontent.com/kody-w/rapp-infrastructure-city/main/install.sh \
  | sh
```

The resident `KeepAlive` process refreshes local health every five minutes and
uses a fifteen-minute GitHub cache. Workflow runs are attributed by workflow
ID, so duplicate workflow names cannot share health or repair evidence. A
single transient repository API failure
degrades only that tower and preserves its prior evidence.

## Tests

```bash
python3 test_city.py
python3 test_collector.py
python3 city_daemon.py --dry-run
```

The dry run builds and validates the complete real plan without mutating
Minecraft.
