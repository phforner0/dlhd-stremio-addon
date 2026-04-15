Plan
Implement logging in 3 passes so signal appears fast without flooding Railway.
Shared Schema
Add one helper and use it everywhere.
Fields to standardize:
- event
- request_id
- route
- method
- status_code
- duration_ms
- channel_id
- event_id
- meta_id
- upstream_host
- upstream_path
- cache_name
- cache_key_class
- cache_status
- reason
- stream_kind
- provider
- user_config_hash
Redact:
- full proxy url
- full referer
- full config token
- raw query strings with tokens
Keep:
- host
- normalized path family
- ids
- hash/fingerprint if needed
New Settings
Add in app/settings.py:
- DLHD_LOG_LEVEL default INFO
- DLHD_LOG_FORMAT default text, optional json
- DLHD_LOG_REQUEST_START default 0
- DLHD_RESOURCE_LOG_ENABLED default 1
- DLHD_RESOURCE_LOG_RSS_MB_THRESHOLD default 350
- DLHD_RESOURCE_LOG_INTERVAL_SECONDS default 60
Shared Logging Helper
Add new module:
- app/logging_utils.py
Functions:
- configure_logging()
- log_event(logger, level, event, **fields)
- hash_url(url)
- proxy_url_fields(url) -> host/path stem only
- config_fingerprint(config)
- maybe request_id_from_request(request)
If you want no new file, put helper in app/main.py first, but a separate module is cleaner.
---
Pass 1
High-value stream/proxy logs first.
app/main.py
Add request-level logs:
- before each route starts:
  - event=request_start
- after each route returns:
  - event=request_end
Useful route-specific events:
- /manifest
  - manifest_served
  - fields: configured, catalog_count, config_hash
- /catalog
  - catalog_served
  - fields: catalog_id, search, skip, result_count, country_filter, stale_after_minutes
  - if zero results: catalog_empty
- /meta
  - meta_served
  - fields: kind, channel_id or event_id, config_hash
  - on miss: meta_not_found
- /stream
  - stream_request_start
  - stream_request_end
  - fields: kind, channel_id or event_id, stream_count, config_hash
  - if zero streams: stream_no_results
Add stream candidate logs:
- in _build_channel_streams()
  - when trying each player: channel_player_attempt
  - when manifest accepted: stream_candidate_selected
  - when manifest skipped: stream_candidate_rejected
- in _resolve_live_channel_stream() / _build_live_streams()
  - live_channel_attempt
  - live_channel_selected
  - live_channel_rejected
  - live_event_budget_exhausted
Reason codes:
- invalid_hls_manifest
- duplicate_manifest
- resolver_exception
- watch_fetch_failed
- no_manifests
- budget_exhausted
/proxy in app/main.py
This is the most important logging area.
Add:
- proxy_request_start
  - fields: filename, upstream_host, referer_host
- proxy_url_rejected
  - reasons:
    - invalid_scheme
    - invalid_host
    - host_not_allowlisted
    - private_ip_blocked
- proxy_redirect_allowed
  - fields: from_host, to_host, redirect_kind
- proxy_redirect_rejected
  - reasons:
    - redirect_host_blocked
    - redirect_private_ip
    - too_many_redirects
- proxy_playlist_cache_hit
- proxy_playlist_rewrite
- proxy_non_hls_passthrough
- proxy_binary_passthrough
- proxy_upstream_error
For current pain:
- when _valid_hls_stream() filters a source, log:
  - stream_candidate_rejected
  - reason=invalid_hls_manifest
  - plus HLS subreason from validator
app/resolve/player.py
Add:
- resolve_start
- resolve_end
- resolve_timeout
- resolve_no_manifest
- playwright_browser_started
- playwright_browser_recycled
- playwright_context_created
- playwright_context_failed
- iframe_external_followed
Fields:
- label
- player_host
- reused_browser
- manifest_count
- iframe_count
- duration_ms
Critical HLS validation logs
Inside _probe_hls_target() and _valid_hls_stream() add:
- hls_validation_start
- hls_validation_pass
- hls_validation_fail
Reason codes:
- not_extm3u
- key_fetch_failed
- media_missing
- media_redirect_blocked
- media_http_403
- media_http_404
- media_html_instead_of_binary
- playlist_candidate_not_hls
- validation_exception
This is the log set that explains almost every “none available” report.
---
Pass 2
Cache, scrape, and upstream health.
app/cache.py
Add logs in:
- get()
  - cache_hit
  - cache_miss
  - cache_stale_hit
- set()
  - cache_set
- remember_stale()
  - cache_stale_served
  - cache_refresh_start
  - cache_refresh_end
  - cache_refresh_fail
- _evict_overflow_unlocked()
  - cache_evicted
- prune()
  - cache_pruned
Fields:
- cache_name
- cache_key_class
- ttl_seconds
- stale_ttl_seconds
- max_entries
- entry_count
You’ll need each cache instance to know its name. Best plan:
- extend TTLCache(..., name="channels")
app/scrape/channels.py
Add:
- scrape_channels_start
- scrape_channels_end
- scrape_channels_fail
Fields:
- url
- status_code
- channel_count
- duration_ms
app/scrape/schedule.py
Add:
- scrape_schedule_start
- scrape_schedule_end
- scrape_schedule_fail
- schedule_event_filtered_stale
  - count only, not per event unless debugging enabled
Fields:
- event_count
- duration_ms
- stale_after_minutes
app/scrape/watch.py
Add:
- watch_fetch_start
- watch_fetch_end
- watch_fetch_fail
Fields:
- channel_id
- status_code
- duration_ms
Upstream health aggregation
Not a full system, just lightweight counters in logs:
- when multiple hls_validation_fail or watch_fetch_fail happen for same host in short window, emit:
  - upstream_health_warning
  - fields: upstream_host, reason, count_in_window
This can be later if you want to avoid complexity.
---
Pass 3
Operational and memory logs.
app/main.py or new app/ops.py
Add periodic resource snapshot:
- resource_snapshot
Fields:
- rss_mb
- active_browsers
- pooled_http_sessions
- cache sizes:
  - channels_entries
  - schedule_entries
  - watch_entries
  - stream_entries
  - live_channel_entries
  - playlist_entries
  - manifest_validation_entries
Trigger:
- on startup
- on shutdown
- when RSS crosses threshold
- optionally every N seconds if DLHD_RESOURCE_LOG_ENABLED=1
Startup / shutdown
In lifespan:
- app_start
  - version
  - concurrency knobs
  - cache limits
  - retry config
  - proxy allowlist summary
  - TLS ignore policy
- app_shutdown
  - open browser count
  - session count
  - cache counts
---
Workflow Logs
.github/workflows/smoke.yml
Improve workflow output:
- print selected smoke channel id
- print candidate ids tried
- when none found, print all candidates checked
- if deployed smoke fails, print which endpoint failed
Not app logs, but very useful for ops.
---
Exact File Plan
1. app/settings.py
- add logging env vars
2. app/logging_utils.py
- add structured logging helper
3. app/main.py
- request logs, stream/proxy logs, startup/shutdown logs
4. app/resolve/player.py
- resolver + HLS validation reason logs
5. app/cache.py
- cache event logs
6. app/scrape/channels.py
- scrape start/end/fail logs
7. app/scrape/schedule.py
- scrape + stale filtering logs
8. app/scrape/watch.py
- fetch start/end/fail logs
9. .github/workflows/smoke.yml
- improve workflow diagnostics
10. README.md
- document:
  - DLHD_LOG_LEVEL
  - DLHD_LOG_FORMAT
  - how to interpret top log events
---
Implementation Order
1. app/logging_utils.py
2. app/main.py stream + proxy logs
3. app/resolve/player.py HLS validation reason logs
4. app/cache.py logs
5. app/scrape/* logs
6. resource/startup logs
7. smoke workflow output
8. docs
---