INSERT INTO documents (
    title, inventory_number, region, year, topic, description, archive_reference
)
VALUES
    (
        '[DEMO] Synthetic borehole record A-01',
        'DEMO-GEO-0001',
        'Учебный регион (синтетические данные)',
        2020,
        'Демонстрация; скважина; стратиграфия',
        'Полностью вымышленные метаданные и координаты для проверки прототипа. Не являются архивной записью.',
        'DEMO ONLY — не архивный шифр'
    ),
    (
        '[DEMO] Synthetic geological survey area B',
        'DEMO-GEO-0002',
        'Учебный регион (синтетические данные)',
        2023,
        'Демонстрация; геологическая съёмка; карта',
        'Полностью вымышленные метаданные и контур для проверки карты и поиска. Не являются архивной записью.',
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
        '[DEMO] Synthetic borehole A-01',
        'well',
        ST_SetSRID(ST_MakePoint(37.6176, 55.7558), 4326),
        '{"synthetic": true, "warning": "Demo-only coordinates; not a real borehole"}'::jsonb
    ),
    (
        '[DEMO] Synthetic survey area B',
        'area',
        ST_GeomFromText(
            'POLYGON((37.45 55.70, 37.52 55.70, 37.52 55.75, 37.45 55.75, 37.45 55.70))',
            4326
        ),
        '{"synthetic": true, "warning": "Demo-only polygon; not a real survey boundary"}'::jsonb
    )
ON CONFLICT (name) DO UPDATE SET
    kind = EXCLUDED.kind,
    geom = EXCLUDED.geom,
    metadata = EXCLUDED.metadata;

INSERT INTO feature_documents (feature_id, document_id)
SELECT f.id, d.id
FROM (VALUES
    ('[DEMO] Synthetic borehole A-01', 'DEMO-GEO-0001'),
    ('[DEMO] Synthetic survey area B', 'DEMO-GEO-0002'),
    ('[DEMO] Synthetic survey area B', 'DEMO-GEO-0001')
) AS links(feature_name, inventory_number)
JOIN features f ON f.name = links.feature_name
JOIN documents d ON d.inventory_number = links.inventory_number
ON CONFLICT DO NOTHING;
