import os
from django.db import models
from django.utils import timezone
from core.models import Magasin


class CompteEmail(models.Model):
    """Compte Outlook 365 connecté via OAuth2 Microsoft Graph."""
    label = models.CharField(max_length=150, default="Boîte support")
    client_id = models.CharField(max_length=200, blank=True)
    client_secret = models.CharField(max_length=500, blank=True)
    tenant_id = models.CharField(max_length=200, blank=True)
    refresh_token = models.TextField(blank=True)
    delta_link = models.TextField(blank=True)
    sync_depuis = models.DateField(null=True, blank=True, help_text="Ne traiter que les emails reçus après cette date")
    is_active = models.BooleanField(default=False)
    last_sync = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Compte email"
        verbose_name_plural = "Comptes email"

    def __str__(self):
        return self.label

    @property
    def est_connecte(self):
        return bool(self.refresh_token)


class ConfigPipeline(models.Model):
    """Configuration globale du pipeline IA + polling (singleton pk=1)."""
    # ── IA ──
    gemini_api_key = models.CharField(max_length=300, blank=True, verbose_name="Gemini API Key")
    gemini_model = models.CharField(max_length=100, blank=True, default="gemini-2.5-flash", verbose_name="Modèle Gemini")
    # ── Polling ──
    polling_actif = models.BooleanField(default=False, verbose_name="Polling automatique activé")
    intervalle_minutes = models.PositiveIntegerField(default=5, verbose_name="Intervalle (minutes)")
    # ── Mots-clés résolution ──
    mots_cles_resolution = models.TextField(
        blank=True,
        default="résolu,resolu,réglé,regle,c'est bon,ca marche,ça marche,ok merci,merci c'est,problème résolu,tout fonctionne,nickel,fixed,resolved,done,working now",
        verbose_name="Mots-clés résolution (séparés par virgule)",
    )
    # ── Prompts IA ──
    prompt_analyse_email = models.TextField(blank=True, verbose_name="Prompt analyse email")
    prompt_analyse_intention = models.TextField(blank=True, verbose_name="Prompt analyse intention (suivi)")
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuration pipeline"

    def __str__(self):
        return "Configuration pipeline"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Technicien(models.Model):
    nom = models.CharField(max_length=150, unique=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Technicien"
        verbose_name_plural = "Techniciens"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class TicketCategorie(models.Model):
    nom = models.CharField(max_length=100, unique=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Catégorie de ticket"
        verbose_name_plural = "Catégories de tickets"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Ticket(models.Model):
    TYPE_INCIDENT = "incident"
    TYPE_DEMANDE = "demande"
    TYPE_CHOICES = [
        (TYPE_INCIDENT, "Incident"),
        (TYPE_DEMANDE, "Demande"),
    ]

    STATUT_NOUVEAU = "nouveau"
    STATUT_EN_COURS = "en_cours"
    STATUT_EN_ATTENTE = "en_attente"
    STATUT_RESOLU = "resolu"
    STATUT_FERME = "ferme"
    STATUT_CHOICES = [
        (STATUT_NOUVEAU, "Nouveau"),
        (STATUT_EN_COURS, "En cours"),
        (STATUT_EN_ATTENTE, "En attente"),
        (STATUT_RESOLU, "Résolu"),
        (STATUT_FERME, "Fermé"),
    ]

    NIVEAU_TRES_BAS = "tres_basse"
    NIVEAU_BAS = "basse"
    NIVEAU_MOYEN = "moyenne"
    NIVEAU_HAUT = "haute"
    NIVEAU_CHOICES = [
        (NIVEAU_TRES_BAS, "Très basse"),
        (NIVEAU_BAS, "Basse"),
        (NIVEAU_MOYEN, "Moyenne"),
        (NIVEAU_HAUT, "Haute"),
    ]

    numero_ticket = models.CharField(max_length=30, unique=True, blank=True)
    type_demande = models.CharField(max_length=20, choices=TYPE_CHOICES)
    categorie = models.ForeignKey(
        TicketCategorie, on_delete=models.PROTECT, related_name="tickets", null=True, blank=True
    )
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default=STATUT_NOUVEAU)
    urgence = models.CharField(max_length=20, choices=NIVEAU_CHOICES)
    impact = models.CharField(max_length=20, choices=NIVEAU_CHOICES)
    magasin = models.ForeignKey(Magasin, on_delete=models.PROTECT, related_name="tickets")
    demandeur = models.CharField(max_length=150, blank=True, default="")
    observateurs = models.ManyToManyField(Technicien, blank=True, related_name="tickets_observes")
    assigne_a = models.ManyToManyField(Technicien, blank=True, related_name="tickets_assignes")
    description = models.TextField(blank=True, default="")
    date_creation = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)
    date_fermeture = models.DateTimeField(null=True, blank=True)

    # ── Traçabilité email Outlook ──
    source_email = models.EmailField(blank=True, default="")          # expéditeur du mail d'origine
    sujet_email = models.CharField(max_length=500, blank=True, default="")  # objet du mail d'origine
    outlook_conversation_id = models.CharField(max_length=500, blank=True, db_index=True)
    outlook_message_id = models.CharField(max_length=500, blank=True)  # id du dernier mail reçu (pour répondre dans le fil)
    cree_par_email = models.BooleanField(default=False)                # True = créé automatiquement via mail
    magasin_non_identifie = models.BooleanField(default=False)        # True = IA n'a pas trouvé le magasin

    class Meta:
        verbose_name = "Ticket"
        verbose_name_plural = "Tickets"
        ordering = ["-date_mise_a_jour"]

    def __str__(self):
        return self.numero_ticket or f"Ticket #{self.pk}"

    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating and not self.numero_ticket:
            # Générer un numéro séquentiel simple (1, 2, 3...)
            # Trouver le numéro maximum existant et ajouter 1
            tickets_existants = Ticket.objects.exclude(pk=self.pk).exclude(numero_ticket__isnull=True).exclude(numero_ticket='')
            max_numero = 0
            for ticket in tickets_existants:
                try:
                    # Essayer de convertir le numéro en entier
                    num = int(ticket.numero_ticket)
                    if num > max_numero:
                        max_numero = num
                except (ValueError, TypeError):
                    # Si le numéro n'est pas un nombre, ignorer
                    pass
            numero = str(max_numero + 1)
            Ticket.objects.filter(pk=self.pk).update(numero_ticket=numero)
            self.numero_ticket = numero

    def set_statut(self, nouveau_statut, utilisateur=""):
        ancien_statut = self.statut
        if ancien_statut == nouveau_statut:
            return
        self.statut = nouveau_statut
        if nouveau_statut in {self.STATUT_RESOLU, self.STATUT_FERME}:
            self.date_fermeture = timezone.now()
        else:
            self.date_fermeture = None
        self.save(update_fields=["statut", "date_fermeture", "date_mise_a_jour"])
        HistoriqueStatut.objects.create(
            ticket=self,
            ancien_statut=ancien_statut,
            nouveau_statut=nouveau_statut,
            utilisateur=utilisateur or "",
        )


class HistoriqueStatut(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="historiques_statut")
    ancien_statut = models.CharField(max_length=20, blank=True)
    nouveau_statut = models.CharField(max_length=20)
    utilisateur = models.CharField(max_length=150, blank=True, default="")
    date_changement = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Historique de statut"
        verbose_name_plural = "Historiques de statut"
        ordering = ["-date_changement"]

    def __str__(self):
        return f"{self.ticket} {self.ancien_statut} → {self.nouveau_statut}"


class SuiviTicket(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="suivis")
    auteur = models.CharField(max_length=150, blank=True, default="")
    message = models.TextField()
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Suivi de ticket"
        verbose_name_plural = "Suivis de tickets"
        ordering = ["-date_creation"]

    def __str__(self):
        return f"Suivi #{self.pk} - {self.ticket}"

    @staticmethod
    def _parse_email_thread(full_body):
        """
        Découpe un corps de mail en liste de messages (du plus récent au plus ancien).
        Chaque message = {from_, to, cc, sent, subject, body}
        """
        import re as _re

        # Normaliser les séparateurs : "  De :" après du texte → "\nDe :"
        text = _re.sub(r'(?<=\S)\s{2,}(De\s*:|From\s*:)', r'\n\1', full_body)

        # Trouver les positions des en-têtes de messages cités
        # Un bloc cité commence par "De :" suivi de "Envoyé :" ou "À :" dans les lignes suivantes
        BLOCK_START = _re.compile(
            r'\n\s*(De\s*:|From\s*:)\s*\S',
            _re.IGNORECASE
        )
        splits = [m.start() for m in BLOCK_START.finditer(text)]

        # Découper en parties
        parts = []
        if splits:
            parts.append(text[:splits[0]])
            for i, start in enumerate(splits):
                end = splits[i+1] if i+1 < len(splits) else len(text)
                parts.append(text[start:end])
        else:
            parts = [text]

        # Patterns pour extraire les champs header
        # Objet : peut avoir le corps collé sur la même ligne → on le sépare
        HEADERS = [
            ('from_',   _re.compile(r'^\s*(?:Email\s+re[çc]u\s+de|De|From)\s*:\s*(.+)', _re.IGNORECASE)),
            ('sent',    _re.compile(r'^\s*(?:Envoyé|Sent|Date)\s*:\s*(.+)', _re.IGNORECASE)),
            ('to',      _re.compile(r'^\s*(?:À|A|To)\s*:\s*(.+)', _re.IGNORECASE)),
            ('cc',      _re.compile(r'^\s*(?:Cc|CC)\s*:\s*(.+)', _re.IGNORECASE)),
            ('subject', _re.compile(r'^\s*(?:Sujet|Objet|Object|Subject)\s*:\s*(.+)', _re.IGNORECASE)),
            ('ia_tag',  _re.compile(r'^\s*(\[IA-nettoyé\]|\[brut\])\s*$', _re.IGNORECASE)),
            ('sig_sep', _re.compile(r'^\s*\[signature\]\s*$', _re.IGNORECASE)),
        ]
        # Noms des champs header pour détecter la fin des headers
        HEADER_KEYS = {'de', 'from', 'envoyé', 'sent', 'à', 'a', 'to', 'cc', 'objet', 'object', 'subject'}

        messages = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            lines = part.splitlines()
            msg = {'from_': '', 'sent': '', 'to': '', 'cc': '', 'subject': '', 'body': '', 'ia_tag': '', 'signature': ''}
            body_lines = []
            in_header = True
            i = 0
            while i < len(lines):
                line = lines[i]
                if in_header:
                    matched = False
                    for key, pat in HEADERS:
                        m = pat.match(line)
                        if m:
                            val = m.group(1).strip()
                            # Pour "Objet :", le corps peut être collé après le sujet
                            # → on vérifie si la ligne contient plus que le sujet
                            if key == 'subject':
                                # Chercher si après les headers il y a du texte collé
                                # On garde tout dans subject et on extrait le corps après
                                msg[key] = val
                            else:
                                msg[key] = val
                            matched = True
                            break
                    if not matched:
                        # Plus de header → reste = corps
                        in_header = False
                        body_lines = lines[i:]
                        break
                i += 1

            # Si on a un sujet avec du texte collé (ex: "Objet : RE: RESEAU LENT Bonjour...")
            # → séparer le vrai sujet du corps
            if msg['subject']:
                subj = msg['subject']
                # Mot de début de corps collé après le vrai sujet
                corps_match = _re.search(
                    r"\s+(Bonjour|Bonsoir|Hello|Cher|Dear|Monsieur|Madame|SVP|Merci|Il\s+s['’]agit|Veuillez|Suite\s+\xe0|Pour\s+info)",
                    subj, _re.IGNORECASE
                )
                if corps_match:
                    extra = subj[corps_match.start():]
                    msg['subject'] = subj[:corps_match.start()].strip()
                    body_lines = [extra.strip()] + body_lines

            # Séparer corps et signature si marqueur [signature] présent
            full_body_text = "\n".join(body_lines)
            sig_split = _re.split(r'\n\[signature\]\n', full_body_text, maxsplit=1, flags=_re.IGNORECASE)
            raw_body = sig_split[0].strip()
            raw_sig = sig_split[1].strip() if len(sig_split) > 1 else ''

            # Si pas de [signature] marqueur → tenter de séparer body/signature
            # en cherchant le début des coordonnées dans le body
            if not raw_sig and raw_body:
                SIG_START = _re.compile(
                    r'(?:Cordialement[^a-z]*)?(?:[\w\s/]+)?\n?'
                    r'((?:Tél|Tel|Portable|Mob|Fax|E-mail|www\.)\s*[:\.]\s*\(?\+?\d|www\.)',
                    _re.IGNORECASE
                )
                m_sig = SIG_START.search(raw_body)
                if not m_sig:
                    # Fallback : cherche un numéro de téléphone précédé de texte non-message
                    m_sig = _re.search(r'(\+?\(?\d{2,3}\)?\s*\d[\d\s\-\.]{7,})', raw_body)
                if m_sig and m_sig.start() > 15:
                    # Remonter jusqu'au début de la ligne de signature
                    cut = raw_body.rfind('\n', 0, m_sig.start())
                    cut = cut if cut > 15 else m_sig.start()
                    raw_sig = raw_body[cut:].strip()
                    raw_body = raw_body[:cut].strip()

            # Reformater signature collée sur une ligne
            SIG_FIELDS = r'((?:Tél|Tel|Portable|Mob|Fax|E-mail|Mail|www\.|http|Poste|Flotte)\s*[:\.]?\s*)'
            if raw_sig and '\n' not in raw_sig:
                raw_sig = _re.sub(SIG_FIELDS, r'\n\1', raw_sig, flags=_re.IGNORECASE).strip()

            # Reformater body collé sur une ligne (HTML mal converti)
            if raw_body and raw_body.count('\n') < 2 and len(raw_body) > 100:
                raw_body = _re.sub(SIG_FIELDS, r'\n\1', raw_body, flags=_re.IGNORECASE).strip()

            msg['body'] = raw_body
            msg['signature'] = raw_sig

            if msg['from_'] or (msg['body'] and len(msg['body']) > 5):
                messages.append(msg)

        return messages

    @property
    def parsed_email(self):
        """Parse le message email en liste de messages ordonnés + note IA."""
        import re as _re
        msg = self.message
        # Retirer le préfixe [Email entrant] etc.
        body = _re.sub(r'^\[Email[^\]]*\]\n?', '', msg)

        # Extraire la note [IA] à la fin (anciens suivis: \n\n[IA], nouveaux: \n---\n[IA])
        ia_match = _re.search(r'\n(?:---\n)?(\[IA\].+)$', body, _re.DOTALL)
        if ia_match:
            ia_note = ia_match.group(1).strip()
            body = body[:ia_match.start()].strip()
        else:
            ia_note = ""

        messages = self._parse_email_thread(body)
        return {"messages": messages, "ia_note": ia_note}


class EmailRecu(models.Model):
    """Trace chaque mail traité par le pipeline Outlook."""
    ACTION_CREE = "cree"
    ACTION_FOLLOWUP = "followup"
    ACTION_RESOLU = "resolu"
    ACTION_IGNORE = "ignore"
    ACTION_ERREUR = "erreur"
    ACTION_CHOICES = [
        (ACTION_CREE, "Remontée créée"),
        (ACTION_FOLLOWUP, "Suivi ajouté"),
        (ACTION_RESOLU, "Résolution détectée"),
        (ACTION_IGNORE, "Ignoré"),
        (ACTION_ERREUR, "Erreur"),
    ]

    compte = models.ForeignKey(CompteEmail, on_delete=models.SET_NULL, null=True, related_name="emails_recus")
    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, blank=True, related_name="emails_source")
    message_id = models.CharField(max_length=500, blank=True, db_index=True)
    conversation_id = models.CharField(max_length=500, blank=True)
    expediteur_email = models.EmailField(blank=True)
    expediteur_nom = models.CharField(max_length=200, blank=True)
    sujet = models.CharField(max_length=500, blank=True)
    extrait = models.TextField(blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, default=ACTION_IGNORE)
    erreur = models.TextField(blank=True)
    date_reception = models.DateTimeField()
    date_traitement = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Email reçu"
        verbose_name_plural = "Emails reçus"
        ordering = ["-date_reception"]

    def __str__(self):
        return f"{self.expediteur_email} — {self.sujet[:60]}"


def chemin_piece_jointe(instance, filename):
    return os.path.join("tickets", "suivis", timezone.now().strftime("%Y/%m/%d"), filename)


class PieceJointe(models.Model):
    TYPE_IMAGE = "image"
    TYPE_VIDEO = "video"
    TYPE_PDF = "pdf"
    TYPE_WORD = "word"
    TYPE_EXCEL = "excel"
    TYPE_AUTRE = "autre"
    TYPE_CHOICES = [
        (TYPE_IMAGE, "Image"),
        (TYPE_VIDEO, "Vidéo"),
        (TYPE_PDF, "PDF"),
        (TYPE_WORD, "Word"),
        (TYPE_EXCEL, "Excel"),
        (TYPE_AUTRE, "Autre"),
    ]

    suivi = models.ForeignKey(SuiviTicket, on_delete=models.CASCADE, related_name="pieces_jointes")
    fichier = models.FileField(upload_to=chemin_piece_jointe)
    type_fichier = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_AUTRE)
    date_upload = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Pièce jointe"
        verbose_name_plural = "Pièces jointes"
        ordering = ["-date_upload"]

    def __str__(self):
        return os.path.basename(self.fichier.name)

    def save(self, *args, **kwargs):
        if self.fichier and (not self.type_fichier or self.type_fichier == self.TYPE_AUTRE):
            self.type_fichier = self.deduire_type_fichier()
        super().save(*args, **kwargs)

    def deduire_type_fichier(self):
        extension = os.path.splitext(self.fichier.name)[1].lower()
        if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            return self.TYPE_IMAGE
        if extension in {".mp4", ".avi", ".mkv", ".mov", ".webm"}:
            return self.TYPE_VIDEO
        if extension == ".pdf":
            return self.TYPE_PDF
        if extension in {".doc", ".docx"}:
            return self.TYPE_WORD
        if extension in {".xls", ".xlsx", ".csv"}:
            return self.TYPE_EXCEL
        return self.TYPE_AUTRE

    @property
    def est_image(self):
        extension = os.path.splitext(self.fichier.name)[1].lower()
        return extension in {".jpg", ".jpeg", ".png", ".gif", ".webp"}



class EmailMagasinMapping(models.Model):
    """Base de connaissance : email expéditeur → magasin connu."""
    email = models.EmailField(unique=True, db_index=True)
    magasin = models.ForeignKey('core.Magasin', on_delete=models.CASCADE, related_name='email_mappings')
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Mapping email → magasin"
        ordering = ['email']

    def __str__(self):
        return f"{self.email} → {self.magasin}"
