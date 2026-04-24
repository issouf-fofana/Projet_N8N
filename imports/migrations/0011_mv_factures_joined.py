from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('imports', '0010_add_qlvu_facture_cyrus'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE MATERIALIZED VIEW IF NOT EXISTS mv_factures_joined AS
                SELECT
                    c.cle_facture, c.nfac, c.nsee, c.dfac_str, c.dfac_date, c.cidc,
                    SUM(c.pfth) as pfth_total,
                    COUNT(DISTINCT c.lart) as nb_articles,
                    COALESCE(MAX(a.quantite_totale), 0) as qt_asten,
                    MAX(a.valorisation_ttc) as valo_ttc,
                    CASE WHEN MAX(a.n_bon_livraison) IS NOT NULL THEN true ELSE false END as integree
                FROM imports_facturecyrusligne c
                LEFT JOIN imports_factureastenligne a
                    ON a.n_bon_livraison = c.cle_facture AND a.magasin = c.cidc
                WHERE c.nsee != '19' AND c.qlvu >= 0
                GROUP BY c.cle_facture, c.nfac, c.nsee, c.dfac_str, c.dfac_date, c.cidc;

                CREATE INDEX IF NOT EXISTS idx_mv_fj_cidc     ON mv_factures_joined(cidc);
                CREATE INDEX IF NOT EXISTS idx_mv_fj_integree ON mv_factures_joined(integree);
                CREATE INDEX IF NOT EXISTS idx_mv_fj_qt       ON mv_factures_joined(qt_asten);
                CREATE INDEX IF NOT EXISTS idx_mv_fj_date     ON mv_factures_joined(dfac_date);
            """,
            reverse_sql="DROP MATERIALIZED VIEW IF EXISTS mv_factures_joined;",
        ),
    ]
