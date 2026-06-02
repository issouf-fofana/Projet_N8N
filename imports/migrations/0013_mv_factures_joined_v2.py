from django.db import migrations


class Migration(migrations.Migration):
    """
    Recrée mv_factures_joined avec les colonnes calculées (statut_effectif,
    has_ecart_valo, ecart_valo) directement en SQL, plus les statuts manuels
    via LEFT JOIN sur imports_factureecartstatut.

    Après cette migration, les vues Django n'ont plus besoin de charger toutes
    les lignes en mémoire — elles filtrent et paginent directement en SQL.
    """

    dependencies = [
        ('imports', '0012_idx_factureastenligne_date'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                -- Supprimer l'ancienne vue
                DROP MATERIALIZED VIEW IF EXISTS mv_factures_joined;

                -- Recréer avec colonnes calculées
                CREATE MATERIALIZED VIEW mv_factures_joined AS
                SELECT
                    c.cle_facture,
                    c.nfac,
                    c.nsee,
                    c.dfac_str,
                    c.dfac_date,
                    c.cidc,
                    SUM(c.pfth)                                          AS pfth_total,
                    COUNT(DISTINCT c.lart)                               AS nb_articles,
                    COALESCE(MAX(a.quantite_totale)::numeric, 0)         AS qt_asten,
                    MAX(a.valorisation_ttc)                              AS valo_ttc,
                    CASE WHEN MAX(a.n_bon_livraison) IS NOT NULL
                         THEN true ELSE false END                        AS integree,

                    -- statut manuel (ignore / integre) depuis la table de surcharge
                    MAX(es.statut)                                       AS statut_manuel,

                    -- colonnes calculées (statut_manuel prioritaire sur integre_vide)
                    CASE
                        WHEN MAX(es.statut) = 'integre'
                            THEN 'integre'
                        WHEN MAX(es.statut) = 'ignore'
                            THEN 'ignore'
                        WHEN MAX(a.n_bon_livraison) IS NOT NULL AND COALESCE(MAX(a.quantite_totale)::numeric, 0) > 0
                            THEN 'integre'
                        WHEN MAX(a.n_bon_livraison) IS NOT NULL
                            THEN 'integre_vide'
                        ELSE 'non_integre'
                    END                                                  AS statut_effectif,

                    -- écart de valorisation (intégrée + valo dispo + qt > 0)
                    CASE
                        WHEN MAX(a.n_bon_livraison) IS NOT NULL
                          AND MAX(a.valorisation_ttc) IS NOT NULL
                          AND COALESCE(MAX(a.quantite_totale)::numeric, 0) > 0
                        THEN ABS(ROUND(
                            COALESCE(MAX(a.valorisation_ttc)::numeric, 0)
                            - SUM(c.pfth)::numeric, 2))
                        ELSE 0
                    END                                                  AS ecart_valo,

                    CASE
                        WHEN MAX(a.n_bon_livraison) IS NOT NULL
                          AND MAX(a.valorisation_ttc) IS NOT NULL
                          AND COALESCE(MAX(a.quantite_totale)::numeric, 0) > 0
                          AND ABS(ROUND(
                              COALESCE(MAX(a.valorisation_ttc)::numeric, 0)
                              - SUM(c.pfth)::numeric, 2)) > 1
                        THEN true ELSE false
                    END                                                  AS has_ecart_valo

                FROM imports_facturecyrusligne c
                LEFT JOIN imports_factureastenligne a
                    ON a.n_bon_livraison = c.cle_facture AND a.magasin = c.cidc
                LEFT JOIN imports_factureecartstatut es
                    ON es.cle_facture = c.cle_facture
                   AND es.dfac_str   = c.dfac_str
                   AND es.cidc       = c.cidc
                WHERE c.nsee != '19' AND c.qlvu >= 0
                GROUP BY c.cle_facture, c.nfac, c.nsee, c.dfac_str, c.dfac_date, c.cidc;

                -- Index pour tous les filtres courants
                CREATE INDEX idx_mv_fj_cidc          ON mv_factures_joined(cidc);
                CREATE INDEX idx_mv_fj_integree       ON mv_factures_joined(integree);
                CREATE INDEX idx_mv_fj_qt             ON mv_factures_joined(qt_asten);
                CREATE INDEX idx_mv_fj_date           ON mv_factures_joined(dfac_date);
                CREATE INDEX idx_mv_fj_statut_eff     ON mv_factures_joined(statut_effectif);
                CREATE INDEX idx_mv_fj_has_ecart_valo ON mv_factures_joined(has_ecart_valo);
                CREATE INDEX idx_mv_fj_cidc_date      ON mv_factures_joined(cidc, dfac_date);
                CREATE INDEX idx_mv_fj_nsee           ON mv_factures_joined(nsee);
                CREATE INDEX idx_mv_fj_cle            ON mv_factures_joined(cle_facture);
            """,
            reverse_sql="DROP MATERIALIZED VIEW IF EXISTS mv_factures_joined;",
        ),
    ]
