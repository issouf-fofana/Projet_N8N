BEGIN;

-- 1. Vider les factures ASTEN et CYRUS
TRUNCATE TABLE
    public.imports_factureastenligne,
    public.imports_facturecyrusligne;

COMMIT;

-- 2. Reconstruire la vue matérialisée
REFRESH MATERIALIZED VIEW public.mv_factures_joined;

-- 3. Vérification
SELECT
    'ASTEN' AS source,
    COUNT(*) AS lignes
FROM public.imports_factureastenligne

UNION ALL

SELECT
    'CYRUS' AS source,
    COUNT(*) AS lignes
FROM public.imports_facturecyrusligne

UNION ALL

SELECT
    'MV_FACTURES_JOINED' AS source,
    COUNT(*) AS lignes
FROM public.mv_factures_joined;

