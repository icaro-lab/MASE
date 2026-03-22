# ShadCN Component Usage Map (frontend)

Scope: `services/admin/frontend` shell and route inventory.

## Shell

- `App.jsx` uses `AppSidebar` and `SiteHeader` from `src/registry/new-york-v4/blocks/sidebar-16/`.
- The shell now covers only the operator surfaces: `/runs` and `/environments`.
- `/` is a standalone landing page outside the shell.
- Docs UI, legacy operator screens, and the sidebar user footer were removed.

## Current Routes

- `/`
  - `features/landing/LandingPage.jsx`
  - `button`, `card`

- `/runs`
  - `features/runs/RunsPage.jsx`
  - `badge`, `button`, `card`, `input`, `select`, `table`

- `/runs/:run_id/stats`
  - `features/runs/RunDashboard.jsx`
  - `alert`, `alert-dialog`, `button`, `dropdown-menu`, `tabs`
  - embedded panels use `card`, `table`, `scroll-area`, `select`

- `/runs/:run_id/traces`
  - `features/runs/RunDashboard.jsx`
  - same shell as stats view with tracing panel active

- `/runs/:run_id/traces/:trace_id`
  - `features/runs/RunDashboard.jsx`
  - full-page trace detail view

- `/environments`
  - `features/environments/EnvironmentsPage.jsx`
  - `badge`, `card`, `input`

- `/environments/:environment_id`
  - `features/environments/EnvironmentDetailPage.jsx`
  - `badge`, `button`, `card`, `input`, `label`, `select`

## Notes

- Branding is `MASE` with subtitle `Multi-Agent Simulation Environment`.
- Breadcrumbs are run/environment-first: `Runs > <run-id>` and `Environments > <environment-id>`.
- Runtime cards intentionally avoid exposing internal `agent_core` identifiers.
- Run inspection intentionally exposes only `Stats` and `Traces`; Compass is no longer part of the frontend shell.
