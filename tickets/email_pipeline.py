"""
Pipeline email → remontée automatique.
Inspiré de IAHUB1/erp-backend/src/services/emailPipeline.js

Fonctionnement :
  1. Connexion IMAP à la boîte configurée
  2. Lecture des mails non lus
  3. Appel Gemini : extraction magasin, type, urgence, résumé
  4. Création d'une remontée (Ticket) avec statut selon résultat IA
  5. Marquage du mail comme lu
"""

import imaplib
import email
import json
import re
import logging
from email.header import decode_header
from email.utils import parseaddr

import requests
from django.conf import settings
from django.utils import timezone

from core.models import Magasin
from tickets.models import Ticket, SuiviTicket

logger = logging.getLogger(__name__)

# ── Prompt IA (inspiré de IAHUB1 promptTemplates.js → analyzeEmail) ──────────
PROMPT_TEMPLATE = """Tu es un assistant qui aide à traiter les demandes de support des magasins Asten.

Analyse cet email et retourne UNIQUEMENT un objet JSON valide, sans markdown ni explication.

Email reçu :
De : {from_name} <{from_email}>
Sujet : {subject}
Corps : {body}

Liste des magasins Asten connus (code → nom) :
{magasins_list}

Retourne ce JSON :
{{
  "magasin_code": "code à 3 chiffres du magasin (ex: '035') ou null si non identifiable",
  "magasin_nom": "nom du magasin trouvé ou null",
  "type": "incident" ou "demande",
  "urgence": "haute" ou "moyenne" ou "basse",
  "resume": "résumé du problème en 1-2 phrases maximum",
  "demandeur": "nom de la personne qui envoie si présent dans l'email ou la signature, sinon null",
  "confiance": 0.0 à 1.0
}}

Règles :
- Cherche le code magasin (3 chiffres) dans l'objet, le corps ET la signature
- Cherche aussi le nom du magasin et fais le lien avec la liste ci-dessus
- Si l'email parle d'une panne/blocage/urgent → urgence haute
- Si c'est une demande de renseignement → urgence basse
- Ne réponds qu'avec le JSON, rien d'autre"""


def decode_str(s):
    """Décode un header email encodé (RFC 2047)."""
    if not s:
        return ""
    parts = decode_header(s)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def extract_text_from_message(msg):
    """Extrait le texte brut d'un message email (préfère text/plain)."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in disp:
                charset = part.get_content_charset() or "utf-8"
                body += part.get_payload(decode=True).decode(charset, errors="replace")
    else:
        charset = msg.get_content_charset() or "utf-8"
        body = msg.get_payload(decode=True).decode(charset, errors="replace")
    return body[:3000]  # limite pour l'IA


def call_gemini(prompt):
    """Appelle l'API Gemini avec le prompt donné. Retourne le texte brut."""
    api_key = settings.GEMINI_API_KEY
    model = getattr(settings, "GEMINI_MODEL", "gemini-1.5-flash")
    if not api_key:
        raise ValueError("GEMINI_API_KEY non configurée")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024},
    }
    resp = requests.post(
        url,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def analyze_email_with_ai(from_name, from_email, subject, body):
    """
    Appelle Gemini pour extraire les infos du mail.
    Retourne un dict avec magasin_code, type, urgence, resume, demandeur, confiance.
    """
    # Construire la liste des magasins pour aider l'IA
    magasins = Magasin.objects.all().values_list("code", "nom")
    magasins_list = "\n".join(f"{code} → {nom}" for code, nom in magasins[:200])

    prompt = PROMPT_TEMPLATE.format(
        from_name=from_name,
        from_email=from_email,
        subject=subject,
        body=body,
        magasins_list=magasins_list or "(aucun magasin en base)",
    )

    raw = call_gemini(prompt)

    # Extraire le JSON de la réponse
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        raise ValueError(f"Réponse IA invalide : {raw[:200]}")

    result = json.loads(json_match.group(0))
    return result


def find_magasin(ai_result):
    """
    Cherche le magasin en base depuis le résultat IA.
    Retourne l'objet Magasin ou None.
    """
    code = ai_result.get("magasin_code")
    nom = ai_result.get("magasin_nom")

    if code:
        try:
            return Magasin.objects.get(code=code)
        except Magasin.DoesNotExist:
            pass

    if nom:
        try:
            return Magasin.objects.get(nom__icontains=nom)
        except (Magasin.DoesNotExist, Magasin.MultipleObjectsReturned):
            pass

    return None


def create_remontee_from_email(from_name, from_email, subject, body, ai_result, magasin):
    """
    Crée un Ticket (remontée) depuis les données extraites par l'IA.
    Statut 'nouveau' si magasin trouvé, 'en_attente' si non identifié.
    """
    urgence_map = {
        "haute": Ticket.NIVEAU_HAUT,
        "moyenne": Ticket.NIVEAU_MOYEN,
        "basse": Ticket.NIVEAU_BAS,
    }
    type_map = {
        "incident": Ticket.TYPE_INCIDENT,
        "demande": Ticket.TYPE_DEMANDE,
    }

    urgence = urgence_map.get(ai_result.get("urgence", "moyenne"), Ticket.NIVEAU_MOYEN)
    type_demande = type_map.get(ai_result.get("type", "incident"), Ticket.TYPE_INCIDENT)
    resume = ai_result.get("resume") or subject
    demandeur = ai_result.get("demandeur") or from_name or from_email
    confiance = ai_result.get("confiance", 0)

    statut = Ticket.STATUT_NOUVEAU if magasin else Ticket.STATUT_EN_ATTENTE

    # Magasin "INCONNU" si non identifié — on utilise un magasin placeholder ou le premier
    if not magasin:
        magasin = Magasin.objects.filter(code="000").first() or Magasin.objects.first()
        if not magasin:
            raise ValueError("Aucun magasin en base — impossible de créer la remontée")

    ticket = Ticket.objects.create(
        type_demande=type_demande,
        statut=statut,
        urgence=urgence,
        impact=urgence,
        magasin=magasin,
        demandeur=demandeur,
        description=resume,
    )

    # Suivi initial avec le corps du mail original
    note_ia = f"[IA] Confiance : {confiance:.0%}"
    if not ai_result.get("magasin_code"):
        note_ia += " | ⚠️ Magasin non identifié — vérification manuelle requise"

    corps_complet = (
        f"Email reçu de : {from_name} <{from_email}>\n"
        f"Sujet : {subject}\n\n"
        f"{body}\n\n"
        f"---\n{note_ia}"
    )

    SuiviTicket.objects.create(
        ticket=ticket,
        auteur="Système (email automatique)",
        message=corps_complet,
    )

    return ticket


def process_imap_inbox(host, port, username, password, use_ssl=True, folder="INBOX"):
    """
    Lit la boîte IMAP, traite les mails non lus, crée les remontées.
    Retourne un résumé {traites, crees, erreurs}.
    """
    stats = {"traites": 0, "crees": 0, "erreurs": 0, "details": []}

    try:
        if use_ssl:
            conn = imaplib.IMAP4_SSL(host, port)
        else:
            conn = imaplib.IMAP4(host, port)

        conn.login(username, password)
        conn.select(folder)

        # Chercher les mails non lus
        _, msg_ids = conn.search(None, "UNSEEN")
        ids = msg_ids[0].split()

        logger.info(f"[EmailPipeline] {len(ids)} mail(s) non lu(s) trouvé(s)")

        for msg_id in ids:
            stats["traites"] += 1
            try:
                _, data = conn.fetch(msg_id, "(RFC822)")
                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                # Extraction des champs
                subject = decode_str(msg.get("Subject", "(sans objet)"))
                from_raw = msg.get("From", "")
                from_name, from_email_addr = parseaddr(from_raw)
                from_name = decode_str(from_name)
                body = extract_text_from_message(msg)

                logger.info(f"[EmailPipeline] Traitement : {subject} | De : {from_email_addr}")

                # Analyse IA
                ai_result = analyze_email_with_ai(from_name, from_email_addr, subject, body)

                # Recherche magasin
                magasin = find_magasin(ai_result)

                # Création remontée
                ticket = create_remontee_from_email(
                    from_name, from_email_addr, subject, body, ai_result, magasin
                )

                # Marquer comme lu
                conn.store(msg_id, "+FLAGS", "\\Seen")

                stats["crees"] += 1
                stats["details"].append({
                    "ticket": ticket.numero_ticket,
                    "magasin": magasin.nom if magasin and ai_result.get("magasin_code") else "NON IDENTIFIÉ",
                    "statut": ticket.statut,
                })
                logger.info(f"[EmailPipeline] ✓ Remontée #{ticket.numero_ticket} créée (magasin: {magasin})")

            except Exception as e:
                stats["erreurs"] += 1
                logger.error(f"[EmailPipeline] Erreur mail {msg_id}: {e}")
                stats["details"].append({"erreur": str(e)})

        conn.logout()

    except Exception as e:
        logger.error(f"[EmailPipeline] Erreur connexion IMAP : {e}")
        stats["erreur_connexion"] = str(e)

    return stats
