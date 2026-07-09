-- Quorum leaderboard schema (03_DATABASE §4, §4a).
--
-- PRIVACY CONTRACT, ENFORCED BY SCHEMA: this table has no column capable of
-- holding free text or an identifier. No query text. No user_id. No IP.
-- Every text column is locked to an enumerated set or a strict pattern via
-- CHECK constraints — tests/test_leaderboard.py asserts this file never
-- grows a forbidden column.

create table if not exists leaderboard_submissions (
    id uuid primary key default gen_random_uuid(),
    symbol text not null
        check (symbol ~ '^[A-Z0-9.&-]{1,20}$'),
    exchange text not null
        check (exchange in ('NSE', 'BSE')),
    action text not null
        check (action in ('BUY', 'SELL', 'WAIT', 'AVOID', 'NO_CALL')),
    entry numeric(12,2),
    target numeric(12,2),
    stop numeric(12,2),
    p_bull_calibrated numeric(5,4)
        check (p_bull_calibrated between 0 and 1),
    expected_value numeric(12,4),
    edge numeric(6,4),
    agent_weights jsonb,
    -- Track A submits post-resolution (the CLI resolves locally first), so
    -- result is NOT NULL here — stricter than doc 03, revisit if a
    -- server-side outcome worker is added.
    result text not null
        check (result in ('target_hit', 'stop_hit', 'expired_open', 'void')),
    resolved_at timestamptz,
    submitted_at timestamptz not null default now(),
    quorum_version text
        check (quorum_version ~ '^[0-9A-Za-z.+-]{1,32}$')
);

create index if not exists idx_submissions_created
    on leaderboard_submissions (submitted_at desc);
create index if not exists idx_submissions_symbol
    on leaderboard_submissions (symbol, submitted_at desc);

-- Append-only for the public: anon may insert (opt-in submissions) and read
-- (the whole point is public auditability); no update/delete policies exist,
-- so rows are immutable through the API.
alter table leaderboard_submissions enable row level security;

drop policy if exists submissions_insert_anon on leaderboard_submissions;
create policy submissions_insert_anon
    on leaderboard_submissions for insert
    to anon, authenticated
    with check (true);

drop policy if exists submissions_select_anon on leaderboard_submissions;
create policy submissions_select_anon
    on leaderboard_submissions for select
    to anon, authenticated
    using (true);

-- Public leaderboard view (03 §4a): sourced directly from submissions —
-- there are no debates/verdicts/users tables here at all, by design.
create or replace view public_calls
    with (security_invoker = on) as
select
    id as submission_id,
    symbol, exchange,
    action, entry, target, stop,
    p_bull_calibrated, expected_value, edge, agent_weights,
    result,
    (result = 'target_hit') as correct,
    (resolved_at is not null) as resolved,
    submitted_at as created_at
from leaderboard_submissions;

-- Rolling accuracy (readability metric, not the primary trust metric).
create or replace view leaderboard_stats
    with (security_invoker = on) as
select
    count(*) filter (where correct)                          as hits,
    count(*)                                                 as resolved,
    round(100.0 * count(*) filter (where correct)
          / nullif(count(*), 0), 1)                          as accuracy_pct
from public_calls
where created_at > now() - interval '90 days'
  and result in ('target_hit', 'stop_hit');

-- Calibration quality (07 §5): Brier + log-loss are what actually gate
-- credibility. Published together with accuracy, never alone.
create or replace view leaderboard_quality
    with (security_invoker = on) as
select
    round(avg(power(p_bull_calibrated - (correct::int), 2))::numeric, 4) as brier_score,
    round(avg(
        - (correct::int) * ln(greatest(p_bull_calibrated, 0.0001))
        - (1 - correct::int) * ln(greatest(1 - p_bull_calibrated, 0.0001))
    )::numeric, 4)                                                        as log_loss,
    count(*)                                                              as n_resolved
from public_calls
where result in ('target_hit', 'stop_hit')
  and p_bull_calibrated is not null
  and created_at > now() - interval '90 days';
