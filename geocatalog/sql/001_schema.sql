CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS features (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('well', 'area', 'site', 'other')),
    geom geometry(Geometry, 4326) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (name),
    CHECK (
        (kind = 'well' AND GeometryType(geom) = 'POINT') OR
        (kind = 'area' AND GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')) OR
        kind IN ('site', 'other')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT NOT NULL,
    inventory_number TEXT UNIQUE,
    region TEXT,
    year INTEGER CHECK (year IS NULL OR year BETWEEN 1500 AND 2200),
    topic TEXT,
    description TEXT,
    archive_reference TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector(
            'simple',
            coalesce(title, '') || ' ' ||
            coalesce(inventory_number, '') || ' ' ||
            coalesce(region, '') || ' ' ||
            coalesce(topic, '') || ' ' ||
            coalesce(description, '') || ' ' ||
            coalesce(archive_reference, '')
        ) ||
        to_tsvector(
            'russian',
            coalesce(title, '') || ' ' ||
            coalesce(topic, '') || ' ' ||
            coalesce(description, '')
        ) ||
        to_tsvector(
            'english',
            coalesce(title, '') || ' ' ||
            coalesce(topic, '') || ' ' ||
            coalesce(description, '')
        )
    ) STORED
);

CREATE TABLE IF NOT EXISTS feature_documents (
    feature_id BIGINT NOT NULL REFERENCES features(id) ON DELETE CASCADE,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    PRIMARY KEY (feature_id, document_id)
);

CREATE INDEX IF NOT EXISTS features_geom_gix ON features USING GIST (geom);
CREATE INDEX IF NOT EXISTS documents_search_gin ON documents USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS documents_region_year_idx ON documents (region, year);
CREATE INDEX IF NOT EXISTS feature_documents_document_idx ON feature_documents (document_id);
