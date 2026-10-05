BEGIN;

INSERT INTO documents (
    title, inventory_number, region, year, topic, description, archive_reference
)
VALUES
    (
        '[DEMO] Магадан — тестовая точка A',
        'DEMO-MAGADAN-0001',
        'Магаданская область (демо-данные)',
        2024,
        'Демонстрация; Магадан; геологическая карта',
        'Синтетическая запись для проверки глобуса. Координата приблизительно у Магадана; это не реальная скважина и не архивный документ.',
        'DEMO ONLY — не архивный шифр'
    ),
    (
        '[DEMO] Магаданская область — тестовая точка B',
        'DEMO-MAGADAN-0002',
        'Магаданская область (демо-данные)',
        2025,
        'Демонстрация; Магаданская область; геологические исследования',
        'Синтетическая запись для проверки масштабирования и карточки документа на глобусе. Координата не обозначает реальный объект.',
        'DEMO ONLY — не архивный шифр'
    )
ON CONFLICT (inventory_number) DO UPDATE SET
    title = EXCLUDED.title,
    region = EXCLUDED.region,
    year = EXCLUDED.year,
    topic = EXCLUDED.topic,
    description = EXCLUDED.description,
    archive_reference = EXCLUDED.archive_reference;

INSERT INTO features (name, kind, geom, metadata)
VALUES
    (
        '[DEMO] Магадан — тестовая точка A',
        'well',
        ST_SetSRID(ST_MakePoint(150.80, 59.56), 4326),
        '{"synthetic": true, "warning": "Demo-only location near Magadan; not a real borehole"}'::jsonb
    ),
    (
        '[DEMO] Магаданская область — тестовая точка B',
        'well',
        ST_SetSRID(ST_MakePoint(151.45, 60.00), 4326),
        '{"synthetic": true, "warning": "Demo-only location in Magadan Oblast; not a real borehole"}'::jsonb
    )
ON CONFLICT (name) DO UPDATE SET
    kind = EXCLUDED.kind,
    geom = EXCLUDED.geom,
    metadata = EXCLUDED.metadata;

INSERT INTO feature_documents (feature_id, document_id)
SELECT f.id, d.id
FROM (VALUES
    ('[DEMO] Магадан — тестовая точка A', 'DEMO-MAGADAN-0001'),
    ('[DEMO] Магаданская область — тестовая точка B', 'DEMO-MAGADAN-0002')
) AS links(feature_name, inventory_number)
JOIN features f ON f.name = links.feature_name
JOIN documents d ON d.inventory_number = links.inventory_number
ON CONFLICT DO NOTHING;

COMMIT;
