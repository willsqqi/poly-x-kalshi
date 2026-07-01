CREATE SCHEMA IF NOT EXISTS prediction_market;

SET search_path TO prediction_market;

CREATE TABLE IF NOT EXISTS etl_runs (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    source_uri TEXT,
    raw_snapshot_uri TEXT,
    processed_snapshot_uri TEXT,
    vertex_embedding_model TEXT,
    vertex_review_model TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS venue_events (
    event_id BIGSERIAL PRIMARY KEY,
    venue TEXT NOT NULL CHECK (venue IN ('polymarket', 'kalshi')),
    venue_event_id TEXT NOT NULL,
    event_ticker TEXT,
    slug TEXT,
    title TEXT NOT NULL,
    subtitle TEXT,
    category TEXT,
    series_ticker TEXT,
    product_metadata TEXT,
    event_status TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'expired', 'settled', 'archived')),
    opened_at TIMESTAMPTZ,
    close_time TIMESTAMPTZ,
    expiration_time TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expired_at TIMESTAMPTZ,
    last_seen_run_id TEXT REFERENCES etl_runs(run_id),
    fact_text_hash TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (venue, venue_event_id)
);

CREATE INDEX IF NOT EXISTS idx_venue_events_lifecycle ON venue_events (venue, lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_venue_events_last_seen ON venue_events (last_seen_at);
CREATE INDEX IF NOT EXISTS idx_venue_events_title ON venue_events USING gin (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(subtitle, '')));

CREATE TABLE IF NOT EXISTS venue_markets (
    market_id BIGSERIAL PRIMARY KEY,
    venue TEXT NOT NULL CHECK (venue IN ('polymarket', 'kalshi')),
    venue_market_id TEXT NOT NULL,
    venue_event_id BIGINT REFERENCES venue_events(event_id),
    ticker_or_slug TEXT,
    title TEXT NOT NULL,
    subtitle TEXT,
    category TEXT,
    market_type TEXT,
    market_status TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'expired', 'settled', 'archived')),
    close_time TIMESTAMPTZ,
    expiration_time TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expired_at TIMESTAMPTZ,
    last_seen_run_id TEXT REFERENCES etl_runs(run_id),
    rules_text TEXT,
    settlement_summary TEXT,
    liquidity_hint TEXT,
    fact_text_hash TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (venue, venue_market_id)
);

CREATE INDEX IF NOT EXISTS idx_venue_markets_event ON venue_markets (venue_event_id);
CREATE INDEX IF NOT EXISTS idx_venue_markets_lifecycle ON venue_markets (venue, lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_venue_markets_type ON venue_markets (market_type);
CREATE INDEX IF NOT EXISTS idx_venue_markets_title ON venue_markets USING gin (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(subtitle, '')));

CREATE TABLE IF NOT EXISTS market_outcomes (
    outcome_id BIGSERIAL PRIMARY KEY,
    market_id BIGINT NOT NULL REFERENCES venue_markets(market_id) ON DELETE CASCADE,
    outcome_key TEXT NOT NULL,
    outcome_label TEXT NOT NULL,
    side TEXT,
    token_id TEXT,
    no_token_id TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'expired', 'settled', 'archived')),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expired_at TIMESTAMPTZ,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (market_id, outcome_key)
);

CREATE INDEX IF NOT EXISTS idx_market_outcomes_market ON market_outcomes (market_id);
CREATE INDEX IF NOT EXISTS idx_market_outcomes_label ON market_outcomes (outcome_label);

CREATE TABLE IF NOT EXISTS embeddings (
    embedding_id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('event', 'market', 'outcome')),
    venue TEXT NOT NULL CHECK (venue IN ('polymarket', 'kalshi')),
    entity_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    fact_text_hash TEXT NOT NULL,
    fact_text TEXT NOT NULL,
    embedding_vector REAL[],
    embedding_uri TEXT,
    run_id TEXT REFERENCES etl_runs(run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_type, venue, entity_key, provider, model, embedding_dim, fact_text_hash)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_entity ON embeddings (entity_type, venue, entity_key);
CREATE INDEX IF NOT EXISTS idx_embeddings_hash ON embeddings (fact_text_hash);

CREATE TABLE IF NOT EXISTS event_match_candidates (
    candidate_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES etl_runs(run_id),
    pm_event_id BIGINT NOT NULL REFERENCES venue_events(event_id),
    kalshi_event_id BIGINT NOT NULL REFERENCES venue_events(event_id),
    rank INTEGER,
    match_score NUMERIC(8, 4),
    suggestion_method TEXT NOT NULL,
    ai_recommendation TEXT,
    ai_confidence NUMERIC(6, 4),
    ai_reason TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'approved', 'rejected', 'needs_review')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, pm_event_id, kalshi_event_id)
);

CREATE INDEX IF NOT EXISTS idx_event_match_candidates_review ON event_match_candidates (review_status, rank);

CREATE TABLE IF NOT EXISTS approved_event_pairs (
    event_pair_id BIGSERIAL PRIMARY KEY,
    pm_event_id BIGINT NOT NULL REFERENCES venue_events(event_id),
    kalshi_event_id BIGINT NOT NULL REFERENCES venue_events(event_id),
    source_candidate_id BIGINT REFERENCES event_match_candidates(candidate_id),
    review_status TEXT NOT NULL DEFAULT 'approved'
        CHECK (review_status IN ('approved', 'rejected', 'needs_review')),
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'expired', 'settled', 'archived')),
    reviewer TEXT,
    reviewed_at TIMESTAMPTZ,
    expired_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pm_event_id, kalshi_event_id)
);

CREATE INDEX IF NOT EXISTS idx_approved_event_pairs_status ON approved_event_pairs (review_status, lifecycle_status);

CREATE TABLE IF NOT EXISTS market_match_candidates (
    candidate_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES etl_runs(run_id),
    event_pair_id BIGINT NOT NULL REFERENCES approved_event_pairs(event_pair_id),
    pm_outcome_id BIGINT NOT NULL REFERENCES market_outcomes(outcome_id),
    kalshi_outcome_id BIGINT NOT NULL REFERENCES market_outcomes(outcome_id),
    rank INTEGER,
    match_score NUMERIC(8, 4),
    suggestion_method TEXT NOT NULL,
    ai_recommendation TEXT,
    ai_confidence NUMERIC(6, 4),
    ai_reason TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'approved', 'rejected', 'needs_review')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, event_pair_id, pm_outcome_id, kalshi_outcome_id)
);

CREATE INDEX IF NOT EXISTS idx_market_match_candidates_review ON market_match_candidates (review_status, rank);
CREATE INDEX IF NOT EXISTS idx_market_match_candidates_event_pair ON market_match_candidates (event_pair_id);

CREATE TABLE IF NOT EXISTS approved_market_pairs (
    market_pair_id BIGSERIAL PRIMARY KEY,
    event_pair_id BIGINT NOT NULL REFERENCES approved_event_pairs(event_pair_id),
    pm_outcome_id BIGINT NOT NULL REFERENCES market_outcomes(outcome_id),
    kalshi_outcome_id BIGINT NOT NULL REFERENCES market_outcomes(outcome_id),
    source_candidate_id BIGINT REFERENCES market_match_candidates(candidate_id),
    review_status TEXT NOT NULL DEFAULT 'approved'
        CHECK (review_status IN ('approved', 'rejected', 'needs_review')),
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'expired', 'settled', 'archived')),
    reviewer TEXT,
    reviewed_at TIMESTAMPTZ,
    expired_at TIMESTAMPTZ,
    settlement_notes TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pm_outcome_id, kalshi_outcome_id)
);

CREATE INDEX IF NOT EXISTS idx_approved_market_pairs_status ON approved_market_pairs (review_status, lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_approved_market_pairs_event_pair ON approved_market_pairs (event_pair_id);

CREATE TABLE IF NOT EXISTS approval_actions (
    approval_action_id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('event_pair', 'market_pair')),
    entity_id BIGINT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('approved', 'rejected', 'needs_review', 'expired', 'reactivated')),
    reviewer TEXT,
    notes TEXT,
    previous_status TEXT,
    new_status TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_approval_actions_entity ON approval_actions (entity_type, entity_id, created_at DESC);

CREATE TABLE IF NOT EXISTS quote_snapshots (
    quote_snapshot_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES etl_runs(run_id),
    market_pair_id BIGINT REFERENCES approved_market_pairs(market_pair_id),
    venue TEXT NOT NULL CHECK (venue IN ('polymarket', 'kalshi')),
    market_id BIGINT REFERENCES venue_markets(market_id),
    outcome_id BIGINT REFERENCES market_outcomes(outcome_id),
    bid NUMERIC(12, 6),
    ask NUMERIC(12, 6),
    mid NUMERIC(12, 6),
    last_price NUMERIC(12, 6),
    volume NUMERIC(20, 6),
    open_interest NUMERIC(20, 6),
    orderbook JSONB,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quote_snapshots_pair_time ON quote_snapshots (market_pair_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_quote_snapshots_run ON quote_snapshots (run_id);

CREATE OR REPLACE VIEW active_approved_market_pairs AS
SELECT
    amp.market_pair_id,
    amp.event_pair_id,
    pm_event.venue_event_id AS polymarket_event_id,
    pm_market.venue_market_id AS polymarket_market_id,
    pm_market.ticker_or_slug AS polymarket_slug,
    pm_outcome.outcome_label AS polymarket_outcome,
    pm_outcome.token_id AS polymarket_yes_token_id,
    pm_outcome.no_token_id AS polymarket_no_token_id,
    ks_event.venue_event_id AS kalshi_event_id,
    ks_market.venue_market_id AS kalshi_ticker,
    ks_outcome.outcome_label AS kalshi_outcome,
    amp.settlement_notes
FROM approved_market_pairs amp
JOIN approved_event_pairs ep ON ep.event_pair_id = amp.event_pair_id
JOIN market_outcomes pm_outcome ON pm_outcome.outcome_id = amp.pm_outcome_id
JOIN venue_markets pm_market ON pm_market.market_id = pm_outcome.market_id
JOIN venue_events pm_event ON pm_event.event_id = pm_market.venue_event_id
JOIN market_outcomes ks_outcome ON ks_outcome.outcome_id = amp.kalshi_outcome_id
JOIN venue_markets ks_market ON ks_market.market_id = ks_outcome.market_id
JOIN venue_events ks_event ON ks_event.event_id = ks_market.venue_event_id
WHERE amp.review_status = 'approved'
  AND amp.lifecycle_status = 'active'
  AND ep.review_status = 'approved'
  AND ep.lifecycle_status = 'active'
  AND pm_market.lifecycle_status = 'active'
  AND ks_market.lifecycle_status = 'active'
  AND pm_outcome.lifecycle_status = 'active'
  AND ks_outcome.lifecycle_status = 'active';
