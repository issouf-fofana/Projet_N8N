from django.db import models


# Types connus avec leur label lisible
TYPES_CONNUS = {
    'price_updater':         'Price Updater',
    'linked_code':           'Linked Code',
    'product':               'Product',
    'product_suppliers':     'Product Suppliers',
    'supplier_pre_order':    'Pre-order',
    'delivery':              'Livraison (delivery)',
    'supplier':              'Fournisseur (supplier)',
    'department':            'Rayon (department)',
    'supplier_order':        'Commande fournisseur',
    'product_loyalty_options': 'Options fidélité',
}


class TypeFichierConfig(models.Model):
    """Configure quels types de fichiers journal sont affichés et comptabilisés."""
    type_key = models.CharField(max_length=100, unique=True, verbose_name="Type")
    label    = models.CharField(max_length=200, blank=True, verbose_name="Label")
    actif    = models.BooleanField(default=True, verbose_name="Actif (affiché et comptabilisé)")

    class Meta:
        ordering = ['type_key']
        verbose_name = "Configuration type fichier"

    def __str__(self):
        return f"{self.type_key} ({'actif' if self.actif else 'inactif'})"

    @classmethod
    def get_types_actifs(cls):
        """Retourne le set des type_key actifs. Initialise les entrées manquantes."""
        existing = {o.type_key: o for o in cls.objects.all()}
        for key, label in TYPES_CONNUS.items():
            if key not in existing:
                cls.objects.create(type_key=key, label=label, actif=True)
        return set(cls.objects.filter(actif=True).values_list('type_key', flat=True))


# Patterns d'erreurs ignorées par défaut (classées en avertissement)
PATTERNS_IGNORES_CONNUS = {
    # ── Avertissements techniques (ignorés par défaut) ────────────────
    'mutex':                  ('Import en parallèle — repris automatiquement',          True),
    'fichier ignoré':         ('Import en parallèle — repris automatiquement',          True),
    'existe déjà':            ('Déjà intégré — doublon ignoré',                         True),
    'import déjà en cours':   ('Import déjà en cours',                                  True),
    'already in progress':    ('Import déjà en cours (en anglais)',                      True),
    # ── Vraies erreurs métier (comptabilisées par défaut) ─────────────
    "n'est pas fourni par":   ('Article absent du catalogue fournisseur dans RPOS',     False),
    'non fournit par':        ('Article absent du catalogue fournisseur (variante)',     False),
    'non commandable':        ('Article bloqué — non commandable dans RPOS',             False),
    'article non commandable':('Article bloqué — non commandable dans RPOS',             False),
    'department matching':    ('Rayon/département introuvable dans RPOS',                False),
    'ce champ ne peut':       ('Données manquantes — champ obligatoire vide dans Asten', False),
    'erreur association':     ('Fournisseur associé à un autre magasin — config RPOS',   False),
    'magasin différent':      ('Fournisseur associé à un autre magasin — config RPOS',   False),
    "n'est pas fourni":       ('Article non fourni (variante générique)',                 False),
}


class ErreurIgnoreeConfig(models.Model):
    """
    Configure quels patterns de messages sont traités comme avertissements (ignorés)
    ou comme vraies erreurs (comptabilisés).
    """
    pattern  = models.CharField(max_length=200, unique=True, verbose_name="Pattern (texte recherché)")
    label    = models.CharField(max_length=300, blank=True, verbose_name="Description")
    ignorer  = models.BooleanField(default=True, verbose_name="Ignorer (traiter comme avertissement, pas comme erreur)")

    class Meta:
        ordering = ['pattern']
        verbose_name = "Configuration erreur ignorée"

    def __str__(self):
        return f"{self.pattern} ({'ignoré' if self.ignorer else 'erreur'})"

    @classmethod
    def get_patterns_ignores(cls):
        """Retourne la liste des patterns à ignorer. Initialise les entrées manquantes."""
        existing = {o.pattern: o for o in cls.objects.all()}
        for pattern, (label, ignorer) in PATTERNS_IGNORES_CONNUS.items():
            if pattern not in existing:
                cls.objects.create(pattern=pattern, label=label, ignorer=ignorer)
        return list(cls.objects.filter(ignorer=True).values_list('pattern', flat=True))

    _derniere_decouverte = None  # cache en mémoire — timestamp dernier scan

    @classmethod
    def decouvrir_nouveaux_patterns(cls, force=False):
        """
        Scanne la DB EntreeJournal pour trouver des messages d'erreur
        qui ne matchent aucun pattern existant, et les ajoute automatiquement.
        Retourne le nombre de nouveaux patterns ajoutés.
        """
        import re
        from collections import Counter
        from datetime import datetime as _dt

        # Ne relancer le scan que toutes les 6 heures max (sauf force=True)
        if not force and cls._derniere_decouverte is not None:
            if (_dt.now() - cls._derniere_decouverte).total_seconds() < 6 * 3600:
                return 0
        cls._derniere_decouverte = _dt.now()

        from .services import _parse_report

        patterns_connus = [o.pattern.lower() for o in cls.objects.all()]

        entries = EntreeJournal.objects.exclude(report='').exclude(report__isnull=True)

        # Extraire les messages qui ne matchent aucun pattern connu
        nouveaux = Counter()
        for e in entries:
            try:
                parsed = _parse_report(e.report)
            except Exception:
                continue
            for err in parsed.get('errors', []) + parsed.get('warnings', []):
                msg = err.get('message', '').lower().strip()
                if not msg:
                    continue
                # Vérifier si ce message matche un pattern existant
                if any(p in msg for p in patterns_connus):
                    continue
                # Extraire le texte depuis ErrorDetail si présent
                m = re.search(r"string='([^']+)'", msg)
                texte = m.group(1) if m else msg
                # Généraliser : enlever IDs numériques et noms d'articles
                generique = re.sub(r'\b\d{6,}\b', '', texte).strip()
                generique = re.sub(r'\s+', ' ', generique)[:100]
                if generique:
                    nouveaux[generique] += 1

        # Mots-clés qui indiquent un message générique (pas un nom d'article spécifique)
        PREFIXES_VALIDES = [
            'article ', 'ce champ', 'department', 'erreur', 'magasin',
            "n'est pas", 'non comm', 'non fourn', 'product matching',
            'import', 'mutex', 'fichier', 'existe', 'already',
            "l'article ne peut", 'supplier', 'fournisseur',
        ]

        nb_ajoutes = 0
        for texte, count in nouveaux.most_common(50):
            if len(texte) < 8:
                continue
            # Supprimer la partie après " - [" (données spécifiques à un article)
            texte_generique = re.sub(r'\s*-\s*\[.*$', '', texte).strip()
            # Supprimer aussi les structures dict Django {'key': [...]}
            texte_generique = re.sub(r"^\{'\w+':\s*\[errordetail\(string=\"", '', texte_generique).rstrip('"')
            if not texte_generique or len(texte_generique) < 8:
                continue
            # Ignorer si ne commence pas par un préfixe générique connu
            tl = texte_generique.lower()
            if not any(tl.startswith(p) or p in tl[:30] for p in PREFIXES_VALIDES):
                continue
            # Vérifier si déjà couvert
            if any(p in tl for p in patterns_connus):
                continue
            if any(tl in p for p in patterns_connus):
                continue
            _, created = cls.objects.get_or_create(
                pattern=texte_generique[:200],
                defaults={'label': f'Erreur détectée automatiquement ({count}x)', 'ignorer': False}
            )
            if created:
                nb_ajoutes += 1
                patterns_connus.append(tl)

        return nb_ajoutes


class FichierImporte(models.Model):
    """Suivi des fichiers CSV déjà importés."""
    nom_fichier  = models.CharField(max_length=255, unique=True)
    pos_id       = models.CharField(max_length=20)
    nb_lignes    = models.IntegerField(default=0)
    importe_le   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-importe_le']
        verbose_name = "Fichier importé"

    def __str__(self):
        return self.nom_fichier


class EntreeJournal(models.Model):
    """Une entrée du journal POS (import de données)."""

    STATUS_CHOICES = [
        (0, 'Erreur'),
        (1, 'En cours'),
        (2, 'Succès'),
        (3, 'Indéfini'),
    ]

    # Identifiant unique de l'entrée (UUID depuis le POS)
    entry_id     = models.CharField(max_length=36, unique=True, db_index=True)

    # Infos POS
    pos_id       = models.CharField(max_length=20, db_index=True)
    pos_name     = models.CharField(max_length=100)

    # Dates
    created_at   = models.DateTimeField(db_index=True)
    updated_at   = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField()

    # Type d'entrée
    entry_type_value       = models.IntegerField(default=30)
    entry_type_label       = models.CharField(max_length=100)
    entry_type_detail_type = models.CharField(max_length=100, db_index=True)
    entry_type_detail_text = models.CharField(max_length=200)

    # Statut
    status_value = models.IntegerField(choices=STATUS_CHOICES, default=2)
    status_label = models.CharField(max_length=50)

    # Utilisateur & rapport
    username     = models.CharField(max_length=100, blank=True)
    report       = models.TextField(blank=True)

    # Magasin
    shop_reference = models.CharField(max_length=20, db_index=True)
    shop_name      = models.CharField(max_length=200)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at', 'shop_reference', 'entry_type_detail_type']),
            models.Index(fields=['pos_id', 'created_at']),
        ]
        verbose_name = "Entrée journal"

    def __str__(self):
        return f"{self.pos_id} | {self.shop_reference} | {self.entry_type_detail_type} | {self.status_label}"
