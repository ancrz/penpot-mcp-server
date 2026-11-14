-- Penpot MCP Server v0.5.0 — PostgreSQL pgvector + Multi-User AI Profile Bootstrap
-- Auto-executed on startup by PenpotDB.ensure_mcp_extensions_and_tables()

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS public.mcp_user_ai_profile (
    profile_id UUID PRIMARY KEY REFERENCES public.profile(id) ON DELETE CASCADE,
    email TEXT NOT NULL DEFAULT '',
    fullname TEXT NOT NULL DEFAULT '',
    active_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    credentials JSONB NOT NULL DEFAULT '{}'::jsonb,
    agent_instructions JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_file_id UUID,
    last_page_id UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS public.mcp_canvas_vision_index (
    file_id UUID NOT NULL,
    page_id UUID NOT NULL,
    shape_id UUID NOT NULL,
    shape_name TEXT NOT NULL DEFAULT '',
    shape_type TEXT NOT NULL DEFAULT 'rect',
    parent_id UUID,
    frame_name TEXT NOT NULL DEFAULT '',
    x DOUBLE PRECISION NOT NULL DEFAULT 0,
    y DOUBLE PRECISION NOT NULL DEFAULT 0,
    width DOUBLE PRECISION NOT NULL DEFAULT 0,
    height DOUBLE PRECISION NOT NULL DEFAULT 0,
    fill_color TEXT NOT NULL DEFAULT '',
    text_content TEXT NOT NULL DEFAULT '',
    semantic_summary TEXT NOT NULL DEFAULT '',
    embedding vector(64),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (file_id, page_id, shape_id)
);

CREATE INDEX IF NOT EXISTS mcp_canvas_vision_hnsw_idx
    ON public.mcp_canvas_vision_index USING hnsw (embedding vector_cosine_ops);
