"""
Service Text-to-SQL avec Gemini.
Reçoit une question en français, génère le SQL, l'exécute, reformule la réponse.
"""
import json
import re
from django.db import connection
from django.conf import settings

# Schéma de la base transmis à Gemini (tables métier uniquement)
DB_SCHEMA = """
PostgreSQL. Tables métier (FK réelles — PAS de suffixe _id sauf exceptions notées):

asten_commandeasten: id,date_commande,numero_commande,montant,statut,code_magasin(FK→core_magasin.code),date_validation,heure_validation,fournisseur
gpv_commandegpv: id,numero_commande,date_creation(timestamp),statut,code_magasin(FK→core_magasin.code)
legend_commandelegend: id,date_commande,numero_commande,depot_destination,depot_origine,exportee
br_brasten: id,numero_br,date_br,ic_integre(bool),statut_ic,en_anomalie,code_magasin(FK→core_magasin.code)
br_branomalie: id,numero_br,code_magasin,date_reception,fournisseur,montant_ht
cyrus_commandecyrus: id,date_commande,numero_commande,montant,statut,code_magasin,nom_magasin
core_magasin: code(PK,string ex:'110'),nom,full_asten
ecarts_ecartcommande: id,commande_asten_id(FK→asten_commandeasten.id),statut,commentaire,date_creation
ecarts_ecartgpv: id,commande_gpv_id(FK→gpv_commandegpv.id),statut,commentaire,date_creation
ecarts_ecartlegend: id,commande_legend_id(FK→legend_commandelegend.id),statut,type_ecart,date_creation
imports_importfichier: id,type_fichier,nom_fichier,date_import,nombre_lignes,nombre_nouveaux,statut
imports_facturesage: id,nom_fichier,date_depot,nombre_lignes
imports_facturebackupcyrus: id,nom_fichier,code_magasin,cle_facture,nfac,dfac_date,date_modif
imports_factureastenligne: id,n_bon_livraison,magasin,fournisseur,date_reception_date,valorisation_ht,type_reception
imports_facturecyrusligne: id,cle_facture,nfac,dfac_date,cidc(code_magasin),lart,nart,pvtc,qlvu
imports_versionastensnap: id,nom,date,statut,conformite_pct,nb_ok,nb_incomplet,nb_absent
entree_journal_entreejournal: id,pos_id,pos_name,shop_reference,shop_name,created_at,entry_type_value,entry_type_label,status_value(OK/ERROR/MISSING),status_label,report
tickets_ticket: id,numero_ticket,type_demande,statut,urgence,date_creation,magasin_id(FK→core_magasin.code),description
tickets_suiviticket: id,ticket_id,message,date_creation,auteur
tickets_ticketcategorie: id,nom

JOINTURES: core_magasin ON m.code = t.code_magasin (pour asten/gpv/br) ou m.code = t.magasin_id (pour tickets)
RÈGLES: gpv date_creation est TIMESTAMP→utiliser ::date. ic_integre: TRUE=intégré FALSE=non intégré. statut écarts: ouvert/resolu/en_cours
"""

SYSTEM_PROMPT = f"""Tu es un assistant expert SQL pour une application Django/PostgreSQL.
Tu analyses les données métier d'une chaîne de distribution (Prosuma) : commandes, factures, bons de réception, remontées magasins.

{DB_SCHEMA}

RÈGLES STRICTES:
1. Génère UNIQUEMENT du SQL SELECT (jamais INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE)
2. Limite toujours les résultats avec LIMIT (max 200 lignes)
3. Utilise des alias clairs (ex: COUNT(*) AS total)
4. Pour les dates du jour: CURRENT_DATE. Pour hier: CURRENT_DATE - 1
5. Pour filtrer par nom de magasin partiel: ILIKE '%nom%'
6. Joins avec core_magasin pour avoir le nom du magasin
7. Si la question est ambiguë, fais une hypothèse raisonnable et précise-la

RÉPONSE FORMAT JSON strict:
{{
  "sql": "SELECT ...",
  "explication": "Ce que la requête fait en une phrase",
  "hypothese": "Si tu as fait une hypothèse, indique-la ici (sinon null)"
}}
"""


def _extract_json(text: str) -> dict:
    """Extrait le JSON de la réponse Gemini (qui peut contenir du markdown)."""
    # Retirer les blocs ```json ... ```
    text = re.sub(r'```(?:json)?\s*', '', text).strip()
    text = re.sub(r'```\s*$', '', text).strip()
    return json.loads(text)


def _is_safe_sql(sql: str) -> bool:
    """Vérifie que le SQL ne contient que des SELECT."""
    clean = re.sub(r'--[^\n]*', '', sql)  # retirer commentaires
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
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    model_name = getattr(settings, 'GEMINI_MODEL', 'gemini-2.0-flash')

    if not api_key:
        return {'erreur': 'Clé API Gemini non configurée.'}

    # Étape 1 : générer le SQL
    try:
        raw = _call_gemini(api_key, model_name, SYSTEM_PROMPT, question)
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
            reponse = _call_gemini(api_key, model_name, '', reformulation_prompt)
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
