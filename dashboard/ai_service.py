"""
Service Text-to-SQL avec Gemini.
Reçoit une question en français, génère le SQL, l'exécute, reformule la réponse.
"""
import datetime
import decimal
import json
import re
from django.db import connection
from django.conf import settings


def _json_safe(value):
    """Convertit les types non sérialisables en JSON (date, datetime, Decimal) en str."""
    if isinstance(value, (datetime.date, datetime.datetime, decimal.Decimal)):
        return str(value)
    return value

DB_SCHEMA = """
PostgreSQL. Application de gestion d'une chaîne de distribution (Prosuma).

━━━ TABLES PRINCIPALES ━━━

core_magasin: code(PK string ex:'035','110','120'), nom, full_asten(bool), exclure_factures(bool)

── COMMANDES ──
asten_commandeasten: id, date_commande(date), numero_commande, montant, fournisseur,
  code_magasin(FK→core_magasin.code), date_validation, heure_validation
  ⚠ statut réel = 'en attente de livraison' uniquement — NE PAS utiliser statut pour "intégré"

gpv_commandegpv: id, numero_commande, date_creation(TIMESTAMP), code_magasin(FK→core_magasin.code)
  ⚠ statuts réels = 'TRANSMISE'/'SAISIE'/'VALIDEE' — NE PAS utiliser statut pour "intégré"
  ⚠ date_creation est TIMESTAMP → toujours utiliser ::date pour comparer avec des dates

legend_commandelegend: id, date_commande(date), numero_commande, depot_destination, depot_origine, exportee(bool)
  exportee: TRUE=intégrée, FALSE=non intégrée
  ⚠ PAS de colonne code_magasin ni magasin_id — depot_destination est un texte libre, pas lié à core_magasin

cyrus_commandecyrus: id, date_commande, numero_commande, montant, code_magasin, nom_magasin

── ÉCARTS COMMANDES ──
ecarts_ecartcommande: id, commande_asten_id(FK→asten_commandeasten.id), statut, commentaire, date_creation
  statut: 'ouvert'=non intégré / 'resolu'=intégré / 'en_cours'

ecarts_ecartgpv: id, commande_gpv_id(FK→gpv_commandegpv.id), statut, commentaire, date_creation
  statut: 'ouvert' / 'resolu' / 'en_cours'

ecarts_ecartlegend: id, commande_legend_id(FK→legend_commandelegend.id), statut, type_ecart, date_creation
  statut: 'ouvert' / 'resolu' / 'en_cours'

── BONS DE RÉCEPTION ──
br_brasten: id, numero_br, date_br(date), ic_integre(bool), statut_ic, en_anomalie(bool), code_magasin(FK→core_magasin.code)
  ic_integre: TRUE=intégré / FALSE=non intégré
  statut_ic: 'Intégré' / 'Non intégré' / 'Intégré (corrigé)'

br_branomalie: id, numero_br, code_magasin, date_reception, fournisseur, montant_ht

── FACTURES ──
mv_factures_joined (VUE MATÉRIALISÉE — SEULE SOURCE pour les stats factures):
  cle_facture, nfac, nsee, dfac_str, dfac_date(date), cidc(code_magasin),
  pfth_total(montant Cyrus HT), nb_articles, qt_asten, valo_ttc,
  integree(bool), statut_manuel, has_ecart_valo(bool), ecart_valo(numeric),
  statut_effectif: 'integre'/'integre_vide'/'non_integre'/'ignore'

imports_facturecyrusligne: id, cle_facture, nfac, dfac_date, cidc, lart, nart, pvtc, qlvu
imports_factureastenligne: id, n_bon_livraison, magasin, fournisseur, date_reception_date, valorisation_ht, quantite_totale
imports_facturesage: id, nom_fichier, date_depot, nombre_lignes
imports_facturebackupcyrus: id, nom_fichier, code_magasin, cle_facture, nfac, dfac_date, date_modif

── IMPORTS ──
imports_importfichier: id, type_fichier('asten'/'gpv'/'legend'/'cyrus'/'br_asten'/'br_ic'), nom_fichier, date_import(datetime), nombre_lignes, nombre_nouveaux, statut('termine'/'en_cours'/'erreur')

── VERSIONS ASTEN ──
imports_versionastensnap: id, nom, date(date), statut('ok'/'warning'/'error'), conformite_pct(int 0-100), nb_ok, nb_incomplet, nb_absent

── JOURNAL D'INTÉGRATION ──
entree_journal_entreejournal: id, pos_id, pos_name, shop_reference, shop_name, created_at(datetime),
  entry_type_value(30=import), entry_type_label,
  status_value(0=erreur / 1=en cours / 2=succès / 3=indéfini), status_label, report

── TICKETS ──
tickets_ticket: id, numero_ticket, type_demande('incident'/'demande'), statut('en_attente'/'resolu'),
  urgence('tres_basse'/'basse'/'moyenne'/'haute'), date_creation(datetime),
  magasin_id(FK→core_magasin.code), description
tickets_suiviticket: id, ticket_id(FK→tickets_ticket.id), message, date_creation, auteur

━━━ JOINTURES ━━━
core_magasin: JOIN ON m.code = t.code_magasin  (asten/gpv/br/cyrus/legend)
              JOIN ON m.code = t.magasin_id     (tickets)
              JOIN ON m.code = t.cidc           (factures mv_factures_joined)
              JOIN ON m.code = t.magasin        (imports_factureastenligne)

━━━ RÈGLES MÉTIER CRITIQUES ━━━

1. COMMANDES NON INTÉGRÉES → utiliser UNIQUEMENT les tables d'écarts (jamais le statut des commandes):
   Asten  : SELECT COUNT(*) FROM ecarts_ecartcommande WHERE statut='ouvert'
   GPV    : SELECT COUNT(*) FROM ecarts_ecartgpv WHERE statut='ouvert'
   Legend : SELECT COUNT(*) FROM ecarts_ecartlegend WHERE statut='ouvert'

2. COMMANDES INTÉGRÉES = total source - écarts ouverts (Asten/GPV) ou exportee=TRUE (Legend).

3. BONS DE RÉCEPTION:
   Non intégrés : SELECT COUNT(*) FROM br_brasten WHERE ic_integre=FALSE
   Intégrés     : SELECT COUNT(*) FROM br_brasten WHERE ic_integre=TRUE

4. FACTURES → utiliser UNIQUEMENT mv_factures_joined (jamais les tables brutes):
   Non intégrées : WHERE statut_effectif='non_integre'
   Intégrées     : WHERE statut_effectif='integre'
   À vide (qt=0) : WHERE statut_effectif='integre_vide'
   Total actif   : WHERE statut_effectif != 'ignore'

5. TICKETS:
   En attente : SELECT COUNT(*) FROM tickets_ticket WHERE statut='en_attente'
   Résolus    : SELECT COUNT(*) FROM tickets_ticket WHERE statut='resolu'

6. JOURNAL INTÉGRATION:
   Erreurs  : WHERE status_value=0
   Succès   : WHERE status_value=2
   En cours : WHERE status_value=1

7. RÈGLES SQL:
   - Toujours ajouter FROM (VALUES(1)) AS t quand SELECT contient uniquement des sous-requêtes scalaires
   - gpv_commandegpv.date_creation est TIMESTAMP → utiliser ::date pour filtrer par date
   - LIMIT max 200 lignes obligatoire
   - Utiliser des alias clairs (COUNT(*) AS total_non_integrees, etc.)
   - NE PAS utiliser imports_facturecyrusligne/imports_factureastenligne pour les stats d'intégration factures
   - Si plusieurs tables ont une colonne de même nom (ex: date_commande, date_creation), TOUJOURS préfixer
     par l'alias de table (ex: a.date_commande, g.date_creation) pour éviter "column reference is ambiguous"
   - Ne jamais inventer de colonne qui n'est pas listée explicitement dans le schéma ci-dessus
   - legend_commandelegend n'a PAS de code_magasin : ne pas la joindre à core_magasin par ce biais
   - "Depuis quand X n'a pas commandé / dernière commande de X" : calculer MAX(date_commande) par
     magasin et TRIER par cette date croissante (les plus anciennes en premier = ceux qui n'ont pas
     commandé depuis le plus longtemps). NE JAMAIS ajouter un filtre WHERE avec une date fixe
     inventée (ex: "depuis le 1er janvier 2022") — aucune date de seuil n'est donnée dans la question,
     n'en invente aucune. Exemple de requête correcte pour Asten :
     SELECT m.code, m.nom, MAX(a.date_commande) AS derniere_commande
     FROM core_magasin m LEFT JOIN asten_commandeasten a ON m.code = a.code_magasin
     GROUP BY m.code, m.nom ORDER BY derniere_commande ASC NULLS FIRST LIMIT 200;
   - Si la question demande un résultat "par source/à part" pour plusieurs sources (ex: "Legend à part,
     Asten à part, GPV à part" / "détail par source"), TOUJOURS faire UNE SEULE requête avec UNION ALL,
     une colonne "source" (libellé textuel) et la même colonne de mesure dans chaque branche — JAMAIS
     une requête scalaire unique qui mélange ou n'additionne qu'une seule source. Exemple :
     SELECT 'Legend' AS source, COUNT(*) AS total_non_integrees FROM ecarts_ecartlegend WHERE statut = 'ouvert'
     UNION ALL
     SELECT 'Asten' AS source, COUNT(*) AS total_non_integrees FROM ecarts_ecartcommande WHERE statut = 'ouvert'
     UNION ALL
     SELECT 'GPV' AS source, COUNT(*) AS total_non_integrees FROM ecarts_ecartgpv WHERE statut = 'ouvert';
"""

SYSTEM_PROMPT = f"""Tu es un assistant expert SQL pour une application Django/PostgreSQL.
Tu analyses les données métier d'une chaîne de distribution (Prosuma) : commandes, factures, bons de réception, tickets magasins.

{DB_SCHEMA}

RÉPONSE FORMAT JSON strict (rien d'autre):
{{
  "sql": "SELECT ...",
  "explication": "Ce que la requête fait en une phrase",
  "hypothese": "Si tu as fait une hypothèse, indique-la ici (sinon null)"
}}

IMPORTANT : la valeur de "sql" doit être UNE SEULE chaîne JSON sur une seule ligne logique
(remplace les retours à la ligne par des espaces). N'utilise JAMAIS la concatenation de chaînes
(pas de "a" + "b", pas de \\ en fin de ligne) à l'intérieur de la valeur JSON.
"""


def _extract_json(text: str) -> dict:
    """
    Extrait le JSON de la réponse du modèle IA.
    Gemini respecte le format JSON strict demandé. D'autres modèles (ex: Llama
    via NVIDIA NIM) répondent parfois en texte libre avec un bloc ```sql``` —
    on reconstruit alors un JSON équivalent à partir de ce texte.
    """
    # 1) Réponse JSON pure (rien d'autre autour) — cas Gemini standard
    cleaned = re.sub(r'```(?:json)?\s*', '', text).strip()
    cleaned = re.sub(r'```\s*$', '', cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Certains modèles (ex: Llama) échappent des caractères SQL invalides en JSON
    # (ex: \* pour COUNT(*)) — on retire les échappements non standard avant de re-essayer.
    repaired = re.sub(r'\\([*])', r'\1', cleaned)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # 2) Un bloc ```json ... ``` isolé au milieu d'un texte plus long
    #    (certains modèles, ex: llama via NVIDIA, ajoutent du texte explicatif
    #    avant/après et parfois AUSSI un bloc ```sql``` séparé — chercher le
    #    bloc json en premier évite de mélanger les deux).
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL | re.IGNORECASE)
    if json_match:
        block = json_match.group(1)
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass
        # Certains modèles (ex: Mixtral) concatènent des chaînes JS-style à
        # l'intérieur de la valeur ("a" + \n "b") au lieu d'une chaîne JSON valide.
        # On retire ces concatenations avant de re-essayer.
        repaired = re.sub(r'"\s*\+\s*\n?\s*"', '', block)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # 3) Fallback : réponse en texte libre avec un bloc ```sql ... ```
    sql_match = re.search(r'```sql\s*(.*?)```', text, re.DOTALL | re.IGNORECASE)
    if not sql_match:
        # Dernier recours : chercher un SELECT direct dans le texte
        sql_match = re.search(r'(SELECT\b.*?;)', text, re.DOTALL | re.IGNORECASE)
    if sql_match:
        sql = sql_match.group(1).strip()
        return {'sql': sql, 'explication': '', 'hypothese': None}

    # 4) Réponse coupée en plein milieu (max_tokens atteint) : récupérer le SELECT
    # même sans fermeture (pas de ```, pas de ;, pas de guillemet fermant).
    truncated_match = re.search(r'SELECT\b.*', text, re.DOTALL | re.IGNORECASE)
    if truncated_match:
        sql = truncated_match.group(0).strip().rstrip('"').rstrip('`').strip()
        raise ValueError(
            f"Réponse du modèle coupée avant la fin (requête incomplète) : {sql[:150]!r}..."
        )

    raise ValueError(f"Impossible d'extraire une requête SQL de la réponse : {text[:200]!r}")


def _is_safe_sql(sql: str) -> bool:
    """Vérifie que le SQL ne contient que des SELECT."""
    clean = re.sub(r'--[^\n]*', '', sql)
    clean = re.sub(r'/\*.*?\*/', '', clean, flags=re.DOTALL)
    keywords = re.findall(r'\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE)\b',
                          clean, re.IGNORECASE)
    return len(keywords) == 0


def _call_gemini(api_key: str, model_name: str, system: str, prompt: str) -> str:
    """Appelle Gemini avec le nouveau SDK google-genai."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    return response.text.strip()


def _call_nvidia(api_key: str, model_name: str, system: str, prompt: str) -> str:
    """Appelle un modèle NVIDIA NIM (API compatible OpenAI — build.nvidia.com)."""
    from openai import OpenAI
    # max_retries=0 : le SDK OpenAI retente automatiquement 2 fois par défaut en cas de
    # timeout, ce qui peut tripler le temps d'attente total (60s x 3 = 180s par appel).
    # On gère nous-même les erreurs. Le prompt RAG complet (schéma + exemples + historique)
    # fait ~10k caractères, ce qui peut dépasser 30s avec Mixtral — 60s donne de la marge.
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key, timeout=60.0, max_retries=0)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=0.2,
        max_tokens=2048,
    )
    content = response.choices[0].message.content
    finish_reason = response.choices[0].finish_reason
    if not content:
        raise RuntimeError(
            f"Réponse vide du modèle NVIDIA '{model_name}' (finish_reason={finish_reason}). "
            f"Vérifiez que le nom du modèle est correct sur build.nvidia.com."
        )
    return content.strip()


def _call_ia(system: str, prompt: str, provider: str = None) -> str:
    """Route l'appel vers le fournisseur IA (gemini ou nvidia).
    Si provider n'est pas précisé, utilise celui configuré dans Paramètres."""
    provider = provider or getattr(settings, 'AI_PROVIDER', 'gemini')
    if provider == 'nvidia':
        api_key = getattr(settings, 'NVIDIA_API_KEY', '')
        model_name = getattr(settings, 'NVIDIA_MODEL', 'meta/llama-3.1-70b-instruct')
        if not api_key:
            raise RuntimeError('Clé API NVIDIA non configurée.')
        return _call_nvidia(api_key, model_name, system, prompt)

    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    model_name = getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash-lite')
    if not api_key:
        raise RuntimeError('Clé API Gemini non configurée.')
    return _call_gemini(api_key, model_name, system, prompt)


def _construire_contexte_historique(historique: list) -> str:
    """
    Construit un résumé des derniers échanges (question + réponse en français,
    sans le SQL ni les données brutes) pour permettre à l'IA de résoudre les
    questions de suivi ("et pour Legend ?", "et la semaine dernière ?").
    `historique` : liste de dicts {'question': str, 'result': dict} dans l'ordre
    chronologique, déjà tronquée aux N derniers échanges par l'appelant.
    """
    if not historique:
        return ''
    blocs = []
    for turn in historique:
        q = turn.get('question', '')
        r = turn.get('result') or {}
        reponse = r.get('reponse') or r.get('erreur') or ''
        if not q or not reponse:
            continue
        blocs.append(f"Q: {q}\nR: {reponse}")
    if not blocs:
        return ''
    return (
        "\n\n━━━ HISTORIQUE DE LA CONVERSATION (contexte) ━━━\n"
        "Voici les échanges précédents de cette conversation — utilise-les pour "
        "comprendre les questions de suivi qui font référence au contexte précédent "
        "(ex: \"et pour X ?\", \"et la semaine dernière ?\"). La nouvelle question peut "
        "porter sur un sujet totalement différent, dans ce cas ignore l'historique :\n\n"
        + "\n\n".join(blocs)
    )


def query_with_gemini(question: str, provider: str = None, historique: list = None) -> dict:
    """
    Prend une question en français, retourne:
    {
      'sql': str,
      'explication': str,
      'hypothese': str|None,
      'colonnes': list,
      'lignes': list[list],
      'nombre': int,
      'reponse': str,
      'erreur': str|None
    }
    `provider` ('gemini' ou 'nvidia') permet de forcer un fournisseur pour cet
    appel précis, sans changer la configuration globale (Paramètres).
    `historique` : derniers échanges de la conversation (liste de {'question','result'}),
    injectés comme contexte pour les questions de suivi.
    """
    # Étape 1 : générer le SQL
    # Enrichir le prompt avec les exemples les plus pertinents de la base de
    # connaissance (RAG), trouvés par similarité sémantique avec la question,
    # et avec l'historique récent de la conversation pour les questions de suivi.
    try:
        from dashboard.ai_knowledge import construire_contexte_rag
        contexte_rag = construire_contexte_rag(question)
    except Exception:
        contexte_rag = ''
    contexte_historique = _construire_contexte_historique(historique or [])
    system_prompt = SYSTEM_PROMPT + contexte_historique + contexte_rag

    try:
        raw = _call_ia(system_prompt, question, provider)
        parsed = _extract_json(raw)
        sql = parsed.get('sql', '').strip()
        explication = parsed.get('explication', '')
        hypothese = parsed.get('hypothese')
    except Exception as e:
        return {'erreur': f'Erreur génération SQL : {e}'}

    if not sql:
        return {'erreur': "Gemini n'a pas pu générer de requête SQL pour cette question."}

    if not _is_safe_sql(sql):
        return {'erreur': 'Requête non autorisée (écriture refusée).'}

    # Étape 2 : exécuter le SQL
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            colonnes = [desc[0] for desc in cursor.description] if cursor.description else []
            lignes = cursor.fetchmany(200)
            lignes = [[_json_safe(v) for v in row] for row in lignes]
            nombre = len(lignes)
    except Exception as e:
        return {
            'sql': sql,
            'explication': explication,
            'hypothese': hypothese,
            'erreur': f'Erreur SQL : {e}',
        }

    # Étape 3 : reformuler la réponse en français
    # NVIDIA (Llama) est sensiblement plus lent que Gemini sur ce 2e appel — pour les
    # résultats simples (1 ligne / 1-2 colonnes), on formule directement sans appel IA
    # supplémentaire. Pour les résultats plus complexes, on repasse par l'IA.
    actual_provider = provider or getattr(settings, 'AI_PROVIDER', 'gemini')
    if not lignes:
        reponse = "Aucun résultat trouvé pour cette question."
    elif actual_provider == 'nvidia' and nombre == 1 and len(colonnes) <= 2:
        valeurs = ', '.join(f"{c} = {v}" for c, v in zip(colonnes, lignes[0]))
        reponse = valeurs
    else:
        data_preview = json.dumps(
            {'colonnes': colonnes, 'lignes': lignes[:20], 'total': nombre},
            ensure_ascii=False, default=str
        )
        reformulation_prompt = (
            f"Question posée: {question}\n\n"
            f"Résultat ({nombre} ligne(s)):\n{data_preview}\n\n"
            f"Réponds UNIQUEMENT en français (jamais en anglais), clairement et directement. "
            f"Base-toi UNIQUEMENT sur les colonnes et valeurs ci-dessus : une colonne 'source' "
            f"donne le nom de la source de données, une colonne commençant par 'total_' ou "
            f"'nombre_' est un nombre de lignes/commandes (PAS une notion mathématique comme "
            f"'entier/non-entier' — ignore le nom littéral de la colonne et déduis son sens du "
            f"contexte de la question). Si plusieurs lignes représentent des sources différentes, "
            f"donne le détail pour chacune. Ne mentionne pas le SQL."
        )
        try:
            reponse = _call_ia('', reformulation_prompt, provider)
        except Exception:
            reponse = f"{nombre} résultat(s) trouvé(s)."

    return {
        'sql': sql,
        'explication': explication,
        'hypothese': hypothese,
        'colonnes': colonnes,
        'lignes': lignes,
        'nombre': nombre,
        'reponse': reponse,
        'erreur': None,
    }
