CREATE TABLE IF NOT EXISTS youtube_videos (
    id              SERIAL PRIMARY KEY,
    channel_name    VARCHAR(100) NOT NULL,
    video_id        VARCHAR(20) NOT NULL UNIQUE,
    title           TEXT,
    published_at    TIMESTAMP,
    transcript_text TEXT,
    analysis_json   JSONB,
    relevance_score REAL DEFAULT 0,
    processed_at    TIMESTAMP DEFAULT NOW(),
    tokens_mentioned JSONB
);
CREATE INDEX IF NOT EXISTS idx_youtube_videos_published ON youtube_videos(published_at);
CREATE INDEX IF NOT EXISTS idx_youtube_videos_relevance ON youtube_videos(relevance_score);
CREATE INDEX IF NOT EXISTS idx_youtube_videos_video_id ON youtube_videos(video_id);
