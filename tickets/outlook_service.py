"""
Service Microsoft Graph pour le pipeline email → remontées.
Porté de IAHUB1/erp-backend/src/utils/graphClient.js
               + src/services/emailPoller.js
               + src/services/conversationMatcher.js
"""

import json
import os
import re
import time
import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from tickets.models import CompteEmail, Ticket, SuiviTicket, EmailRecu, PieceJointe
from tickets.email_pipeline import analyze_email_with_ai, find_magasin, create_remontee_from_email, strip_signature_and_thread, clean_email_body_with_ai

logger = logging.getLogger(__name__)

GRAPH_SCOPES = "offline_access User.Read Mail.Read Mail.ReadWrite Mail.Send"

# Mots-clés qui indiquent qu'une réponse = résolution (inspiré IAHUB1 intentAnalyzer)
RESOLUTION_KEYWORDS = [
    "résolu", "resolu", "réglé", "regle", "c'est bon", "ca marche", "ça marche",
    "ok merci", "merci c'est", "problème résolu", "probleme resolu",
    "tout fonctionne", "ca fonctionne", "ça fonctionne", "nickel",
    "fixed", "resolved", "done", "working now",
]


# ─────────────────────────────────────────────
# OAuth2 helpers
# ─────────────────────────────────────────────

def get_authorize_url(compte, redirect_uri):
    """Génère l'URL de connexion Microsoft OAuth2."""
    params = {
        "client_id": compte.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": GRAPH_SCOPES,
        "state": str(compte.pk),
    }
    base = f"https://login.microsoftonline.com/{compte.tenant_id}/oauth2/v2.0/authorize"
    qs = "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items())
    return f"{base}?{qs}"


def exchange_code_for_token(compte, code, redirect_uri):
    """Échange le code OAuth2 contre un refresh_token et le sauvegarde."""
    resp = requests.post(
        f"https://login.microsoftonline.com/{compte.tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": compte.client_id,
            "client_secret": compte.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "scope": GRAPH_SCOPES,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise ValueError(f"Erreur Microsoft : {data.get('error_description', data['error'])}")

    compte.refresh_token = data["refresh_token"]
    compte.is_active = True
    compte.save(update_fields=["refresh_token", "is_active"])
    return data["access_token"]


def get_access_token(compte):
    """Échange le refresh_token contre un access_token valide."""
    if not compte.refresh_token:
        raise ValueError(f"Compte '{compte.label}' non connecté (pas de refresh token)")

    resp = requests.post(
        f"https://login.microsoftonline.com/{compte.tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": compte.client_id,
            "client_secret": compte.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": compte.refresh_token,
            "scope": GRAPH_SCOPES,
        },
        timeout=15,
    )
    data = resp.json()
    if not resp.ok:
        raise ValueError(f"Refresh token expiré ou invalide : {data.get('error_description', data.get('error'))}")

    # Microsoft peut renouveler le refresh_token
    if data.get("refresh_token") and data["refresh_token"] != compte.refresh_token:
        compte.refresh_token = data["refresh_token"]
        compte.save(update_fields=["refresh_token"])

    return data["access_token"]


def graph_get(compte, path):
    """GET Microsoft Graph API."""
    token = get_access_token(compte)
    url = path if path.startswith("https://") else f"https://graph.microsoft.com/v1.0{path}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def graph_post(compte, path, payload):
    """POST Microsoft Graph API."""
    token = get_access_token(compte)
    url = path if path.startswith("https://") else f"https://graph.microsoft.com/v1.0{path}"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json() if resp.text else {}


# ─────────────────────────────────────────────
# Pièces jointes
# ─────────────────────────────────────────────

def download_attachments(compte, message_id, suivi):
    """
    Télécharge les pièces jointes d'un message Graph et les attache au suivi.
    Ignore les pièces jointes inline (images intégrées dans le HTML).
    """
    try:
        token = get_access_token(compte)
        url = f"https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if not resp.ok:
            return
        attachments = resp.json().get("value", [])
        for att in attachments:
            # Ignorer les pièces jointes inline (images dans le corps HTML)
            if att.get("isInline"):
                continue
            name = att.get("name", "fichier")
            content_bytes = att.get("contentBytes")
            if not content_bytes:
                continue
            import base64
            data = base64.b64decode(content_bytes)
            pj = PieceJointe(suivi=suivi)
            pj.fichier.save(name, ContentFile(data), save=True)
            logger.info(f"[Outlook] Pièce jointe sauvegardée : {name}")
    except Exception as e:
        logger.warning(f"[Outlook] Impossible de récupérer les pièces jointes : {e}")


# ─────────────────────────────────────────────
# Polling des nouveaux mails (Graph delta)
# ─────────────────────────────────────────────

MSG_SELECT = "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,conversationId,hasAttachments,internetMessageId,internetMessageHeaders"


def fetch_new_messages(compte):
    """
    Récupère les nouveaux messages via l'API delta Graph.
    Utilise compte.delta_link comme curseur. Si sync_depuis est défini et qu'il
    n'y a pas encore de delta_link, filtre les messages par date.
    """
    token = get_access_token(compte)

    if compte.delta_link:
        start_url = compte.delta_link
    elif compte.sync_depuis:
        date_iso = compte.sync_depuis.strftime("%Y-%m-%dT00:00:00Z")
        start_url = (
            f"https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
            f"?$select={MSG_SELECT}&$filter=receivedDateTime ge {date_iso}"
            f"&$orderby=receivedDateTime asc&$top=50"
        )
    else:
        start_url = f"https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$select={MSG_SELECT}"

    msgs = []
    url = start_url
    next_delta = compte.delta_link
    use_delta = bool(compte.delta_link) or not compte.sync_depuis

    while url:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        resp.raise_for_status()
        page = resp.json()

        for item in page.get("value", []):
            if not item.get("@removed"):
                msgs.append(item)

        if "@odata.nextLink" in page:
            url = page["@odata.nextLink"]
        elif use_delta:
            next_delta = page.get("@odata.deltaLink", next_delta)
            url = None
        else:
            url = None

    # Sauvegarder le curseur delta (seulement si on utilise le mode delta)
    if use_delta:
        compte.delta_link = next_delta or ""
    compte.last_sync = timezone.now()
    compte.save(update_fields=["delta_link", "last_sync"])

    return msgs


# ─────────────────────────────────────────────
# Correspondance mail → ticket existant
# ─────────────────────────────────────────────

def find_existing_ticket(conversation_id, in_reply_to=None):
    """
    Cherche un ticket existant par conversation_id Outlook.
    Logique portée de IAHUB1 conversationMatcher.js.
    """
    if conversation_id:
        try:
            ticket = Ticket.objects.filter(
                outlook_conversation_id=conversation_id,
                statut__in=[Ticket.STATUT_NOUVEAU, Ticket.STATUT_EN_COURS, Ticket.STATUT_EN_ATTENTE],
            ).order_by("-date_mise_a_jour").first()
            if ticket:
                return ticket
        except Exception:
            pass

    return None


# ─────────────────────────────────────────────
# Détection résolution par réponse
# ─────────────────────────────────────────────

def is_resolution_reply(body_text):
    """
    Vérifie si le corps du mail indique que le problème est résolu.
    Version légère sans appel IA (mots-clés suffisent pour les cas clairs).
    """
    text = (body_text or "").lower()
    # Retirer les anciennes parties du fil (>)
    lines = [l for l in text.splitlines() if not l.strip().startswith(">")]
    clean = " ".join(lines)
    return any(kw in clean for kw in RESOLUTION_KEYWORDS)


# ─────────────────────────────────────────────
# Extraction texte brut du corps Graph
# ─────────────────────────────────────────────

def html_to_text(content):
    """Convertit HTML Outlook en texte brut avec sauts de ligne corrects."""
    # Balises block → saut de ligne AVANT suppression des tags
    text = re.sub(r"</?(p|div|tr|li|h[1-6]|blockquote)[^>]*>", "\n", content, flags=re.IGNORECASE)
    # <br> → saut de ligne
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Supprimer tous les autres tags
    text = re.sub(r"<[^>]+>", "", text)
    # Décoder entités HTML courantes
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Nettoyer espaces multiples sur une même ligne (mais garder les \n)
    lines = [l.rstrip() for l in text.split("\n")]
    # Supprimer les lignes vides consécutives (max 1 vide entre paragraphes)
    result = []
    prev_blank = False
    for line in lines:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        result.append(line)
        prev_blank = is_blank
    return "\n".join(result).strip()


def extract_body_text(message):
    """
    Extrait le texte brut COMPLET depuis un message Graph (pour stockage et affichage).
    Pas de coupure — on veut tout le mail visible dans le ticket.
    """
    content = message.get("body", {}).get("content", "")
    content_type = message.get("body", {}).get("contentType", "text")
    if content_type.lower() == "html":
        return html_to_text(content)
    return (content or "").strip()


# ─────────────────────────────────────────────
# Traitement d'un message entrant
# ─────────────────────────────────────────────

def parse_received_at(received_at_str):
    """Parse la date reçue depuis Graph API."""
    from django.utils.dateparse import parse_datetime
    if not received_at_str:
        return timezone.now()
    try:
        dt = parse_datetime(received_at_str.replace("Z", "+00:00"))
        return dt or timezone.now()
    except Exception:
        return timezone.now()


def process_message(message, compte):
    """
    Traite un mail Graph :
    - S'il appartient à un fil existant → suivi + détection résolution
    - Sinon → analyse IA + création remontée
    Sauvegarde chaque mail dans EmailRecu pour traçabilité.
    """
    msg_id = message.get("id", "")
    conversation_id = message.get("conversationId", "")
    subject = message.get("subject", "(sans objet)")
    from_addr = message.get("from", {}).get("emailAddress", {})
    from_email = from_addr.get("address", "")
    from_name = from_addr.get("name", "")
    body_text_raw = extract_body_text(message)
    # body_text_raw = corps complet pour affichage
    # body_clean = version courte pour l'IA uniquement (ne PAS stocker)
    received_at = parse_received_at(message.get("receivedDateTime", ""))
    body_preview = message.get("bodyPreview", body_text_raw[:200])

    # ── Cas 1 : réponse dans un fil existant ──
    existing = find_existing_ticket(conversation_id)
    if existing:
        # Analyser l'intention avec l'IA
        try:
            ai = analyze_email_with_ai(from_name, from_email, subject, body_text_raw)
            urgence_ia = ai.get("urgence", "")
            resume_ia = ai.get("resume", "")
            info_ia = f"\n\n[IA] Résumé : {resume_ia}" if resume_ia else ""
        except Exception:
            info_ia = ""

        # Séparation message / signature par IA avant stockage
        corps_propre, signature, ia_nettoye = clean_email_body_with_ai(body_text_raw)
        tag_nettoyage = "[IA-nettoyé]" if ia_nettoye else "[brut]"
        bloc_sig = f"\n[signature]\n{signature}" if signature else ""

        if is_resolution_reply(body_text_raw):
            existing.set_statut(Ticket.STATUT_RESOLU, utilisateur=from_email)
            suivi = SuiviTicket.objects.create(
                ticket=existing,
                auteur=from_name or from_email,
                message=f"[Email] Résolution détectée automatiquement.\nDe : {from_name} <{from_email}>\nObjet : {subject}\n{tag_nettoyage}\n\n{corps_propre}{bloc_sig}",
            )
            if message.get("hasAttachments"):
                download_attachments(compte, msg_id, suivi)
            action = EmailRecu.ACTION_RESOLU
            logger.info(f"[Outlook] Ticket #{existing.numero_ticket} marqué RÉSOLU par réponse mail")
        else:
            note_ia_followup = f"\n\n---\n{info_ia.strip()}" if info_ia.strip() else ""
            suivi = SuiviTicket.objects.create(
                ticket=existing,
                auteur=from_name or from_email,
                message=f"[Email entrant]\nDe : {from_name} <{from_email}>\nObjet : {subject}\n{tag_nettoyage}\n\n{corps_propre}{bloc_sig}{note_ia_followup}",
            )
            if message.get("hasAttachments"):
                download_attachments(compte, msg_id, suivi)
            if existing.statut == Ticket.STATUT_NOUVEAU:
                existing.set_statut(Ticket.STATUT_EN_COURS, utilisateur="Système")
            action = EmailRecu.ACTION_FOLLOWUP

        Ticket.objects.filter(pk=existing.pk).update(outlook_message_id=msg_id)
        EmailRecu.objects.get_or_create(
            message_id=msg_id,
            defaults=dict(
                compte=compte,
                ticket=existing,
                conversation_id=conversation_id,
                expediteur_email=from_email,
                expediteur_nom=from_name,
                sujet=subject,
                extrait=body_preview,
                action=action,
                date_reception=received_at,
            ),
        )
        return {"action": "followup", "ticket": existing.numero_ticket}

    # ── Cas 2 : nouveau mail → création remontée ──
    ai_result = analyze_email_with_ai(from_name, from_email, subject, body_text_raw)
    magasin = find_magasin(ai_result, from_email=from_email)
    ticket, suivi_initial = create_remontee_from_email(from_name, from_email, subject, body_text_raw, ai_result, magasin)

    if message.get("hasAttachments"):
        download_attachments(compte, msg_id, suivi_initial)

    Ticket.objects.filter(pk=ticket.pk).update(
        outlook_conversation_id=conversation_id,
        outlook_message_id=msg_id,
        source_email=from_email,
        sujet_email=subject or "",
        cree_par_email=True,
        magasin_non_identifie=(magasin is None or not ai_result.get("magasin_code")),
    )

    EmailRecu.objects.get_or_create(
        message_id=msg_id,
        defaults=dict(
            compte=compte,
            ticket=ticket,
            conversation_id=conversation_id,
            expediteur_email=from_email,
            expediteur_nom=from_name,
            sujet=subject,
            extrait=body_preview,
            action=EmailRecu.ACTION_CREE,
            date_reception=received_at,
        ),
    )

    logger.info(
        f"[Outlook] Remontée #{ticket.numero_ticket} créée "
        f"(magasin: {magasin or 'NON IDENTIFIÉ'}, statut: {ticket.statut})"
    )
    return {"action": "created", "ticket": ticket.numero_ticket}


# ─────────────────────────────────────────────
# Point d'entrée principal : poll tous les comptes
# ─────────────────────────────────────────────

def poll_all_accounts():
    """
    Parcourt tous les comptes email actifs et connectés,
    traite les nouveaux messages.
    """
    comptes = CompteEmail.objects.filter(is_active=True).exclude(refresh_token="")
    stats = {"comptes": 0, "messages": 0, "crees": 0, "followups": 0, "erreurs": 0}

    for compte in comptes:
        stats["comptes"] += 1
        try:
            messages = fetch_new_messages(compte)
            logger.info(f"[Outlook] Compte '{compte.label}' : {len(messages)} nouveau(x) message(s)")

            for msg in messages:
                try:
                    result = process_message(msg, compte)
                    stats["messages"] += 1
                    time.sleep(1.5)  # éviter le throttling Gemini API
                    if result["action"] == "created":
                        stats["crees"] += 1
                    else:
                        stats["followups"] += 1
                except Exception as e:
                    stats["erreurs"] += 1
                    logger.error(f"[Outlook] Erreur traitement message : {e}")

        except Exception as e:
            stats["erreurs"] += 1
            logger.error(f"[Outlook] Erreur compte '{compte.label}' : {e}")

    return stats
