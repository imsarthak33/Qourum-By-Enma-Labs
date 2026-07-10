-- Widen leaderboard_submissions.exchange to accept NASDAQ/NYSE alongside
-- NSE/BSE, now that Quorum can run a correct debate for US-listed stocks
-- (a proper US macro factor set, not NSE's, backs the Macro Oracle - see
-- quorum/data/adapters.py's MACRO_TICKERS_BY_EXCHANGE). Privacy contract is
-- unaffected: still a closed enum, not free text (see 0001's header).

alter table leaderboard_submissions
    drop constraint if exists leaderboard_submissions_exchange_check;

alter table leaderboard_submissions
    add constraint leaderboard_submissions_exchange_check
    check (exchange in ('NSE', 'BSE', 'NASDAQ', 'NYSE'));
