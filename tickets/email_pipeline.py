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
DEFAULT_PROMPT_ANALYSE_EMAIL = """Tu es un assistant qui aide à traiter les demandes de support des magasins Asten.

Analyse cet email et retourne UNIQUEMENT un objet JSON valide, sans markdown ni explication.

Email reçu :
De : {from_name} <{from_email}>
Sujet : {subject}
Corps : {body}

Liste des magasins Asten connus (code → nom) :
{magasins_list}

Catégories disponibles (choisir la plus proche) :
{categories_list}

Retourne ce JSON :
{{
  "magasin_code": "code à 3 chiffres du magasin (ex: '035') ou null si non identifiable",
  "magasin_nom": "nom du magasin trouvé ou null",
  "type": "incident" ou "demande",
  "urgence": "haute" ou "moyenne" ou "basse",
  "categorie": "nom exact d'une catégorie de la liste ci-dessus ou null",
  "resume": "résumé du problème en 1-2 phrases maximum",
  "demandeur": "nom de la personne qui envoie si présent dans l'email ou la signature, sinon null",
  "confiance": 0.0 à 1.0
}}

Règles :
- Cherche le code magasin (3 chiffres) dans l'objet, le corps ET la signature
- Cherche aussi le nom du magasin et fais le lien avec la liste ci-dessus
- Si l'email parle d'une panne/blocage/urgent → urgence haute
- Si c'est une demande de renseignement → urgence basse
- Choisis la catégorie la plus pertinente parmi la liste fournie
- Ne réponds qu'avec le JSON, rien d'autre"""

DEFAULT_PROMPT_ANALYSE_INTENTION = """Tu es un agent ITSM. Analyse ce message de réponse d'un utilisateur concernant un ticket de support existant.

Ticket : {ticket_title}
Résumé ticket : {ticket_summary}
Réponse reçue de {from_email} :
{body}

Retourne UNIQUEMENT ce JSON :
{{
  "intent": "RESOLVED" | "STILL_PRESENT" | "NEW_INFO" | "QUESTION" | "REOPEN" | "UNKNOWN",
  "confidence": 0.0 à 1.0,
  "isAutoReply": true | false
}}

Règles :
- RESOLVED : l'utilisateur confirme que le problème est résolu
- STILL_PRESENT : le problème persiste
- NEW_INFO : l'utilisateur donne une information complémentaire
- QUESTION : l'utilisateur pose une question
- REOPEN : l'utilisateur signale que le problème est revenu
- isAutoReply : true si c'est un accusé de réception ou message d'absence automatique
- Ne réponds qu'avec le JSON"""


def get_prompt(key):
    """Charge le prompt depuis la DB si modifié, sinon retourne le défaut."""
    try:
        from tickets.models import ConfigPipeline
        cfg = ConfigPipeline.get()
        if key == "analyse_email":
            return cfg.prompt_analyse_email or DEFAULT_PROMPT_ANALYSE_EMAIL
        if key == "analyse_intention":
            return cfg.prompt_analyse_intention or DEFAULT_PROMPT_ANALYSE_INTENTION
    except Exception:
        pass
    return DEFAULT_PROMPT_ANALYSE_EMAIL


def strip_signature_and_thread(text):
    """
    Extrait uniquement la partie "nouvelle" d'un email :
    - Coupe avant le fil de réponse cité (De:, From:, Envoyé:, ---, >>>)
    - Coupe avant la signature (lignes vides + cordialités)
    Inspiré de IAHUB1 signatureStripper.js
    """
    if not text:
        return ""

    lines = text.splitlines()
    cutoff = len(lines)

    # Séparateurs de fil cité
    thread_markers = [
        r"^De\s*:",
        r"^From\s*:",
        r"^Envoyé\s*:",
        r"^Sent\s*:",
        r"^-{3,}",
        r"^_{3,}",
        r"^>+\s",
        r"^Le\s.+a écrit\s*:",
        r"^On\s.+wrote\s*:",
    ]
    # Séparateurs de signature
    sig_markers = [
        r"^cordialement",
        r"^bien à vous",
        r"^bien cordialement",
        r"^salutations",
        r"^regards",
        r"^best regards",
        r"^merci",
        r"^cdlt",
    ]

    for i, line in enumerate(lines):
        s = line.strip().lower()
        if not s:
            continue
        for pat in thread_markers:
            if re.match(pat, line.strip(), re.IGNORECASE):
                if i > 2:
                    cutoff = i
                    break
        else:
            for pat in sig_markers:
                if re.match(pat, s, re.IGNORECASE) and i > 3:
                    cutoff = i
                    break
            else:
                continue
        break

    result = "\n".join(lines[:cutoff]).strip()
    return result or text


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
    from tickets.models import ConfigPipeline
    cfg = ConfigPipeline.get()
    api_key = cfg.gemini_api_key or settings.GEMINI_API_KEY
    model = cfg.gemini_model or getattr(settings, "GEMINI_MODEL", "gemini-3.6-flash")
    if not api_key:
        raise ValueError("GEMINI_API_KEY non configurée")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 800},
    }
    import time
    data = {}
    for attempt in range(4):
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=payload,
            timeout=60,
        )
        if resp.status_code in (503, 429):
            time.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        data = resp.json()
        # "high demand" arrive parfois en 200 avec error dans le body
        err_msg = data.get("error", {}).get("message", "")
        if "high demand" in err_msg or "overloaded" in err_msg.lower() or "try again" in err_msg.lower():
            time.sleep(5 * (attempt + 1))
            continue
        break
    # Gemini peut bloquer la réponse (safety, quota) sans lever d'erreur HTTP
    candidates = data.get("candidates")
    if not candidates:
        feedback = data.get("promptFeedback", {})
        reason = feedback.get("blockReason") or data.get("error", {}).get("message") or str(data)
        raise ValueError(f"Gemini bloqué ou sans résultat : {reason}")
    candidate = candidates[0]
    # finishReason OTHER ou MAX_TOKENS → réponse tronquée
    finish = candidate.get("finishReason", "")
    parts = candidate.get("content", {}).get("parts", [])
    if not parts:
        raise ValueError(f"Gemini : réponse vide (finishReason={finish})")
    return parts[0]["text"]


def clean_email_body_with_ai(body):
    """
    Demande à Gemini de séparer le corps du mail en deux parties :
    - Le message principal (contenu utile)
    - La signature / coordonnées / disclaimer (à afficher en gris en bas)
    Retourne (corps_principal, signature, ia_a_traite: bool)
    """
    if not body or len(body.strip()) < 20:
        return body, "", False

    prompt = f"""Tu es un assistant qui analyse des emails professionnels.

Voici le corps brut d'un email :
---
{body[:2500]}
---

Ta tâche : séparer en deux blocs en retournant EXACTEMENT ce format JSON :
{{
  "message": "le texte du message principal ici",
  "signature": "la signature, coordonnées, disclaimer ici (ou vide si absent)"
}}

Règles :
- "message" : contient UNIQUEMENT le texte du message (salutation + contenu + formule de politesse)
- "signature" : contient le nom, poste, téléphone, email, site web, adresse, disclaimer légal
- Ne reformule RIEN, copie le texte exact
- Si pas de signature détectable, mets "" pour "signature"
- Les messages cités (fils de réponse) vont dans "signature"

Retourne UNIQUEMENT le JSON, sans explication."""

    try:
        raw = call_gemini(prompt).strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw).strip()
        import json
        data = json.loads(raw)
        message = (data.get("message") or "").strip()
        signature = (data.get("signature") or "").strip()
        # Vérification : si message vide ou bizarre, fallback
        if not message or len(message) < 5:
            return body, "", False
        return message, signature, True
    except Exception:
        # Fallback : nettoyage par règles
        corps_regle = strip_signature_and_thread(body)
        return corps_regle, "", False


def analyze_email_with_ai(from_name, from_email, subject, body):
    """
    Appelle Gemini pour extraire les infos du mail.
    Retourne un dict avec magasin_code, type, urgence, categorie, resume, demandeur, confiance.
    """
    from tickets.models import TicketCategorie
    magasins = Magasin.objects.all().values_list("code", "nom")
    magasins_list = "\n".join(f"{code} → {nom}" for code, nom in magasins[:50])
    categories = list(TicketCategorie.objects.values_list("nom", flat=True))
    categories_list = "\n".join(f"- {c}" for c in categories) or "(aucune catégorie)"

    # Limite uniquement pour le prompt IA (éviter dépassement tokens Gemini)
    clean_body = strip_signature_and_thread(body)[:800]

    prompt = get_prompt("analyse_email").format(
        from_name=from_name,
        from_email=from_email,
        subject=subject,
        body=clean_body,
        magasins_list=magasins_list or "(aucun magasin en base)",
        categories_list=categories_list,
    )

    raw = call_gemini(prompt)

    # Retirer les blocs markdown si présents
    clean = re.sub(r"```json\s*", "", raw)
    clean = re.sub(r"```\s*", "", clean).strip()

    # Extraire champ par champ directement — fonctionne même si JSON tronqué
    KEYS = ["magasin_code", "magasin_nom", "type", "urgence", "categorie",
            "resume", "demandeur", "confiance", "intent", "confidence", "isAutoReply"]
    result = {}
    for key in KEYS:
        m = re.search(
            rf'"{key}"\s*:\s*("(?:[^"\\]|\\.)*"|null|true|false|-?\d+(?:\.\d+)?)',
            clean
        )
        if m:
            try:
                result[key] = json.loads(m.group(1))
            except Exception:
                result[key] = m.group(1).strip('"')

    # Si aucun champ trouvé → vraiment invalide
    if not result:
        raise ValueError(f"Réponse IA invalide : {raw[:200]}")
    return result


def find_magasin(ai_result, from_email=None):
    """
    Cherche le magasin en base depuis le résultat IA, puis depuis la base de connaissance.
    Retourne l'objet Magasin ou None.
    """
    from tickets.models import EmailMagasinMapping
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

    # Fallback : base de connaissance email → magasin
    if from_email:
        mapping = EmailMagasinMapping.objects.filter(email__iexact=from_email).select_related('magasin').first()
        if mapping:
            logger.info(f"[EmailPipeline] Magasin trouvé via base connaissance : {from_email} → {mapping.magasin}")
            return mapping.magasin

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

    from tickets.models import TicketCategorie
    urgence = urgence_map.get(ai_result.get("urgence", "moyenne"), Ticket.NIVEAU_MOYEN)
    type_demande = type_map.get(ai_result.get("type", "incident"), Ticket.TYPE_INCIDENT)
    resume = ai_result.get("resume") or subject
    demandeur = ai_result.get("demandeur") or from_name or from_email
    confiance = ai_result.get("confiance", 0)

    # Résoudre la catégorie
    categorie = None
    categorie_nom = ai_result.get("categorie")
    if categorie_nom:
        categorie = TicketCategorie.objects.filter(nom__iexact=categorie_nom).first()
        if not categorie:
            categorie = TicketCategorie.objects.filter(nom__icontains=categorie_nom).first()

    statut = Ticket.STATUT_NOUVEAU if magasin else Ticket.STATUT_EN_ATTENTE

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
        categorie=categorie,
    )

    # Séparation message / signature par IA avant stockage
    corps_stocke, signature, ia_a_traite = clean_email_body_with_ai(body)

    note_ia = f"[IA] Confiance : {confiance:.0%}"
    if not ai_result.get("magasin_code"):
        note_ia += " | ⚠️ Magasin non identifié — vérification manuelle requise"

    tag_nettoyage = "[IA-nettoyé]" if ia_a_traite else "[brut]"
    bloc_signature = f"\n[signature]\n{signature}" if signature else ""

    corps_complet = (
        f"De : {from_name} <{from_email}>\n"
        f"Objet : {subject}\n"
        f"{tag_nettoyage}\n\n"
        f"{corps_stocke}"
        f"{bloc_signature}\n\n"
        f"---\n{note_ia}"
    )

    suivi = SuiviTicket.objects.create(
        ticket=ticket,
        auteur="Système (email automatique)",
        message=f"[Email entrant]\n{corps_complet}",
    )

    return ticket, suivi


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
