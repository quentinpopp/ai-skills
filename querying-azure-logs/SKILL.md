---
name: querying-azure-logs
description: Use when retrieving, filtering, or analyzing logs from deployed services in Azure Log Analytics (e.g. Dev, NonProd, Prod stages)
---

# Querying Azure Logs

## Overview

Retrieves logs from deployed microservices via Azure Log Analytics REST API.
**Core principle:** Always use the REST API directly — `az monitor log-analytics query` hangs indefinitely on large result sets. Use PowerShell `Invoke-RestMethod` instead.

The examples assume a saved KQL function `queryForServiceFromTsWithRange(service, start, range)` in each workspace that filters your log table by service name and time range. Create it first or replace the call with your own KQL.

## Configuration

| Variable | Meaning |
|---|---|
| `AZURE_TENANT_ID` | `<tenant-id>` used for `az login` |
| `AZURE_SUBSCRIPTION_ID` | `<subscription-id>` of the stage you query |
| `AZURE_LOG_ANALYTICS_WORKSPACE_ID` | `<workspace-id>` of the stage you query |

If you have one subscription/workspace per stage, keep a per-stage table (your own values, never commit real IDs):

| Stage | Subscription ID | Workspace ID |
|---|---|---|
| **Prod** | `<prod-subscription-id>` | `<prod-workspace-id>` |
| **NonProd** | `<nonprod-subscription-id>` | `<nonprod-workspace-id>` |
| **Dev** | `<dev-subscription-id>` | `<dev-workspace-id>` |

## Service Names

Use the service name exactly as it appears in your log table's `ServiceName` column, e.g. `my-service`, `my-other-service`.

## Step 1: Authenticate

```powershell
az login --tenant $env:AZURE_TENANT_ID
```

This opens a browser. After login, verify the correct subscription is default:
```powershell
az account show --subscription <SUBSCRIPTION_ID> -o table
```

## Step 2: Query Logs (PowerShell REST API)

```powershell
$subscriptionId = $env:AZURE_SUBSCRIPTION_ID
$workspaceId    = $env:AZURE_LOG_ANALYTICS_WORKSPACE_ID
$serviceName    = "my-service"
$startTime      = "2026-04-17 00:00:00"
$duration       = "10h"   # e.g. 10m, 1h, 6h, 1d

$token   = (az account get-access-token --resource https://api.loganalytics.io --subscription $subscriptionId | ConvertFrom-Json).accessToken
$headers = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }
$query   = "queryForServiceFromTsWithRange(`"$serviceName`", datetime(`"$startTime`"), $duration)"
$body    = @{ query = $query } | ConvertTo-Json

$response = Invoke-RestMethod `
    -Uri "https://api.loganalytics.io/v1/workspaces/$workspaceId/query" `
    -Method POST -Headers $headers -Body $body -TimeoutSec 120

$table = $response.tables[0]
Write-Host "Rows: $($table.rows.Count)"
```

Columns returned: `TimeGenerated`, `level`, `thread_name`, `message`, `Environment`, `ServiceName`, `SpanId`, `TraceId`, `Version`, `logger_name`, `stack_trace`, `TenantId`, `Type`, `_ResourceId`

## Step 3: Standard Analysis Queries

### Log level distribution

```powershell
$q = 'queryForServiceFromTsWithRange("my-service", datetime("2026-04-17 00:00:00"), 10h) | summarize Count=count() by level | order by Count desc'
```

### Hourly breakdown

```powershell
$q = 'queryForServiceFromTsWithRange("my-service", datetime("2026-04-17 00:00:00"), 10h) | summarize Count=count() by bin(TimeGenerated, 1h) | order by TimeGenerated asc'
```

### Errors and warnings only

```powershell
$q = 'queryForServiceFromTsWithRange("my-service", datetime("2026-04-17 00:00:00"), 10h) | where level in ("ERROR", "WARN") | project TimeGenerated, level, message, stack_trace, logger_name | order by TimeGenerated asc'
```

### Keyword filter (in message or stack_trace)

```powershell
$keyword = "TimeoutException"
$q = "queryForServiceFromTsWithRange(`"my-service`", datetime(`"2026-04-17 00:00:00`"), 10h) | where message contains `"$keyword`" or stack_trace contains `"$keyword`" | project TimeGenerated, level, message, stack_trace, logger_name | order by TimeGenerated asc"
```

### Multiple keywords (OR)

```powershell
$q = 'queryForServiceFromTsWithRange("my-service", datetime("2026-04-17 00:00:00"), 10h) | where message contains "timeout" or message contains "503" | project TimeGenerated, level, message | order by TimeGenerated asc'
```

## Step 4: Reusable Helper Function

For repeated queries in one session, define this helper upfront:

```powershell
function Query-Logs {
    param(
        [ValidateSet("Prod","NonProd","Dev")]
        [string]$Stage = "Prod",

        [string]$Service = "my-service",
        [string]$StartTime = "2026-04-17 00:00:00",
        [string]$Duration = "1h",
        [string]$KqlSuffix = ""  # appended after the function call, e.g. '| where level == "ERROR"'
    )

    # Fill in your own per-stage values
    $stageMap = @{
        Prod    = @{ Sub = "<prod-subscription-id>";    WS = "<prod-workspace-id>" }
        NonProd = @{ Sub = "<nonprod-subscription-id>"; WS = "<nonprod-workspace-id>" }
        Dev     = @{ Sub = "<dev-subscription-id>";     WS = "<dev-workspace-id>" }
    }

    $sub  = $stageMap[$Stage].Sub
    $ws   = $stageMap[$Stage].WS
    $token = (az account get-access-token --resource https://api.loganalytics.io --subscription $sub | ConvertFrom-Json).accessToken
    $headers = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }
    $kql  = "queryForServiceFromTsWithRange(`"$Service`", datetime(`"$StartTime`"), $Duration) $KqlSuffix"
    $body = @{ query = $kql } | ConvertTo-Json

    return (Invoke-RestMethod -Uri "https://api.loganalytics.io/v1/workspaces/$ws/query" -Method POST -Headers $headers -Body $body -TimeoutSec 120).tables[0]
}

# Usage examples:
$t = Query-Logs -Stage Prod -Service "my-service" -StartTime "2026-04-17 00:00:00" -Duration "10h" -KqlSuffix '| where level == "ERROR" | project TimeGenerated, message, stack_trace'
$t.rows | ForEach-Object { Write-Host "[$($_[0])] $($_[1])" }
```

## Standard Summary Report

After fetching rows, present:
1. **Counts by level** — INFO / DEBUG / WARN / ERROR totals
2. **Hourly breakdown** — spot traffic spikes
3. **All ERRORs** — grouped by unique message pattern with first/last occurrence
4. **All WARNs** — same grouping
5. **Keyword hits** (if requested) — chronological with context

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Using `az monitor log-analytics query` | Always use REST API — CLI command hangs on large datasets |
| Querying 10h+ without filtering | Add `\| where level == "ERROR"` first; fetch full range only for summaries |
| Wrong tenant login | Run `az login --tenant $env:AZURE_TENANT_ID` before querying |
| Token expiry mid-session | Re-fetch token with `az account get-access-token` — tokens expire after ~1h |
| Duration too large → timeout | Break into hourly chunks or add level/keyword filter to reduce data |
