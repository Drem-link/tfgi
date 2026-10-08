BEGIN;

ALTER TABLE features ALTER COLUMN geom DROP NOT NULL;
ALTER TABLE features ADD COLUMN IF NOT EXISTS source_geom geometry(Geometry);
ALTER TABLE features ADD COLUMN IF NOT EXISTS source_crs TEXT NOT NULL DEFAULT 'EPSG:4326';
UPDATE features SET source_geom = geom WHERE source_geom IS NULL AND geom IS NOT NULL;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS document_type TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS authors TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS coauthors TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS executor_org TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS work_year_start INTEGER;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS work_year_end INTEGER;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS created_place TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS minerals TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS archive_disk_number TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS material_composition TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS electronic_copy_status TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS efgi_id TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS efgi_url TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tgf_number TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS import_fingerprint CHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS documents_import_fingerprint_idx
    ON documents (import_fingerprint) WHERE import_fingerprint IS NOT NULL;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'features_source_crs_check' AND conrelid = 'features'::regclass
    ) THEN
        ALTER TABLE features ADD CONSTRAINT features_source_crs_check CHECK (
            source_crs IN ('EPSG:4326', 'EPSG:7683')
            AND (source_geom IS NULL OR ST_SRID(source_geom) =
                CASE source_crs WHEN 'EPSG:7683' THEN 7683 ELSE 4326 END)
            AND (source_crs <> 'EPSG:7683' OR geom IS NULL)
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'documents_work_years_check' AND conrelid = 'documents'::regclass
    ) THEN
        ALTER TABLE documents ADD CONSTRAINT documents_work_years_check CHECK (
            work_year_start IS NULL OR work_year_end IS NULL OR work_year_start <= work_year_end
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'documents_efgi_url_check' AND conrelid = 'documents'::regclass
    ) THEN
        ALTER TABLE documents ADD CONSTRAINT documents_efgi_url_check CHECK (
            efgi_url IS NULL OR efgi_url ~ '^https?://[^[:space:]]+$'
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS document_relations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    target_document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (
        relation_type IN ('related', 'appendix', 'protocol', 'map', 'copy_of')
    ),
    created_by BIGINT REFERENCES geocatalog_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_document_id <> target_document_id),
    UNIQUE (source_document_id, target_document_id, relation_type)
);
CREATE INDEX IF NOT EXISTS document_relations_target_idx ON document_relations (target_document_id);

CREATE TABLE IF NOT EXISTS saved_searches (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES geocatalog_users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    filters JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS catalog_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id BIGINT REFERENCES geocatalog_users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id BIGINT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS catalog_audit_created_idx ON catalog_audit (created_at DESC);

COMMIT;
