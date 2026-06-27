"""
Service Text-to-SQL avec Gemini.
Reçoit une question en français, génère le SQL, l'exécute, reformule la réponse.
"""
import json
import re
from django.db import connection
from django.conf import settings

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
   Asten  : SELECT COUNT(*) FROM ecarts_ecartcommande WHERE statut='ouvert'   → 14 actuellement
   GPV    : SELECT COUNT(*) FROM ecarts_ecartgpv WHERE statut='ouvert'        → 14 actuellement
   Legend : SELECT COUNT(*) FROM ecarts_ecartlegend WHERE statut='ouvert'     → 50 actuellement
   Total commandes non intégrées toutes sources = 14+14+50 = 78

2. COMMANDES INTÉGRÉES:
   Asten  : COUNT(asten) - COUNT(ecarts ouverts) = 662-14 = 648
   GPV    : COUNT(gpv transmises) - COUNT(ecarts ouverts) = 6026-14 = 6012
   Legend : COUNT(legend exportee=TRUE) = 183

3. BONS DE RÉCEPTION:
   Non intégrés : SELECT COUNT(*) FROM br_brasten WHERE ic_integre=FALSE  → 5
   Intégrés     : SELECT COUNT(*) FROM br_brasten WHERE ic_integre=TRUE   → 14734
   Total        : 14739

4. FACTURES → utiliser UNIQUEMENT mv_factures_joined (jamais les tables brutes):
   Non intégrées : WHERE statut_effectif='non_integre'   → 0 actuellement
   Intégrées     : WHERE statut_effectif='integre'       → 55501
   À vide (qt=0) : WHERE statut_effectif='integre_vide'  → 578
   Total actif   : WHERE statut_effectif != 'ignore'     → 56079

5. TICKETS:
   En attente : SELECT COUNT(*) FROM tickets_ticket WHERE statut='en_attente'  → 1
   Résolus    : SELECT COUNT(*) FROM tickets_ticket WHERE statut='resolu'       → 3
   Total      : 4

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
"""


def _extract_json(text: str) -> dict:
    """Extrait le JSON de la réponse Gemini (qui peut contenir du markdown)."""
    text = re.sub(r'```(?:json)?\s*', '', text).strip()
    text = re.sub(r'```\s*$', '', text).strip()
    return json.loads(text)


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
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()


def _call_ia(system: str, prompt: str) -> str:
    """Route l'appel vers le fournisseur IA configuré (gemini ou nvidia)."""
    provider = getattr(settings, 'AI_PROVIDER', 'gemini')
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


def query_with_gemini(question: str) -> dict:
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
    """
    # Étape 1 : générer le SQL
    try:
        raw = _call_ia(SYSTEM_PROMPT, question)
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
            lignes = [list(row) for row in lignes]
            nombre = len(lignes)
    except Exception as e:
        return {
            'sql': sql,
            'explication': explication,
            'hypothese': hypothese,
            'erreur': f'Erreur SQL : {e}',
        }

    # Étape 3 : reformuler la réponse en français
    if lignes:
        data_preview = json.dumps(
            {'colonnes': colonnes, 'lignes': lignes[:20], 'total': nombre},
            ensure_ascii=False, default=str
        )
        reformulation_prompt = (
            f"Question posée: {question}\n\n"
            f"Résultat ({nombre} ligne(s)):\n{data_preview}\n\n"
            f"Réponds à la question en français, clairement et directement. "
            f"Si c'est une liste, résume les points clés. Ne mentionne pas le SQL."
        )
        try:
            reponse = _call_ia('', reformulation_prompt)
        except Exception:
            reponse = f"{nombre} résultat(s) trouvé(s)."
    else:
        reponse = "Aucun résultat trouvé pour cette question."

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
