"""
Service Microsoft Graph pour le pipeline email → remontées.
Porté de IAHUB1/erp-backend/src/utils/graphClient.js
               + src/services/emailPoller.js
               + src/services/conversationMatcher.js
"""

import json
import re
import logging
from datetime import datetime

import requests
from django.conf import settings
from django.utils import timezone

from tickets.models import CompteEmail, Ticket, SuiviTicket
from tickets.email_pipeline import analyze_email_with_ai, find_magasin, create_remontee_from_email

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
# Polling des nouveaux mails (Graph delta)
# ─────────────────────────────────────────────

MSG_SELECT = "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,conversationId,hasAttachments,internetMessageId,internetMessageHeaders"


def fetch_new_messages(compte):
    """
    Récupère les nouveaux messages via l'API delta Graph.
    Utilise compte.delta_link comme curseur (comme IAHUB1 emailPoller.js).
    """
    token = get_access_token(compte)
    start_url = (
        compte.delta_link
        or f"https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$select={MSG_SELECT}"
    )

    messages = []
    url = start_url
    next_delta = compte.delta_link

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
                messages.append(item)

        if "@odata.nextLink" in page:
            url = page["@odata.nextLink"]
        else:
            next_delta = page.get("@odata.deltaLink", next_delta)
            url = None

    # Sauvegarder le curseur delta
    compte.delta_link = next_delta or ""
    compte.last_sync = timezone.now()
    compte.save(update_fields=["delta_link", "last_sync"])

    return messages


# ─────────────────────────────────────────────
# Correspondance mail → ticket existant
# ─────────────────────────────────────────────

def find_existing_ticket(conversation_id, in_reply_to=None):
    """
    Cherche un ticket existant par conversation_id Outlook.
    Logique portée de IAHUB1 conversationMatcher.js.
    """
    if conversation_id:
        ticket = Ticket.objects.filter(
            outlook_conversation_id=conversation_id,
            statut__in=[Ticket.STATUT_NOUVEAU, Ticket.STATUT_EN_COURS, Ticket.STATUT_EN_ATTENTE],
        ).order_by("-date_mise_a_jour").first()
        if ticket:
            return ticket

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

def extract_body_text(message):
    """Extrait le texte brut depuis un message Graph."""
    content = message.get("body", {}).get("content", "")
    content_type = message.get("body", {}).get("contentType", "text")

    if content_type.lower() == "html":
        # Retirer les balises HTML basiquement
        text = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        return text.strip()[:3000]

    return (content or "")[:3000]


# ─────────────────────────────────────────────
# Traitement d'un message entrant
# ─────────────────────────────────────────────

def process_message(message, compte):
    """
    Traite un mail Graph :
    - S'il appartient à un fil existant → suivi + détection résolution
    - Sinon → analyse IA + création remontée
    """
    msg_id = message.get("id", "")
    conversation_id = message.get("conversationId", "")
    subject = message.get("subject", "(sans objet)")
    from_addr = message.get("from", {}).get("emailAddress", {})
    from_email = from_addr.get("address", "")
    from_name = from_addr.get("name", "")
    body_text = extract_body_text(message)
    received_at = message.get("receivedDateTime", "")

    # ── Cas 1 : réponse dans un fil existant ──
    existing = find_existing_ticket(conversation_id)
    if existing:
        # Détecter si c'est une résolution
        if is_resolution_reply(body_text):
            existing.set_statut(Ticket.STATUT_RESOLU, utilisateur=from_email)
            SuiviTicket.objects.create(
                ticket=existing,
                auteur=from_name or from_email,
                message=f"[Email] Résolution détectée automatiquement.\n\n{body_text[:1000]}",
            )
            logger.info(f"[Outlook] Ticket #{existing.numero_ticket} marqué RÉSOLU par réponse mail")
        else:
            # Nouveau message dans le fil → suivi
            SuiviTicket.objects.create(
                ticket=existing,
                auteur=from_name or from_email,
                message=f"[Email entrant]\n\n{body_text[:2000]}",
            )
            # Mettre le ticket en cours si nouveau
            if existing.statut == Ticket.STATUT_NOUVEAU:
                existing.set_statut(Ticket.STATUT_EN_COURS, utilisateur="Système")

        # Mettre à jour le dernier message_id pour pouvoir répondre dans le fil
        Ticket.objects.filter(pk=existing.pk).update(outlook_message_id=msg_id)
        return {"action": "followup", "ticket": existing.numero_ticket}

    # ── Cas 2 : nouveau mail → création remontée ──
    ai_result = analyze_email_with_ai(from_name, from_email, subject, body_text)
    magasin = find_magasin(ai_result)
    ticket = create_remontee_from_email(from_name, from_email, subject, body_text, ai_result, magasin)

    # Stocker les IDs Outlook pour le suivi du fil
    Ticket.objects.filter(pk=ticket.pk).update(
        outlook_conversation_id=conversation_id,
        outlook_message_id=msg_id,
        source_email=from_email,
        cree_par_email=True,
        magasin_non_identifie=(magasin is None or not ai_result.get("magasin_code")),
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
